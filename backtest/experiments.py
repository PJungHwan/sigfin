"""고정 OHLCV·전략·비용으로 독립 실행하는 STEP 18 실험. API/DB/UI 접근 없음."""

from dataclasses import asdict, dataclass, fields
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
from pydantic import Field

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, BacktestError, BacktestMetrics, BacktestResult, RiskConfig
from config.settings import APP_VERSION, OHLCV_COLUMNS, SUPPORTED_SYMBOLS, TIMEFRAME_MILLISECONDS
from indicators.technical import add_technical_indicators
from llm.schemas import StrictModel
from services.research_snapshots import TableSnapshot, pack_frame, restore_frame, safe_json
from strategies.evaluator import StrategyEvaluationError
from strategies.models import ConditionGroup, Direction, Logic, Strategy
from strategies.storage import strategy_from_json, strategy_to_json

CALCULATION_VERSION = "backtest-experiment-v1"
MAX_STRATEGIES = 6
CODE_FILES = (
    "backtest/engine.py", "backtest/models.py", "backtest/metrics.py", "backtest/risk.py",
    "backtest/experiments.py", "indicators/technical.py", "strategies/evaluator.py",
    "strategies/models.py", "strategies/storage.py", "config/settings.py",
    "services/research_snapshots.py",
)


class ExperimentError(ValueError):
    """지원 불가/손상된 실험 명세. 실패를 무거래 성과로 바꾸지 않는다."""


class ExperimentSnapshot(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    calculation_version: str = CALCULATION_VERSION
    app_version: str
    code_digest: str
    symbol: str
    timeframe: str
    period: str
    as_of_utc: str
    market: TableSnapshot
    strategies: list[str] = Field(min_length=2, max_length=MAX_STRATEGIES)
    settings: dict
    risk: dict
    split_at: int | None
    source_quality: dict
    input_digest: str


@dataclass(frozen=True)
class ExperimentRun:
    segment: str
    strategy_name: str
    risk_mode: str
    result: BacktestResult | None
    error: str | None = None


@dataclass(frozen=True)
class ExperimentResult:
    snapshot: ExperimentSnapshot
    runs: tuple[ExperimentRun, ...]
    segments: pd.DataFrame
    warnings: tuple[str, ...]

    @property
    def status(self):
        success = sum(item.result is not None for item in self.runs)
        return "COMPLETED" if success == len(self.runs) else "PARTIAL" if success else "FAILED"


def code_digest():
    """계산에 영향을 주는 소스의 LF 정규화 SHA-256. Git/네트워크를 호출하지 않는다."""
    root = Path(__file__).resolve().parents[1]
    return sha256(safe_json({name: sha256((root / name).read_text(encoding="utf-8").encode()).hexdigest()
                            for name in CODE_FILES}).encode()).hexdigest()


def _digest(payload):
    return sha256(safe_json({k: v for k, v in payload.items() if k != "input_digest"}).encode()).hexdigest()


def _utc(value):
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError
        return stamp.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError) as error:
        raise ExperimentError("실험 시각은 시간대가 지정된 유효한 UTC 시각이어야 합니다.") from error


def _configs(saved):
    if set(saved.settings) != set(asdict(BacktestConfig())) or set(saved.risk) != set(asdict(RiskConfig())):
        raise ExperimentError("비용/리스크 설정 필드가 올바르지 않습니다.")
    return BacktestConfig(**saved.settings), RiskConfig(**saved.risk)


def _empty_strategy():
    return Strategy("입력 검증", ConditionGroup(Direction.LONG, Logic.AND, ()),
                    ConditionGroup(Direction.SHORT, Logic.AND, ()))


