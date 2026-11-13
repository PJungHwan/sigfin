"""명시적 저장용 허용 목록. 키/원본 예외/승인/세션 예산을 저장하지 않는다."""

import json
import re
from datetime import date, datetime
from hashlib import sha256

import pandas as pd
from pydantic import Field

from config.settings import OHLCV_COLUMNS,APP_VERSION
from indicators.technical import INDICATOR_COLUMNS
from llm.schemas import StrictModel
from portfolio.models import utc

SETTINGS = {"period", "strategy_json", "initial_capital", "fee_rate", "slippage_rate", "mode", "debate_enabled", "risk_enabled", "backtest_config", "backtest_risk", "risk_agent_settings"}
ATTRS = {"symbol", "timeframe", "period", "fetched_at", "actual_start", "actual_end", "is_partial", "missing_candles",
         "version_policy", "is_point_in_time", "units", "frequency", "series_id", "seasonal_adjustment",
         "missing_values", "removed_duplicates", "source_last_updated", "release_dates_available", "observation_window_policy"}
MACRO_COLUMNS = {"series_id", "observation_date", "value", "units", "frequency", "release_date", "available_at",
                 "vintage_date", "realtime_start", "realtime_end", "fetched_at", "yoy_pct"}
STATE_KEYS = {"run_id", "symbol", "timeframe", "execution_mode", "data_as_of_utc", "input_fingerprint", "execution_status",
              "technical_result", "macro_result", "aggregation_result", "final_report", "technical_execution", "macro_execution",
              "node_execution_logs", "errors", "debate_evaluation", "debate_history", "debate_status", "risk_result", "research_action"}
STATE_KEYS.update({'debate_enabled','debate_evidence','evidence_fingerprint','bull_result','bear_result','debate_rounds','debate_round',
                   'risk_manager_enabled','risk_input','risk_manager_status','pre_risk_signal'})


def safe_json(value):
    def encode(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, (pd.Timestamp, datetime, date)):
            return item.isoformat()
        raise TypeError("Unsupported snapshot value")
    text=json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(text.encode())>8_000_000 or re.search(r"sk-[A-Za-z0-9_-]{8,}|(?i:api[_ -]?key|authorization|bearer\s|password|client_secret)",text):
        raise ValueError("저장 자료의 크기/민감정보 검증에 실패했습니다.")
    return text


class TableSnapshot(StrictModel):
    table: dict
    attrs: dict


