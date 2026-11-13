"""R01 offline acceptance: read frozen synthetic fixtures, never rewrite baselines.

Run: .venv/Scripts/python.exe -X utf8 scripts/verify_redesign_baseline.py
Only explicitly generated test databases below .pytest_cache are written.
"""

from contextlib import ExitStack
from dataclasses import asdict
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.experiments import CODE_FILES, code_digest, execute_experiment, result_digest
from backtest.experiment_verification import read_experiment_artifact, verify_result
from portfolio.engine import execute_trade, evaluate, replay
from portfolio.models import Account, Quote
from services.local_store import LocalStore

FIXTURES = ROOT / "tests" / "fixtures" / "redesign_r01"
PROTECTED_FOLDERS = ("analytics", "agents", "backtest", "collectors", "config", "graph",
                     "indicators", "llm", "models", "portfolio", "risk", "services", "strategies")


def text_digest(path):
    """Match calculation-code hashing: UTF-8 text with universal LF newlines."""
    return sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def forbid_external(*args, **kwargs):
    raise RuntimeError("R01 baseline verification forbids external HTTP, paid LLM and default user storage")


def verify():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["schema_version"] == 1 and manifest["data_kind"] == "SYNTHETIC_ONLY", "Invalid baseline manifest")
    for name, expected in manifest["fixture_sha256_lf"].items():
        require(name in {"synthetic_experiment.json", "synthetic_account.json"}, "Unexpected fixture path")
        require(text_digest(FIXTURES / name) == expected, "Frozen fixture content changed")
    require(set(manifest["protected_code_sha256_lf"]) == set(CODE_FILES), "Protected code file set changed")
    for name, expected in manifest["protected_code_sha256_lf"].items():
        require(text_digest(ROOT / name) == expected, "Protected calculation source changed: " + name)
    require(code_digest() == manifest["code_digest"], "Calculation code digest changed")
    runtime = {p.relative_to(ROOT).as_posix() for folder in PROTECTED_FOLDERS for p in (ROOT / folder).glob("*.py")}
    require(runtime == set(manifest["protected_runtime_sha256_lf"]), "Backend source file set changed")
    for name, expected in manifest["protected_runtime_sha256_lf"].items():
        require(text_digest(ROOT / name) == expected, "Protected backend contract changed: " + name)

    with ExitStack() as guard:
        for target in ("requests.sessions.Session.request", "httpx.Client.send", "httpx.AsyncClient.send",
                       "openai.OpenAI", "openai.AsyncOpenAI", "services.local_store.default_store_path"):
            guard.enter_context(patch(target, side_effect=forbid_external))
        snapshot, reference = read_experiment_artifact((FIXTURES / "synthetic_experiment.json").read_bytes())
        result = execute_experiment(snapshot)
        require(result.status == "COMPLETED" and len(result.runs) == 12, "Expected 12 complete experiment runs")
        verification = verify_result(result, reference)
        require(verification.status == "MATCH", "Frozen experiment result SHA-256 mismatch")
        require(result_digest(execute_experiment(snapshot)) == verification.actual_digest, "Repeated execution differs")
        actual = [{"segment": r.segment, "strategy": r.strategy_name, "risk": r.risk_mode,
                   "metrics": asdict(r.result.metrics)} for r in result.runs]
        require(actual == manifest["experiment_metrics"], "Experiment performance records changed")
        stop_exits = 0
        for run in result.runs:
            events = run.result.events
            entries = events.loc[events.status.eq("FILLED") & events.action.eq("ENTRY")]
            require(entries.execution_bar.eq(entries.signal_bar + 1).all(), "Next-open bar timing changed")
            require(entries.execution_timing.eq("NEXT_OPEN").all(), "Entry execution policy changed")
            require(entries.execution_timestamp.gt(entries.signal_timestamp).all(), "Entry uses its signal bar")
            if run.risk_mode == "ON":
                stop_exits += int(run.result.trades.exit_reason.eq("STOP_LOSS").sum())
        require(stop_exits > 0, "Risk ON fixture did not trigger stop loss")
        # A valid baseline must also make mismatch/unavailable states observable.
        wrong = reference.model_copy(update={"result_digest": "0" * 64})
        require(verify_result(result, wrong).status == "MISMATCH", "Mismatch state no longer detected")
        require(verify_result(result, None).status == "UNAVAILABLE", "Missing reference is not unavailable")

        body = json.loads((FIXTURES / "synthetic_account.json").read_text(encoding="utf-8"))
        account = Account.model_validate(body["initial_account"])
        now = body["evaluated_at_utc"]
        for item in body["transactions"]:
            account = execute_trade(account, item["symbol"], item["side"], item["quantity"],
                Quote.model_validate(item["quote"]), fee_rate=item["fee_rate"], slippage_rate=item["slippage_rate"],
                event_id=item["event_id"], now=item["recorded_at_utc"])
        require(account == Account.model_validate(body["final_account"]), "Frozen account ledger changed")
        quotes = {s: Quote.model_validate(q) for s, q in body["valuation_quotes"].items()}
        value = evaluate(account, quotes, now=now)
        require(value.status == "COMPLETE", "Synthetic account valuation unavailable")
        for key, expected in body["hand_calculated"].items():
            require(isclose(getattr(value, key), expected, abs_tol=1e-9, rel_tol=1e-12), "Account equation changed: " + key)
        holding = next(h for h in replay(account).holdings if h.symbol == "BTC/USDT")
        require(holding.quantity == 1 and holding.average_price == 100, "Average cost/remaining quantity changed")

        # Explicit UUID path; no caller-supplied path and no user-store fallback.
        folder = ROOT / ".pytest_cache" / "r01-validation" / uuid4().hex
        folder.mkdir(parents=True, exist_ok=False)
        store = LocalStore(folder / "source.sqlite3")
        require(store.save_account(account) == 2, "Unexpected account revision")
        require(store.save_account(account) == 2, "Duplicate save changed revision")
        require(store.save_valuation(account, quotes, now=now) == value, "Saved valuation differs")
        with store.connection() as db, sqlite3.connect(folder / "backup.sqlite3") as backup:
            require(db.execute("SELECT version FROM metadata").fetchall() == [(1,)], "SQLite version changed")
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            require(set(tables) == set(manifest["sqlite_columns"]), "SQLite table set changed")
            for table in tables:
                # table is from sqlite_master and the fixed manifest allow-list above.
                columns = [list(r[1:]) for r in db.execute('PRAGMA table_info("' + table + '")')]
                require(columns == manifest["sqlite_columns"][table], "SQLite schema changed: " + table)
            require(db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2, "Duplicate transaction recorded")
            db.backup(backup)
        restored = LocalStore(folder / "backup.sqlite3")
        require(restored.load_account(account.account_id) == (account, 2), "Backup account restore differs")
        require(restored.load_valuations(account.account_id) == [value], "Backup valuation restore differs")

    return {"data_kind": "SYNTHETIC_ONLY", "protected_sources": len(CODE_FILES), "protected_backend_sources": len(runtime),
            "experiment_runs": len(result.runs), "result_verification": verification.status,
            "input_digest": snapshot.input_digest, "result_digest": verification.actual_digest,
            "stop_loss_exits": stop_exits, "account_equity": value.total_equity,
            "sqlite_schema_restore": "PASS", "external_http_or_paid_llm": "BLOCKED"}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