def validate_experiment(value):
    saved = ExperimentSnapshot.model_validate(value)
    if saved.calculation_version != CALCULATION_VERSION or saved.code_digest != code_digest():
        raise ExperimentError("계산 코드/버전이 저장 시점과 다릅니다. 같은 버전에서 재현하거나 새 실험을 생성하세요.")
    if _digest(saved.model_dump(mode="json")) != saved.input_digest:
        raise ExperimentError("실험 입력 해시가 일치하지 않습니다.")
    if saved.symbol not in SUPPORTED_SYMBOLS or saved.timeframe not in TIMEFRAME_MILLISECONDS:
        raise ExperimentError("지원 종목/Timeframe을 확인하세요.")
    cutoff = _utc(saved.as_of_utc)
    if cutoff > pd.Timestamp.now(tz="UTC"):
        raise ExperimentError("실행 기준 시각을 미래로 지정할 수 없습니다.")
    settings, _ = _configs(saved)
    strategies = [strategy_from_json(item) for item in saved.strategies]
    if len({item.name for item in strategies}) != len(strategies):
        raise ExperimentError("비교 전략의 이름은 서로 달라야 합니다.")
    frame = restore_frame(saved.market)
    if list(frame.columns) != OHLCV_COLUMNS or len(frame) < 2 or len(frame) > 5000:
        raise ExperimentError("실험에는 2~5,000개의 원본 OHLCV 확정 봉이 필요합니다.")
    if frame.attrs.get("symbol") != saved.symbol or frame.attrs.get("timeframe") != saved.timeframe:
        raise ExperimentError("시장 데이터와 실험의 종목/Timeframe이 다릅니다.")
    # 기존 엔진의 OHLC/확정 시각/정렬/중복 검증을 그대로 사용한다.
    run_backtest(frame, _empty_strategy(), settings, timeframe=saved.timeframe, as_of=cutoff)
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[saved.timeframe])
    if any(stamp.value % interval.value for stamp in frame["timestamp"]):
        raise ExperimentError("봉 시작 시각이 Timeframe 경계와 일치하지 않습니다.")
    fetched = frame.attrs.get("fetched_at")
    if fetched is not None and (frame["timestamp"].iloc[-1] + interval > _utc(fetched) or _utc(fetched) > cutoff):
        raise ExperimentError("수집 시각·마지막 봉 마감·실행 기준 시각이 일치하지 않습니다.")
    if saved.split_at is not None and not 2 <= saved.split_at <= len(frame) - 2:
        raise ExperimentError("시간순 검토/검증 구간은 각각 최소 2봉이 필요합니다.")
    pack_frame(frame)
    return saved


def capture_experiment(data, strategies, settings, risk, *, symbol, timeframe, period,
                       split_at=None, as_of=None):
    """현재 조회 구간을 고정. 외부 계산 지표는 버리고 이 원본 OHLCV로 다시 계산한다."""
    if not isinstance(data, pd.DataFrame) or not set(OHLCV_COLUMNS).issubset(data.columns):
        raise ExperimentError("시장 OHLCV 데이터가 필요합니다.")
    raw = data[OHLCV_COLUMNS].copy(deep=True).reset_index(drop=True)
    if raw.attrs.get("symbol") != symbol or raw.attrs.get("timeframe") != timeframe:
        raise ExperimentError("현재 조회 데이터의 종목/Timeframe을 확인하세요.")
    # 수집 제한 사유도 보존하되 비밀정보/전체 attrs를 저장하지 않는다.
    quality = {k: data.attrs[k] for k in ("requested_start", "requested_end", "is_partial",
               "missing_candles", "limit_reasons") if k in data.attrs}
    payload = dict(app_version=APP_VERSION, code_digest=code_digest(), symbol=symbol,
                   timeframe=timeframe, period=period,
                   as_of_utc=_utc(as_of if as_of is not None else pd.Timestamp.now(tz="UTC")).isoformat(),
                   market=pack_frame(raw), strategies=[strategy_to_json(s) for s in strategies],
                   settings=asdict(settings), risk=asdict(risk), split_at=split_at,
                   source_quality=json.loads(safe_json(quality)),
                   calculation_version=CALCULATION_VERSION, schema_version=1)
    payload = json.loads(safe_json(payload))
    payload["input_digest"] = _digest(payload)
    return validate_experiment(payload)


