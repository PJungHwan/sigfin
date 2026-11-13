"""실행별 역할 어댑터. SDK와 세션 원장은 STEP 9 경로를 공유한다. 네트워크 중에는 원장 lock을 놓는다."""

import hashlib
import json

from pydantic import ValidationError

from agents.research import evidence_for
from config.llm_settings import AI_MAX_INPUT_BYTES, SUPPORTED_OPENAI_MODELS
from graph.state import AgentExecution, TechnicalResult, MacroResult
from llm.agent_contracts import (TechnicalLLMOutput, MacroLLMOutput, TECH_PROMPT, MACRO_PROMPT,
                                SUMMARIES, agent_payload, technical_catalog, macro_catalog,
                                validate_technical_output, validate_macro_output, token_reservation)
from llm.budget import estimate_cost, price_for
from llm.client import request_structured
from llm.config import AIError
from services.ai_inputs import summarize_macro_frames, snapshot_fingerprint


def validate_llm_access(config, options, ledger, *, requested, cost_approved):
    if requested is not True:
        raise AIError("Multi-Agent 분석 실행 버튼 입력이 필요합니다.", "NOT_REQUESTED")
    if cost_approved is not True:
        raise AIError("LLM Workflow의 유료 요청 비용 승인이 필요합니다.", "COST_APPROVAL_REQUIRED")
    if config is None or not config.api_key:
        raise AIError("OPENAI_API_KEY가 없습니다. Mock Workflow를 사용하거나 본인의 키를 설정하세요.", "KEY_MISSING")
    if config.model not in SUPPORTED_OPENAI_MODELS:
        raise AIError("Structured Outputs 지원이 확인되지 않은 모델입니다.", "MODEL_UNVERIFIED")
    if price_for(config.model) is None:
        raise AIError("가격 미확인 모델의 유료 요청을 차단했습니다.", "PRICE_UNKNOWN")
    if options is None or options.mode != "live" or options.retries != 0 or ledger is None:
        raise AIError("LLM Workflow는 공유 세션 원장과 자동 재시도 0회 설정이 필요합니다.", "CONFIGURATION")


class WorkflowLLMSession:
    def __init__(self, config, options, ledger):
        self.config, self.options, self.ledger = config, options, ledger
        self.records = {}
        self.prepared = False

    def prepare(self, snapshot, frames, now, run_id):
        # 요청할 분기만 사전 계산한다. 잘못된 Macro 입력은 Macro 노드가 독립적으로 보고한다.
        plans = {}
        if snapshot.sufficient:
            plans["technical_agent"] = (TECH_PROMPT, agent_payload("technical_agent", snapshot), TechnicalLLMOutput)
        try:
            facts, metadata = summarize_macro_frames(frames, as_of=snapshot.data_as_of_utc, now=now, include_quality=True)
        except AIError:
            facts, metadata = [], []
        if facts:
            plans["macro_agent"] = (MACRO_PROMPT, agent_payload("macro_agent", facts=facts, metadata=metadata), MacroLLMOutput)
        for prompt, payload, schema in plans.values():
            if len(payload.encode()) > AI_MAX_INPUT_BYTES:
                raise AIError("역할별 입력 크기 제한을 초과했습니다.", "INPUT_VALIDATION")
        fingerprint = hashlib.sha256(json.dumps(["step11", self.config.model, self.config.scope,
            snapshot_fingerprint(snapshot), [(role, plan[1]) for role, plan in plans.items()]], ensure_ascii=False).encode()).hexdigest()
        estimates = [estimate_cost(self.config.model, token_reservation(*plan), self.options.max_output_tokens) for plan in plans.values()]
        records = self.ledger.reserve_many(self.options, self.config.model, estimates, now, fingerprint)
        for role, record in zip(plans, records):
            record.update(node=role, run_id=run_id)
            self.records[role] = record
        self.plans = plans
        self.prepared = True

    def execute(self, role, validate):
        if not self.prepared or role not in self.records:
            raise AIError("승인된 역할별 비용 예약이 없습니다.", "CONFIGURATION")
        record = self.records[role]
        with self.ledger.lock:
            if record["status"] != "RESERVED":
                raise AIError("동일 노드의 중복 요청을 차단했습니다.", "DUPLICATE_REQUEST")
            record["status"] = "ATTEMPTED"
        prompt, payload, schema = self.plans[role]
        usage = None
        try:
            response = request_structured(prompt, payload, schema, self.config, self.options.max_output_tokens)
            usage = response.usage
            with self.ledger.lock:
                record["response_model"] = response.model
            self.ledger.finish(record, status="RESPONSE_RECEIVED", usage=usage)
            result = validate(response.report)
        except Exception as error:
            category = error.category if isinstance(error, AIError) else "RESPONSE_VALIDATION" if isinstance(error, ValidationError) else "NODE_ERROR"
            self.ledger.finish(record, status=category, usage=getattr(error, "usage", None) or usage)
            raise AIError("에이전트 API 호출 또는 결과 검증에 실패했습니다. 오류 유형과 사용량 기록을 확인하세요.", category) from None
        with self.ledger.lock:
            record["status"] = "VALIDATED"
        return result

    def technical(self, snapshot):
        if not snapshot.sufficient:
            catalog = technical_catalog(snapshot)
            return TechnicalResult(technical_signal="INSUFFICIENT_DATA", technical_summary=SUMMARIES["INSUFFICIENT_DATA"],
                bullish_factors=[fact.explanation for fact in snapshot.facts if fact.source_type == "TECHNICAL" and fact.role == "bullish"],
                bearish_factors=[fact.explanation for fact in snapshot.facts if fact.source_type == "TECHNICAL" and fact.role == "bearish"],
                evidence=[evidence_for(fact) for fact in snapshot.facts if fact.source_type == "TECHNICAL"],
                risk_factors=catalog["risk_factors"], limitations=catalog["limitations"])
        result = self.execute("technical_agent", lambda value: validate_technical_output(value, snapshot))
        return TechnicalResult(technical_signal=result.signal, technical_summary=result.technical_summary,
            bullish_factors=result.bullish_factors, bearish_factors=result.bearish_factors, evidence=result.evidence,
            risk_factors=result.risk_factors, limitations=result.limitations)

    def macro(self, facts, metadata):
        if not facts:
            return MacroResult(macro_evidence=[], **macro_catalog(facts))
        result = self.execute("macro_agent", lambda value: validate_macro_output(value, facts, metadata))
        return MacroResult(**result.model_dump(exclude={"macro_evidence_ids"}))

    def execution(self, role, error=None):
        record = self.records.get(role)
        called = record is not None and record["status"] not in ("RESERVED", "CANCELLED_NOT_SENT")
        usage = None if record is None or record["usage"] is None else record["usage"]
        return AgentExecution(execution_mode="LLM Workflow", model=record.get("response_model", self.config.model) if called else None,
            api_called=called, validation_status="FAILED" if error else "VALIDATED" if called else "LOCAL_DATA_CHECK",
            usage=usage, estimated_cost_usd=(None if usage is None and called else 0. if not called else
                estimate_cost(self.config.model, usage["input_tokens"], usage["output_tokens"])),
            reserved_cost_usd=record["reserved_or_estimated_usd"] if called else 0., error_type=error)

    def close(self):
        self.ledger.cancel_unattempted(list(self.records.values()))
