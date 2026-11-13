"""시장 데이터의 검증·계산·요약. LLM에 원시 시계열/사용자 텍스트/키를 전달하지 않는다."""

import hashlib
import json
from math import isfinite

import pandas as pd

from analytics.alignment import closed_prices, utc_time
from analytics.models import AnalysisError
from config.llm_settings import AI_PROMPT_VERSION, AI_MAX_INPUT_BYTES
from config.settings import OHLCV_COLUMNS, TIMEFRAME_MILLISECONDS, SUPPORTED_SYMBOLS
from llm.config import AIError
from llm.schemas import Fact, MarketSnapshot
from models.macro import SERIES

LIMITATIONS = [
    "단일 LLM의 제한된 자료 요약이며 다중 에이전트 분석이 아닙니다.",
    "관측일은 발표일이 아닙니다. 거시 자료는 현재 개정값이며 과거 당시 공개값이 아닙니다.",
    "거시 설명과 사후 상관관계를 매매 신호·실시간 예측 성능·과거 백테스트 입력으로 사용하지 않습니다.",
    "방향 요약은 검증된 성공 확률이 아니며 미래 가격이나 투자 수익을 보장하지 않습니다.",
]


def iso(value):
    try:
        return utc_time(value).isoformat()
    except AnalysisError:
        raise AIError("입력의 UTC 시각 정보를 확인하세요.") from None


def snapshot_fingerprint(snapshot):
    return hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()