class ResearchSnapshot(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    app_version: str = '기록 확인 불가'
    calculation_version: str = "research-snapshot-v1"
    record_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    captured_at_utc: str
    requested_at_utc: str | None = None
    symbol: str
    timeframe: str
    settings: dict
    market: TableSnapshot
    macro: dict[str, TableSnapshot]
    macro_order: list[str] = Field(default_factory=list)
    results: dict
    input_digest: str
    integrated: dict | None = None


def pack_frame(frame, *, macro=False):
    allowed=MACRO_COLUMNS if macro else set(OHLCV_COLUMNS+INDICATOR_COLUMNS)
    if not isinstance(frame,pd.DataFrame) or frame.empty or len(frame)>5300 or not set(frame.columns).issubset(allowed):
        raise ValueError("스냅샷의 허용 데이터 구조/크기를 확인하세요.")
    table=json.loads(frame.to_json(orient="table",date_format="iso",date_unit="ns"))
    # to_json의 기본 10자리 반올림으로 지표 재계산이 달라지지 않게
    # IEEE-754 값은 Python JSON 왕복 표현으로 기록한다.
    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            for row,value in zip(table['data'],frame[column]):
                row[column]=None if pd.isna(value) else value.item() if hasattr(value,'item') else value
    # pandas 3의 us/ns 정밀도를 명시적으로 보존한다(값/타임존뿐 아니라 dtype).
    table['schema']['sigfin_datetime_dtypes']={str(c):str(frame[c].dtype) for c in frame.columns if isinstance(frame[c].dtype,pd.DatetimeTZDtype)}
    attrs={key:value for key,value in frame.attrs.items() if key in ATTRS}
    return TableSnapshot(table=table,attrs=json.loads(safe_json(attrs)))


def restore_frame(saved):
    from io import StringIO
    saved=TableSnapshot.model_validate(saved)
    data=pd.read_json(StringIO(safe_json(saved.table)),orient="table",precise_float=True)
    for column,dtype in saved.table['schema'].get('sigfin_datetime_dtypes',{}).items():
        data[column]=data[column].astype(dtype)
    data.attrs=dict(saved.attrs)
    for key in ("fetched_at", "actual_start", "actual_end"):
        if key in data.attrs:
            data.attrs[key]=pd.Timestamp(data.attrs[key])
    return data


def snapshot_digest(market,macro,settings,macro_order=None):
    return sha256(safe_json({"market":market,"macro":macro,"settings":settings,"macro_order":list(macro) if macro_order is None else macro_order}).encode()).hexdigest()


def restore_macro_frames(snapshot):
    order=snapshot.macro_order or list(snapshot.macro)
    if len(order)!=len(set(order)) or set(order)!=set(snapshot.macro):
        raise ValueError("Macro 원본 순서와 저장 자료가 일치하지 않습니다.")
    return {key:restore_frame(snapshot.macro[key]) for key in order}


def validate_snapshot(value):
    value=ResearchSnapshot.model_validate(value)
    utc(value.captured_at_utc)
    restore_macro_frames(value)
    if not set(value.settings).issubset(SETTINGS) or not set(value.results).issubset(STATE_KEYS):
        raise ValueError("허용되지 않은 분석 설정/결과 필드입니다.")
    if snapshot_digest(value.market,value.macro,value.settings,value.macro_order or list(value.macro))!=value.input_digest:
        raise ValueError("스냅샷 입력 해시가 일치하지 않습니다.")
    if (value.results.get('run_id')!=value.record_id or value.results.get('symbol')!=value.symbol
            or value.results.get('timeframe')!=value.timeframe):
        raise ValueError("스냅샷 분석 대상/실행 ID가 일치하지 않습니다.")
    for table,macro in [(value.market,False),*[(item,True) for item in value.macro.values()]]:
        frame=restore_frame(table)
        pack_frame(frame,macro=macro)
    # 결과 형식의 추가 필드/비유한 수치도 복원 시 다시 검사한다.
    report=value.results.get('final_report')
    if report is not None:
        from graph.state import WorkflowReport
        from graph.debate_state import DebateReport
        from graph.risk_state import RiskWorkflowReport,RiskDebateReport
        schema=(RiskDebateReport if 'debate_evaluation' in report else RiskWorkflowReport) if 'risk_report' in report else (DebateReport if 'debate_evaluation' in report else WorkflowReport)
        schema.model_validate_json(safe_json(report))
    safe_json(value)
    if value.integrated is not None:
        from services.research_service import validate_integrated_snapshot
        validate_integrated_snapshot(value)
    return value


def capture_workflow(execution,data,*,macro_frames=None,settings=None,requested_at=None):
    state=execution.state
    market=pack_frame(data)
    macro={key:pack_frame(frame,macro=True) for key,frame in (macro_frames or {}).items()}
    results=json.loads(safe_json({key:state[key] for key in STATE_KEYS if key in state}))
    settings=settings or {}
    report=state.get('final_report')
    requested_at=requested_at or (report.analyzed_at_utc if report is not None else execution.completed_at_utc)
    snapshot=ResearchSnapshot(app_version=APP_VERSION,record_id=state['run_id'],captured_at_utc=utc(execution.completed_at_utc),requested_at_utc=utc(requested_at),symbol=state['symbol'],
        timeframe=state['timeframe'],settings=settings,market=market,macro=macro,macro_order=list(macro),results=results,
        input_digest=snapshot_digest(market,macro,settings))
    return validate_snapshot(snapshot)
