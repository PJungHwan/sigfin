"""SQLite 원자적 저장/손상/중복/복원과 실제 그래프 스냅샷. 네트워크 없음."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3
import unittest

from pandas.testing import assert_frame_equal

from services.local_store import LocalStore,StorageError,StorageConflict
from services.research_snapshots import capture_workflow,restore_frame,restore_macro_frames,validate_snapshot,safe_json
from graph.runner import run_workflow
from test_workflow import market,macro_frames
from test_ai_analysis import NOW as WORKFLOW_NOW
from test_portfolio import account,trade,quote,NOW


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'test.sqlite3'
        self.store=LocalStore(self.path)

    def test_account_and_events_survive_new_connection(self):
        a=trade(account())
        self.assertEqual(self.store.save_account(a),1)
        self.assertEqual(LocalStore(self.path).load_account(),(a,1))

    def test_duplicate_save_is_idempotent_and_changed_prefix_rejected(self):
        a=trade(account(),event_id='one'); self.store.save_account(a)
        self.store.save_account(a)
        with self.store.connection() as db: self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
        fake=a.model_copy(deep=True); fake.trades[0].quantity=1.
        with self.assertRaises(StorageConflict): self.store.save_account(fake,expected_revision=1)

    def test_optimistic_concurrent_updates_prevent_lost_trade(self):
        a=account(); self.store.save_account(a)
        first=trade(a,event_id='first'); second=trade(a,event_id='second')
        self.store.save_account(first,expected_revision=0)
        with self.assertRaises(StorageConflict): LocalStore(self.path).save_account(second,expected_revision=0)
        self.assertEqual(self.store.load_account()[0],first)

    def test_transaction_failure_rolls_back_events_and_account(self):
        a=account(); self.store.save_account(a)
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER break_save BEFORE UPDATE ON accounts BEGIN SELECT RAISE(ABORT,'test'); END")
            # INSERT OR REPLACE는 INSERT 트리거 경로다.
            db.execute("CREATE TRIGGER break_insert BEFORE INSERT ON accounts BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(StorageError): self.store.save_account(trade(a),expected_revision=0)
        self.assertEqual(self.store.load_account()[0],a)
        with self.store.connection() as db: self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    def test_corrupt_payload_and_missing_events_are_explicit(self):
        a=trade(account()); self.store.save_account(a)
        with self.store.connection() as db: db.execute("DELETE FROM events")
        with self.assertRaises(StorageError): self.store.load_account()
        with self.store.connection() as db: db.execute("UPDATE accounts SET payload='{}'")
        with self.assertRaises(StorageError): self.store.load_account()

    def test_bad_database_path_and_schema_never_reset(self):
        with self.store.connection() as db: db.execute('UPDATE metadata SET version=2')
        with self.assertRaises(StorageError): LocalStore(self.path)
        bad=Path(self.folder.name)/'bad.sqlite3'; bad.write_bytes(b'corrupted file')
        with self.assertRaises(StorageError): LocalStore(bad)
        self.assertEqual(bad.read_bytes(),b'corrupted file')

    def test_saved_valuation_reconstructs_historic_ledger_revision(self):
        a=trade(account()); self.store.save_account(a)
        saved=self.store.save_valuation(a,{'BTC/USDT':quote(120.)},now=NOW)
        self.store.save_valuation(a,{'BTC/USDT':quote(120.)},now=NOW)
        later=trade(a,'SELL',1.,110.); self.store.save_account(later,expected_revision=1)
        self.assertEqual(self.store.load_valuations(a.account_id),[saved])

    def test_unavailable_or_outdated_valuation_not_saved(self):
        a=trade(account()); self.store.save_account(a)
        with self.assertRaises(StorageError): self.store.save_valuation(a,{},now=NOW)
        with self.assertRaises(StorageConflict): self.store.save_valuation(trade(a),{'BTC/USDT':quote()},now=NOW)

    def snapshot(self):
        data=market(); frames=macro_frames()
        result=run_workflow(data,'BTC/USDT','1h',now=WORKFLOW_NOW,macro_frames=frames,requested=True,debate_enabled=True,risk_enabled=True)
        return capture_workflow(result,data,macro_frames=frames,settings={'period':'3개월','mode':'Mock Workflow'}),data

    def test_analysis_snapshot_roundtrip_inputs_settings_results(self):
        snapshot,data=self.snapshot()
        ident=self.store.save_analysis(snapshot)
        restored=LocalStore(self.path).load_analysis(ident)
        self.assertEqual(snapshot,restored)
        assert_frame_equal(restore_frame(restored.market),data)
        self.assertEqual(self.store.list_analyses(),[ident])

    def test_analysis_duplicate_and_digest_tampering(self):
        snapshot,_=self.snapshot(); self.store.save_analysis(snapshot); self.store.save_analysis(snapshot)
        changed=snapshot.model_copy(deep=True); changed.settings['period']='1년'
        with self.assertRaises(ValueError): validate_snapshot(changed)
        changed=snapshot.model_copy(deep=True); changed.results['execution_status']='FAILED'
        with self.assertRaises(StorageConflict): self.store.save_analysis(changed)
        with self.store.connection() as db: db.execute("UPDATE analyses SET hash='broken'")
        with self.assertRaises(StorageError): self.store.load_analysis(snapshot.record_id)

    def test_secret_and_unapproved_fields_blocked_without_api(self):
        snapshot,_=self.snapshot()
        for key in ('api_key','authorization','custom'):
            changed=snapshot.model_copy(deep=True); changed.settings[key]='do-not-store'
            with self.assertRaises(ValueError): self.store.save_analysis(changed)
        with self.assertRaises(ValueError): safe_json({'note':'sk-not-for-storage-123456789'})
        with patch('openai.OpenAI') as sdk:
            self.store.save_analysis(snapshot); self.store.load_analysis(snapshot.record_id)
        sdk.assert_not_called()

    def test_unknown_frame_column_is_not_silently_archived(self):
        data=market(); data['api_key']='do-not-store'
        from services.research_snapshots import pack_frame
        with self.assertRaises(ValueError): pack_frame(data)

    def test_restored_inputs_reproduce_deterministic_analysis(self):
        snapshot,data=self.snapshot()
        self.store.save_analysis(snapshot)
        restored=self.store.load_analysis(snapshot.record_id)
        execution=run_workflow(restore_frame(restored.market),restored.symbol,restored.timeframe,
            macro_frames=restore_macro_frames(restored),now=WORKFLOW_NOW,
            requested=True,debate_enabled=True,risk_enabled=True)
        for key in ('technical_result','macro_result','debate_evaluation','risk_result','aggregation_result'):
            self.assertEqual(__import__('json').loads(safe_json(execution.state[key])),restored.results[key])