def build_market_snapshot(data, symbol, timeframe, *, macro_frames=None, now=None):
    if symbol not in SUPPORTED_SYMBOLS or timeframe not in TIMEFRAME_MILLISECONDS:
        raise AIError("지원하지 않는 분석 종목/타임프레임입니다.")
    if not set(OHLCV_COLUMNS).issubset(data.columns):
        raise AIError("분석할 OHLCV 컬럼이 없습니다.")
    current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(iso(now))
    try:
        closed = closed_prices(data, timeframe, current)
    except AnalysisError:
        raise AIError("가격 데이터의 값·시간순서·확정 여부 검증에 실패했습니다.") from None
    if closed.empty:
        raise AIError("판단 근거 부족: 확정된 시장 데이터가 없습니다.")
    try:
        numeric = closed[OHLCV_COLUMNS[1:]].apply(pd.to_numeric, errors="raise")
        if (not numeric.map(lambda value: isfinite(value)).all().all() or
                (numeric[["open", "high", "low", "close"]] <= 0).any().any() or (numeric.volume < 0).any() or
                (numeric.high < numeric[["open", "close", "low"]].max(axis=1)).any() or
                (numeric.low > numeric[["open", "close", "high"]].min(axis=1)).any()):
            raise ValueError
    except (TypeError, ValueError):
        raise AIError("OHLCV 가격 범위·거래량 검증에 실패했습니다.") from None
    fetched = pd.Timestamp(iso(data.attrs.get("fetched_at")))
    if fetched > current:
        raise AIError("수집 시각이 분석 요청 시각보다 늦습니다.")
    latest = closed.iloc[-1]
    as_of = latest.candle_end
    if fetched < as_of:
        raise AIError("가격 수집 시각보다 늦은 확정 봉은 사용할 수 없습니다.")
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    gap_count = int((closed.timestamp.diff().dropna()/interval-1).clip(lower=0).sum())
    facts, missing = [], []
    risks = ["수수료·슬리피지·실제 체결을 고려한 투자 판단이 아닙니다.",
             "거시 자료는 발표 시각·당시 빈티지가 확인되지 않아 방향 판단 근거에서 제외합니다."]

    def add(metric, value, label, unit="", role="context", source="TECHNICAL", observed=None):
        if isinstance(value, bool) or value is None or not isfinite(float(value)):
            missing.append(metric)
            return
        value = float(value)
        facts.append(Fact(reference_id=f"{source.lower()}:{metric}", source_type=source, metric_name=metric,
                          metric_value=value, observation_time=iso(as_of if observed is None else observed),
                          explanation=f"{label}: {value:.6g}{(' '+unit) if unit else ''}.", role=role, unit=unit))

    add("close", latest.close, "마지막 확정 종가", "USDT")
    add("candle_change_pct", (latest.close/float(latest.open)-1)*100, "확정 봉 시가 대비 종가 변화", "%")
    labels = {"rsi_14": "RSI", "macd": "MACD", "macd_signal": "MACD Signal", "macd_histogram": "MACD Histogram",
              "ema_20": "EMA20", "ema_60": "EMA60", "sma_20": "SMA20", "sma_60": "SMA60",
              "bb_upper": "Bollinger Upper", "bb_middle": "Bollinger Middle", "bb_lower": "Bollinger Lower",
              "volume": "확정 봉 거래량", "volume_sma_20": "거래량 평균", "volume_ratio_20": "거래량 평균 대비 배수"}
    for metric, label in labels.items():
        value = latest.get(metric)
        if value is not None and pd.notna(value):
            try:
                if isinstance(value, bool):
                    raise ValueError
                value = float(value)
            except (TypeError, ValueError):
                raise AIError("기술적 지표에 숫자가 아닌 값이 있습니다.") from None
        if value is not None and pd.notna(value) and not isinstance(value, bool) and isfinite(float(value)):
            value = float(value)
            if (metric == "rsi_14" and not 0 <= value <= 100) or (metric.startswith(("ema_", "sma_", "bb_")) and value <= 0):
                raise AIError("기술적 지표가 허용 범위를 벗어났습니다.")
            role = "context"
            if metric == "rsi_14":
                role = "bullish" if value > 50 else "bearish" if value < 50 else "context"
                if value >= 70:
                    risks.append("RSI 과매수 구간이므로 상승 방향 근거와 조정 위험을 함께 확인해야 합니다.")
                elif value <= 30:
                    risks.append("RSI 과매도 구간이므로 하락 방향 근거와 반등 가능성을 함께 확인해야 합니다.")
            elif metric == "macd_histogram":
                role = "bullish" if value > 0 else "bearish" if value < 0 else "context"
            unit = "USDT" if metric.startswith(("ema_", "sma_", "bb_", "macd")) else (symbol.split("/")[0] if metric in ("volume", "volume_sma_20") else "")
            add(metric, value, label, unit, role)
        else:
            missing.append(metric)
    available = {fact.metric_name: fact.metric_value for fact in facts}
    if {"macd", "macd_signal", "macd_histogram"}.issubset(available):
        if abs(available["macd"]-available["macd_signal"]-available["macd_histogram"]) > 1e-8:
            raise AIError("MACD와 Signal/Histogram 값이 일치하지 않습니다.")
    for prefix in ("ema", "sma"):
        fast, slow = available.get(f"{prefix}_20"), available.get(f"{prefix}_60")
        if fast is not None and slow is not None:
            spread = (fast/slow-1)*100
            add(f"{prefix}_spread_pct", spread, f"{prefix.upper()}20의 {prefix.upper()}60 대비 차이", "%",
                "bullish" if spread > 0 else "bearish" if spread < 0 else "context")
    recent = closed.tail(21)
    if len(recent) == 21 and recent.timestamp.diff().dropna().eq(interval).all():
        value = (recent.iloc[-1].close/recent.iloc[0].close-1)*100
        add("recent_20_return_pct", value, "최근 연속 20봉 구간 종가 변화", "%",
            "bullish" if value > 0 else "bearish" if value < 0 else "context")
    else:
        missing.append("recent_20_return_pct")
    sufficient = len(closed) >= 60 and not set(("rsi_14", "macd_histogram", "ema_60", "sma_60")).intersection(missing)
    if gap_count or data.attrs.get("is_partial"):
        risks.append("수집 구간에 누락 또는 제한이 있습니다. 누락값을 임의로 보간하지 않았습니다.")
    if current-as_of > interval*2:
        risks.append("마지막 확정 가격 데이터가 현재 시각보다 오래되어 최신 시장 상태와 다를 수 있습니다.")
    if not sufficient:
        risks.append("판단 근거 부족: 핵심 지표를 계산할 연속 확정 데이터가 부족합니다.")
    add("missing_candles", gap_count, "수집된 구간 내부 누락 봉 수", source="DATA_QUALITY")
    macro_facts, metadata = summarize_macro_frames(macro_frames, as_of=as_of, now=current)
    facts.extend(macro_facts)
    macro_summary = ("거시경제는 현재 개정된 관측 수준을 제한적으로 설명합니다. 발표 시각과 당시 빈티지가 없어 방향 판단에 사용하지 않습니다."
                     if metadata else "판단 근거 부족: 사용 가능한 거시경제 관측값이 없습니다. FRED 조회 후 현재 자료의 제한적 설명만 가능합니다.")
    result = MarketSnapshot(symbol=symbol, timeframe=timeframe, candle_start_utc=iso(latest.timestamp),
                            data_as_of_utc=iso(as_of), fetched_at_utc=iso(fetched), facts=facts,
                            missing_metrics=missing, risk_notes=risks, limitations=LIMITATIONS,
                            sufficient=bool(sufficient), macro_summary=macro_summary, macro_metadata=metadata,
                            input_version=AI_PROMPT_VERSION)
    if len(result.model_dump_json().encode()) > AI_MAX_INPUT_BYTES:
        raise AIError("요약 데이터가 입력 크기 제한을 초과했습니다.")
    return result


