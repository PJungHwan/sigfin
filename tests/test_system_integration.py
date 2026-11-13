"""STEP 19: 실제 모듈을 연결한 오프라인 통합·실패·경합·백업 검증."""

from concurrent.futures import ThreadPoolExecutor
import ast
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from unittest.mock import patch

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from backtest.experiments import capture_experiment, execute_experiment
from backtest.experiment_verification import experiment_artifact_json, read_experiment_artifact, verify_result
from backtest.models import BacktestConfig, RiskConfig
from models.research import BacktestResearchInput, PortfolioResearchInput, ResearchContext
from portfolio.engine import create_account, execute_trade, evaluate
from risk.portfolio import PortfolioRiskSettings
from services.local_store import LocalStore, StorageError, StorageConflict, digest
from services.market_data import load_market_data
from services.portfolio_service import quotes_from_frames
from services.research_service import run_research, validate_integrated_snapshot
from services.research_snapshots import pack_frame, restore_frame, restore_macro_frames
from strategies.models import default_strategy
from strategies.storage import strategy_to_json
from test_experiments import strategy
from test_portfolio import account, trade, NOW
from test_portfolio_ui import fresh_frame
from test_workflow import macro_frames


def test_unsupported_database_is_rejected_before_any_schema_write(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE metadata(version INTEGER PRIMARY KEY); INSERT INTO metadata VALUES(2); CREATE TABLE user_content(value TEXT); INSERT INTO user_content VALUES('preserve');")
    before = path.read_bytes()
    with pytest.raises(StorageError): LocalStore(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"metadata", "user_content"}


@pytest.mark.parametrize("body", [b'{}', '[' * 2000 + '0' + ']' * 2000], ids=["non_text", "deep_json"])
def test_corrupt_nontext_or_deep_database_payload_has_safe_error(tmp_path, body):
    store = LocalStore(tmp_path / "corrupt.sqlite3")
    raw_digest = sha256(body).hexdigest() if isinstance(body, bytes) else digest(body)
    with store.connection() as db:
        db.execute("INSERT INTO analyses VALUES(?,?,?)", ("bad", body, raw_digest))
    before = store.path.read_bytes()
    with pytest.raises(StorageError): store.load_analysis("bad")
    assert store.path.read_bytes() == before


def test_real_concurrent_writers_commit_one_revision_without_lost_update(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    original = account()
    LocalStore(path).save_account(original)
    barrier = Barrier(2)
    def write(event_id):
        store = LocalStore(path)
        updated = trade(original, event_id=event_id)
        barrier.wait(timeout=5)
        try:
            store.save_account(updated, expected_revision=0)
            return "SAVED", updated
        except StorageConflict:
            return "CONFLICT", None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ("first", "second")))
    assert sorted(status for status, _ in results) == ["CONFLICT", "SAVED"]
    saved = next(value for status, value in results if status == "SAVED")
    assert LocalStore(path).load_account() == (saved, 1)


def test_no_real_order_or_untrusted_code_execution_paths_in_runtime():
    root = Path(__file__).resolve().parents[1]
    forbidden = {"create_order", "create_market_order", "create_limit_order", "create_orders",
                 "create_market_buy_order", "create_market_sell_order", "withdraw", "transfer",
                 "private_post_order", "privatePostOrder"}
    violations = []
    for folder in ("collectors", "portfolio", "services", "graph", "agents", "ui", "strategies"):
        for file in (root / folder).glob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call): continue
                target = node.func
                if ((isinstance(target, ast.Attribute) and target.attr in forbidden)
                        or (isinstance(target, ast.Name) and target.id in {"eval", "exec"})):
                    violations.append(f"{file.relative_to(root)}:{node.lineno}")
    assert not violations, violations


