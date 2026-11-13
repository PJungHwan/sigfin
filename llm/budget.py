"""세션별 요청 원장. 실패/중단 시 과금 불명을 무료로 간주하지 않고 예약 비용을 유지한다."""

from dataclasses import dataclass, field
from datetime import date
from math import isfinite
from threading import RLock
import time

from config.llm_settings import (MODEL_PRICES, PRICE_MAX_AGE_DAYS, AI_DUPLICATE_SECONDS,
                                 AI_MAX_OUTPUT_TOKENS, AI_DEFAULT_OUTPUT_TOKENS)
from llm.config import AIError


def price_for(model, *, today=None):
    price = MODEL_PRICES.get(model)
    if price is None or not 0 <= ((today or date.today())-price["verified_on"]).days <= PRICE_MAX_AGE_DAYS:
        return None
    return price


def estimate_cost(model, input_tokens, output_tokens):
    price = price_for(model)
    return None if price is None else (input_tokens*price["input"]+output_tokens*price["output"])/1_000_000


@dataclass(frozen=True)
class AIOptions:
    mode: str = "mock"
    max_requests: int = 5
    cost_limit_usd: float = .25
    max_output_tokens: int = AI_DEFAULT_OUTPUT_TOKENS
    retries: int = 0

    def __post_init__(self):
        if (self.mode not in ("mock", "live") or type(self.max_requests) is not int or not 1 <= self.max_requests <= 100
                or type(self.cost_limit_usd) not in (int, float) or not isfinite(self.cost_limit_usd) or not 0 < self.cost_limit_usd <= 100
                or type(self.max_output_tokens) is not int or not 512 <= self.max_output_tokens <= AI_MAX_OUTPUT_TOKENS
                or type(self.retries) is not int or self.retries not in (0, 1)):
            raise AIError("AI 사용량 제한 설정이 유효하지 않습니다.", "CONFIGURATION")


@dataclass
class UsageLedger:
    records: list[dict] = field(default_factory=list)
    recent: dict[str, float] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)

    def check_duplicate(self, fingerprint, *, clock=None, remember=True):
        current = time.monotonic() if clock is None else clock
        self.recent = {key: value for key, value in self.recent.items() if current-value < AI_DUPLICATE_SECONDS}
        if fingerprint in self.recent:
            raise AIError("동일한 입력은 잠시 후 다시 실행하세요. 중복 요청을 차단했습니다.", "DUPLICATE_REQUEST")
        if remember:
            self.recent[fingerprint] = current

    @property
    def committed_usd(self):
        return sum(row["reserved_or_estimated_usd"] for row in self.records)

    def reserve(self, options, model, estimate, analyzed_at):
        with self.lock:
            return self._reserve(options, model, estimate, analyzed_at)

    @property
    def request_count(self):
        return sum(row["status"] != "CANCELLED_NOT_SENT" for row in self.records)

    def _reserve(self, options, model, estimate, analyzed_at):
        if estimate is None:
            raise AIError("모델 가격이 미확인이므로 비용 상한을 검사할 수 없어 실 API 요청을 보류합니다.", "PRICE_UNKNOWN")
        if type(estimate) not in (int, float) or not isfinite(estimate) or estimate < 0:
            raise AIError("요청 비용 예약값이 유효하지 않습니다.", "CONFIGURATION")
        if self.request_count >= options.max_requests:
            raise AIError("세션 요청 횟수 상한에 도달했습니다.", "REQUEST_LIMIT")
        if self.committed_usd+estimate > options.cost_limit_usd+1e-12:
            raise AIError("세션 추정 비용 상한을 초과하는 요청입니다.", "COST_LIMIT")
        record = {"model": model, "attempted_at_utc": analyzed_at, "status": "PENDING",
                  "reserved_or_estimated_usd": estimate, "usage": None, "billing": "미확인"}
        self.records.append(record)
        return record

    def reserve_many(self, options, model, estimates, analyzed_at, fingerprint):
        """병렬 분기의 전체 예약을 원자적으로 확보. 실패하면 어느 요청도 예약/전송하지 않는다."""
        with self.lock:
            self.check_duplicate(fingerprint, remember=False)
            if any(value is None for value in estimates):
                raise AIError("모델 가격 미확인으로 요청을 차단했습니다.", "PRICE_UNKNOWN")
            if any(type(value) not in (int, float) or not isfinite(value) or value < 0 for value in estimates):
                raise AIError("역할별 비용 예약값이 유효하지 않습니다.", "CONFIGURATION")
            if self.request_count+len(estimates) > options.max_requests:
                raise AIError("에이전트 전체 요청을 위한 세션 요청 한도가 부족합니다.", "REQUEST_LIMIT")
            if self.committed_usd+sum(estimates) > options.cost_limit_usd+1e-12:
                raise AIError("에이전트 전체 예약 비용이 세션 비용 상한을 초과합니다.", "COST_LIMIT")
            records = [self._reserve(options, model, value, analyzed_at) for value in estimates]
            self.check_duplicate(fingerprint)
            for record in records:
                record["status"] = "RESERVED"
            return records

    def cancel_unattempted(self, records):
        with self.lock:
            for record in records:
                if record["status"] == "RESERVED":
                    record.update(status="CANCELLED_NOT_SENT", reserved_or_estimated_usd=0., billing="미전송 · 예약 해제")

    def finish(self, record, *, status, usage):
        with self.lock:
            self._finish(record, status=status, usage=usage)

    def _finish(self, record, *, status, usage):
        record["status"] = status
        if usage is not None:
            record["usage"] = usage.model_dump()
            cost = estimate_cost(record["model"], usage.input_tokens, usage.output_tokens)
            # 알려진 사용량만 추정으로 대체한다. 요금 변경·세금·할인 등을 반영한 청구 확정액이 아니다.
            if cost is not None:
                record["reserved_or_estimated_usd"] = cost
            record["billing"] = "사용량 기반 추정 (청구 확정액 아님)"