def execute_experiment(value):
    """전체·검토·검증별 독립 FLAT 시작. 앞 구간 주문/포지션을 넘기지 않는다.

    지표는 저장 OHLCV의 과거 접두 구간만 사용하는 STEP 3 함수로 재계산한다.
    검증 구간 첫 봉의 Cross는 이전 행이 없어 False이고, 첫 진입은 두 번째 봉부터다.
    초기 지표 NaN은 그대로 유지하며 Macro/현재 가상 원장을 입력으로 받지 않는다.
    """
    saved = validate_experiment(value)
    settings, risk = _configs(saved)
    raw = restore_frame(saved.market)
    calculated = add_technical_indicators(raw, timeframe=saved.timeframe)
    bounds = [("전체", 0, len(raw))]
    if saved.split_at is not None:
        bounds.extend([("검토", 0, saved.split_at), ("검증", saved.split_at, len(raw))])
    rows, runs, warnings = [], [], [
        "과거 성과는 미래 성과를 보장하지 않습니다. Risk ON의 성과 우위를 가정하지 않습니다.",
        "지표는 저장 구간의 OHLCV로 재계산합니다. 구간 이전 외부 워밍업은 없으며 초기 NaN을 유지합니다.",
        "전략은 실행 전에 고정됩니다. 검증 결과를 본 뒤 수정한 전략을 독립 검증으로 해석하지 마세요.",
        "SHORT는 1배 가상 노출입니다. OHLCV/고정 비용/직전 봉 거래량은 실제 호가·체결을 보장하지 않습니다.",
    ]
    if saved.source_quality.get("is_partial"):
        warnings.append("수집 제한으로 요청 기간의 일부만 사용했습니다: " + str(saved.source_quality.get("limit_reasons", [])))
    if not risk.enabled:
        warnings.append("Risk ON 설정이 모두 비활성화되어 Risk OFF와 동일합니다.")
    bodies = [json.loads(text) for text in saved.strategies]
    signatures = [safe_json({key: value for key, value in body.items() if key != "name"}) for body in bodies]
    if len(set(signatures)) < len(signatures):
        warnings.append("이름만 다르고 같은 조건인 전략이 있습니다. 다른 전략의 독립적인 증거로 해석하지 마세요.")
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[saved.timeframe])
    gaps = int(raw["timestamp"].diff().iloc[1:].ne(interval).sum())
    if gaps:
        warnings.append(f"시간 간격 누락 {gaps}곳: 지표는 재초기화되고 누락 다음 봉으로 주문을 넘기지 않습니다.")
    for label, start, stop in bounds:
        segment = calculated.iloc[start:stop].copy().reset_index(drop=True)
        rows.append(dict(segment=label, start_utc=segment["timestamp"].iloc[0].isoformat(),
                         end_utc=(segment["timestamp"].iloc[-1] + interval).isoformat(), candles=len(segment),
                         prior_warmup_candles=start, initial_position="FLAT"))
        for strategy_json in saved.strategies:
            strategy = strategy_from_json(strategy_json)
            for mode, selected_risk in (("OFF", RiskConfig()), ("ON", risk)):
                try:
                    result = run_backtest(segment, strategy, settings, timeframe=saved.timeframe,
                                          as_of=_utc(saved.as_of_utc), risk=selected_risk)
                    runs.append(ExperimentRun(label, strategy.name, mode, result))
                except (BacktestError, StrategyEvaluationError) as error:
                    runs.append(ExperimentRun(label, strategy.name, mode, None, f"{type(error).__name__}: {error}"))
    return ExperimentResult(saved, tuple(runs), pd.DataFrame(rows), tuple(warnings))


