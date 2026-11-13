"""공식 SDK Responses.parse. 호출은 서비스의 명시적 승인/예산 검사 뒤에만 수행한다."""

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import re

import httpx
import openai
from pydantic import ValidationError
from pydantic import BaseModel

from config.llm_settings import AI_TIMEOUT_SECONDS, SUPPORTED_OPENAI_MODELS
from llm.config import AIError
from llm.prompts import SYSTEM_PROMPT, request_payload
from llm.schemas import AnalysisReport, TokenUsage


class RedactCredentials(logging.Filter):
    def __init__(self, key):
        super().__init__()
        self.key = key

    def filter(self, record):
        text = record.getMessage()
        if self.key:
            text = text.replace(self.key, "[REDACTED]")
        text = re.sub(r"(?i)(authorization[\s\"':=]+(?:bearer\s+)?)[^\s,}\]]+", r"\1[REDACTED]", text)
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
        record.msg, record.args = text, ()
        return True


@contextmanager
def credential_log_filter(key):
    redactor = RedactCredentials(key)
    targets = [logging.getLogger(name) for name in list(logging.Logger.manager.loggerDict)
               if name.startswith(("openai", "httpx", "httpcore"))]
    targets += list(logging.getLogger().handlers)
    for target in targets:
        target.addFilter(redactor)
    try:
        yield
    finally:
        for target in targets:
            target.removeFilter(redactor)


@dataclass(frozen=True)
class ClientResult:
    report: BaseModel
    usage: TokenUsage | None
    model: str


def _usage(response):
    raw = getattr(response, "usage", None)
    if raw is None:
        return None
    try:
        usage = TokenUsage(input_tokens=raw.input_tokens, output_tokens=raw.output_tokens, total_tokens=raw.total_tokens)
    except (ValidationError, AttributeError):
        return None
    return usage if usage.total_tokens == usage.input_tokens+usage.output_tokens else None


def request_analysis(snapshot, analyzed_at, config, max_output_tokens):
    return request_structured(SYSTEM_PROMPT, request_payload(snapshot, analyzed_at), AnalysisReport,
                              config, max_output_tokens)


def request_structured(instructions, payload_text, output_schema, config, max_output_tokens):
    """단일/역할별 분석이 공유하는 유일한 SDK 전송 경로. 예산 예약은 호출자가 담당한다."""
    if not config.api_key:
        raise AIError("OPENAI_API_KEY가 없습니다. 프로젝트 .env에 본인의 키를 설정하거나 Mock Mode를 사용하세요.", "KEY_MISSING")
    if config.model not in SUPPORTED_OPENAI_MODELS:
        raise AIError("Structured Outputs 지원을 확인하지 못한 OPENAI_MODEL입니다. 공식 모델 설정을 확인하세요.", "MODEL_UNVERIFIED")
    usage = None
    try:
        with credential_log_filter(config.api_key), openai.OpenAI(
            api_key=config.api_key, base_url="https://api.openai.com/v1", max_retries=0,
            timeout=httpx.Timeout(AI_TIMEOUT_SECONDS, connect=5),
        ) as client:
            raw = client.responses.with_raw_response.parse(
                model=config.model, instructions=instructions,
                input=[{"role": "user", "content": payload_text}],
                text_format=output_schema, max_output_tokens=max_output_tokens,
                reasoning={"effort": "none"}, store=False,
            )
            # 출력 JSON 파싱 실패 때도 API가 반환한 사용량은 보존한다. 원문은 저장/출력하지 않는다.
            payload = raw.http_response.json()
            values = payload.get("usage") if isinstance(payload, dict) else None
            if isinstance(values, dict):
                try:
                    candidate = TokenUsage(**{key: values[key] for key in ("input_tokens", "output_tokens", "total_tokens")})
                    if candidate.input_tokens+candidate.output_tokens == candidate.total_tokens:
                        usage = candidate
                except (KeyError, ValidationError):
                    pass
            if isinstance(payload, dict) and payload.get("status") != "completed":
                raise AIError("AI 응답이 완료되지 않았습니다. 출력 한도 또는 응답 상태를 확인하세요.", "INCOMPLETE_RESPONSE", usage=usage)
            response = raw.parse()
        usage = _usage(response) or usage
        if response.status != "completed":
            raise AIError("AI 응답이 완료되지 않았습니다. 출력 한도 또는 응답 상태를 확인하세요.", "INCOMPLETE_RESPONSE", usage=usage)
        if response.output_parsed is None:
            refusal = any(getattr(part, "type", None) == "refusal" for item in response.output
                          for part in getattr(item, "content", ()))
            raise AIError("모델이 응답을 거부했습니다." if refusal else "구조화된 AI 응답이 없습니다.",
                          "REFUSAL" if refusal else "RESPONSE_VALIDATION", usage=usage)
        if response.model not in SUPPORTED_OPENAI_MODELS:
            raise AIError("응답 모델이 확인된 모델 설정과 일치하지 않습니다.", "RESPONSE_VALIDATION", usage=usage)
        return ClientResult(response.output_parsed, usage, response.model)
    except openai.AuthenticationError:
        raise AIError("OpenAI 인증에 실패했습니다. 본인 API 키와 프로젝트 권한을 확인하세요.", "AUTHENTICATION") from None
    except openai.PermissionDeniedError:
        raise AIError("OpenAI 모델/프로젝트 접근 권한이 없습니다.", "PERMISSION_DENIED") from None
    except openai.RateLimitError:
        raise AIError("OpenAI 요청 한도 또는 결제 할당량을 확인하세요.", "RATE_LIMIT") from None
    except openai.APITimeoutError:
        raise AIError("OpenAI 응답 대기 시간이 초과되었습니다. 실패 요청의 과금 여부는 미확인입니다.", "TIMEOUT") from None
    except openai.APIConnectionError:
        raise AIError("OpenAI 네트워크 연결에 실패했습니다. 네트워크 권한·연결 상태를 확인하세요.", "NETWORK_ERROR") from None
    except openai.APIStatusError as error:
        category = "SERVER_ERROR" if error.status_code >= 500 else "API_ERROR"
        raise AIError("OpenAI API가 요청을 처리하지 못했습니다. 모델과 서비스 상태를 확인하세요.", category) from None
    except (ValidationError, ValueError, TypeError, AttributeError, openai.APIResponseValidationError):
        raise AIError("OpenAI 응답이 구조화 출력 검증을 통과하지 못했습니다.", "RESPONSE_VALIDATION", usage=usage) from None