def summarize_macro_frames(macro_frames, *, as_of, now, include_quality=False):
    """STEP 9와 워크플로우가 공유하는 FRED 문맥. 가격 DataFrame을 복사하지 않는다."""
    as_of, current = pd.Timestamp(iso(as_of)), pd.Timestamp(iso(now))
    facts = []
    def add(metric, value, label, unit, *, source, observed):
        value = float(value)
        facts.append(Fact(reference_id=f"{source.lower()}:{metric}", source_type=source, metric_name=metric,
                          metric_value=value, observation_time=iso(observed),
                          explanation=f"{label}: {value:.6g} {unit}.", role="context", unit=unit))
    metadata = []
    for series_id, frame in (macro_frames or {}).items():
        if series_id not in SERIES:
            raise AIError("지원하지 않는 거시경제 시리즈입니다.")
        spec = SERIES[series_id]
        if (not {"value", "observation_date", "units", "frequency", "series_id"}.issubset(frame.columns) or
                not frame.series_id.eq(series_id).all() or not frame.units.eq(spec.units).all() or not frame.frequency.eq(spec.frequency).all()):
            raise AIError("거시경제 데이터 식별자·주기·단위가 일치하지 않습니다.")
        dates = frame.observation_date
        try:
            if (dates.dt.tz is None or not dates.is_unique or not dates.is_monotonic_increasing or dates.isna().any()
                    or (dates.dt.tz_convert("UTC").dt.normalize() != dates.dt.tz_convert("UTC")).any()
                    or (spec.frequency == "M" and dates.dt.day.ne(1).any())):
                raise ValueError
            values = pd.to_numeric(frame.value, errors="raise")
            if values.dropna().map(lambda value: not isfinite(value)).any():
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise AIError("거시경제 관측 날짜 또는 수치 검증에 실패했습니다.") from None
        valid = frame.loc[values.map(lambda v: pd.notna(v) and isfinite(v)) & (dates <= as_of.normalize())]
        if valid.empty:
            continue
        row = valid.iloc[-1]
        add(series_id, row.value, f"{spec.name} 최신 유효 관측 수준", spec.unit_label, source="MACRO", observed=row.observation_date)
        macro_fetched = iso(frame.attrs.get("fetched_at"))
        if pd.Timestamp(macro_fetched) > current:
            raise AIError("거시 자료 수집 시각이 요청 시각보다 늦습니다.")
        metadata.append({"series_id": series_id, "frequency": spec.frequency, "units": spec.unit_label,
                         "fetched_at_utc": macro_fetched, "version_policy": "current_revised_snapshot",
                         "release_time": "unknown", "vintage": "unknown"})
        if include_quality:
            metadata[-1].update(observation_count=str(len(frame)), missing_values=str(int(frame.value.isna().sum())),
                publication_records_present=str(int(frame.get("available_at", pd.Series(dtype=object)).notna().sum())),
                vintage_records_present=str(int(frame.get("vintage_date", pd.Series(dtype=object)).notna().sum())))
    return facts, metadata