def test_collect_indicators_ledger_risk_graph_report_store_backup_and_experiment(tmp_path):
    """합성 수집 → 기존 지표/원장/그래프 → SQLite → 새 연결/백업 → 고정 실험."""
    def collect(symbol, timeframe, period, **kwargs):
        raw = fresh_frame()
        raw.attrs.update(symbol=symbol, timeframe=timeframe)
        if symbol == "ETH/USDT":
            raw.loc[:, ["open", "high", "low", "close"]] *= .1
        return raw
    load_market_data.clear()
    try:
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=collect) as fetch, \
             patch("openai.OpenAI", side_effect=AssertionError("paid API forbidden")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
            frames = {symbol: load_market_data(symbol, "1h", "3개월") for symbol in ("BTC/USDT", "ETH/USDT")}
            assert "rsi_14" in frames["BTC/USDT"] and "macd" in frames["BTC/USDT"]
            for symbol, frame in frames.items():
                assert_frame_equal(load_market_data(symbol, "1h", "3개월"), frame)
            assert fetch.call_count == 2
            quotes = quotes_from_frames(frames, now=NOW)
            book = create_account(10000., now=NOW - pd.Timedelta(minutes=5))
            for i, symbol in enumerate(frames):
                book = execute_trade(book, symbol, "BUY", .01, quotes[symbol], event_id=f"trade-{i}", now=NOW)
            before = book.model_dump_json()
            store = LocalStore(tmp_path / "source.sqlite3")
            store.save_account(book)
            store.save_account(book)
            value = store.save_valuation(book, quotes, now=NOW)
            assert value.status == "COMPLETE" and store.load_valuations(book.account_id) == [value]
            configured = BacktestConfig()
            risk = RiskConfig(stop_loss_enabled=True, stop_loss_rate=.01)
            selected = default_strategy()
            context = ResearchContext(strategy_json=strategy_to_json(selected),
                portfolio=PortfolioResearchInput(account=book, quotes=quotes,
                    frames={s: pack_frame(f) for s, f in frames.items()}, history=[value],
                    risk_settings=PortfolioRiskSettings(), evaluated_at_utc=NOW.isoformat()),
                backtest=BacktestResearchInput(strategy_json=strategy_to_json(selected),
                    config=asdict(configured), risk=asdict(risk)))
            execution, snapshot = run_research(frames["BTC/USDT"], "BTC/USDT", "1h", period="3개월",
                macro_frames=macro_frames(), requested=True, context=context, now=NOW)
            report = validate_integrated_snapshot(snapshot)
            assert report.status != "FAILED"
            assert report.valuation == evaluate(book, quotes, now=NOW)
            assert report.portfolio_risk is not None and report.backtest is not None
            assert all(not detail["api_called"] for detail in report.agent_execution.values())
            assert {"bull_agent", "bear_agent", "risk_manager"} <= {row.node for row in execution.state["node_execution_logs"]}
            store.save_analysis(snapshot)
            # 백업 검증에는 사용자 원본이 아닌 임시 연구 DB만 사용한다.
            backup = tmp_path / "backup.sqlite3"
            with store.connection() as source, sqlite3.connect(backup) as destination:
                source.backup(destination)
            restored_store = LocalStore(backup)
            assert restored_store.load_account(book.account_id) == (book, 2)
            restored = restored_store.load_analysis(snapshot.record_id)
            assert restored == snapshot
            assert validate_integrated_snapshot(restored) == report
            replay_execution, replay_snapshot = run_research(restore_frame(restored.market), "BTC/USDT", "1h",
                period="3개월", macro_frames=restore_macro_frames(restored), requested=True, context=context, now=NOW)
            replay_report = validate_integrated_snapshot(replay_snapshot)
            assert replay_report.evidence == report.evidence
            assert replay_report.signal == report.signal
            assert book.model_dump_json() == before
            spec = capture_experiment(frames["BTC/USDT"], [selected, strategy("종가 비교", 150, 200)], configured, risk,
                symbol="BTC/USDT", timeframe="1h", period="3개월", split_at=80, as_of=NOW)
            experiment = execute_experiment(spec)
            restored_spec, reference = read_experiment_artifact(experiment_artifact_json(experiment))
            assert len(experiment.runs) == 12
            assert verify_result(execute_experiment(restored_spec), reference).status == "MATCH"
            assert fetch.call_count == 2 and store.load_account(book.account_id) == (book, 2)
    finally:
        load_market_data.clear()