def comparison_table(experiment):
    rows = []
    for run in experiment.runs:
        row = dict(segment=run.segment, strategy=run.strategy_name, risk=run.risk_mode,
                   status="COMPLETED" if run.result is not None else "FAILED", error=run.error)
        row.update({field.name: None for field in fields(BacktestMetrics)})
        if run.result is not None:
            row.update(asdict(run.result.metrics))
            validity = run.result.signals.validity
            row["valid_condition_bars"] = int(validity.all(axis=1).sum()) if len(validity.columns) else 0
            row["total_candles"] = len(run.result.signals.long)
            row["data_status"] = "AVAILABLE" if row["valid_condition_bars"] else "INSUFFICIENT_DATA"
        rows.append(row)
    return pd.DataFrame(rows)


def risk_delta_table(experiment):
    """ON − OFF. 수익률/승률/MDD는 %p 차이이며 개선의 방향을 자동 판단하지 않는다."""
    table = comparison_table(experiment)
    metrics = ("cumulative_return_pct", "max_drawdown_pct", "win_rate_pct", "final_equity",
               "closed_trades", "average_trade_pnl", "total_fees")
    rows = []
    for (segment, strategy), group in table.groupby(["segment", "strategy"], sort=False):
        off, on = group.iloc[0], group.iloc[1]
        row = dict(segment=segment, strategy=strategy)
        for metric in metrics:
            row[metric] = on[metric] - off[metric] if pd.notna(on[metric]) and pd.notna(off[metric]) else None
        rows.append(row)
    return pd.DataFrame(rows)


def result_digest(experiment):
    """실행 시간/ID 없는 정규화 결과 지문. 로그·거래·지표·자산 곡선까지 비교한다."""
    rows = []
    for run in experiment.runs:
        row = dict(segment=run.segment, strategy=run.strategy_name, risk=run.risk_mode, error=run.error)
        if run.result is not None:
            result = run.result
            row.update(metrics=asdict(result.metrics), position=result.final_position.value)
            for key in ("equity_curve", "trades", "events"):
                row[key] = json.loads(getattr(result, key).to_json(orient="split", date_format="iso", date_unit="ns", double_precision=15))
        rows.append(row)
    rows.sort(key=lambda row: (row["segment"], row["strategy"], row["risk"]))
    return sha256(safe_json(rows).encode()).hexdigest()


def experiment_json(value):
    return safe_json(validate_experiment(value))


def experiment_from_json(text):
    if not isinstance(text, (str, bytes)) or len(text) > 8_000_000:
        raise ExperimentError("실험 JSON은 최대 8MB입니다.")
    def pairs(items):
        out = {}
        for key, val in items:
            if key in out:
                raise ExperimentError("중복 JSON 필드는 허용하지 않습니다.")
            out[key] = val
        return out
    def reject_constant(_):
        raise ExperimentError("비유한 JSON 값입니다.")
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        safe_json(value)
        return validate_experiment(value)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ExperimentError("실험 JSON/해시/코드 버전/데이터 검증에 실패했습니다.") from error


def experiment_markdown(experiment):
    lines = ["# SIGFIN 백테스트 비교 실험", f"상태: {experiment.status}",
             f"대상: {experiment.snapshot.symbol} / {experiment.snapshot.timeframe}",
             f"실행 기준 UTC: {experiment.snapshot.as_of_utc}",
             f"입력 SHA-256: {experiment.snapshot.input_digest}",
             f"계산 버전: {CALCULATION_VERSION}", f"결과 SHA-256: {result_digest(experiment)}",
             "## 구간 (각각 동일 초기금 / FLAT)", experiment.segments.to_csv(index=False),
             "## 성과 (승률/평균 거래 손익의 빈 값은 종료 거래 없음 또는 실행 실패 N/A)",
             comparison_table(experiment).to_csv(index=False), "## Risk ON − OFF (비율 차이는 %p)",
             risk_delta_table(experiment).to_csv(index=False), "## 비용/리스크 가정",
             safe_json(dict(settings=experiment.snapshot.settings, risk=experiment.snapshot.risk)),
             "## 연구 한계", *experiment.warnings]
    return "\n\n".join(lines)
