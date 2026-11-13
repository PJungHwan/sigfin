"""로컬 SQLite v1. 연결/쓰기마다 트랜잭션, SHA-256 손상 탐지, 낙관적 원장 버전 검사."""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import sqlite3

from portfolio.models import Account, Quote, Valuation
from portfolio.engine import replay, evaluate
from services.research_snapshots import safe_json, validate_snapshot
from llm.config import AIError


class StorageError(ValueError):
    pass


class StorageConflict(StorageError):
    pass


def default_store_path():
    return Path(__file__).resolve().parents[1]/'data'/'sigfin.sqlite3'


def digest(text):
    return sha256(text.encode()).hexdigest()


def verified(payload,expected):
    if not isinstance(payload,str) or len(payload.encode('utf-8'))>8_000_000:
        raise StorageError("저장 자료의 형식/크기 검증에 실패했습니다.")
    if digest(payload)!=expected:
        raise StorageError("저장 자료의 무결성 검증에 실패했습니다.")
    from strategies.storage import _unique_keys, _reject_constant
    try:
        return json.loads(payload,object_pairs_hook=_unique_keys,parse_constant=_reject_constant)
    except (ValueError,TypeError,RecursionError):
        raise StorageError("저장 JSON의 구조 검증에 실패했습니다.") from None


class LocalStore:
    def __init__(self,path=None):
        self.path=Path(path) if path is not None else default_store_path()
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connection() as db:
                # 미지원/다른 형식의 DB에는 CREATE도 실행하지 않는다.
                has_metadata=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone()
                if has_metadata:
                    versions=db.execute('SELECT version FROM metadata').fetchall()
                    if versions and versions!=[(1,)]:
                        raise StorageError("지원하지 않는 저장 스키마 버전입니다.")
                elif db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                    raise StorageError("SIGFIN 저장 스키마가 아닙니다. 기존 파일을 보존합니다.")
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS metadata(version INTEGER PRIMARY KEY);
                    CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL, updated INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS events(account_id TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(account_id,id));
                    CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS valuations(account_id TEXT NOT NULL, revision INTEGER NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(account_id,revision,as_of));
                ''')
                rows=db.execute('SELECT version FROM metadata').fetchall()
                if not rows:
                    db.execute('INSERT OR IGNORE INTO metadata VALUES(1)')
                elif rows!=[(1,)]:
                    raise StorageError("지원하지 않는 저장 스키마 버전입니다.")
        except (OSError,sqlite3.Error):
            raise StorageError("로컬 저장소를 열 수 없습니다. 권한/손상/저장 경로를 확인하세요.") from None

    @contextmanager
    def connection(self):
        db=None
        try:
            db=sqlite3.connect(self.path,timeout=2,isolation_level=None)
            yield db
        except sqlite3.Error:
            raise StorageError("로컬 저장/복원에 실패했습니다. 기존 원장을 유지합니다.") from None
        finally:
            if db is not None: db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def save_account(self,account,*,expected_revision=None):
        account=Account.model_validate(account); replay(account)
        payload=safe_json(account); revision=len(account.trades)
        with self.transaction() as db:
            row=db.execute('SELECT revision,payload,hash FROM accounts WHERE id=?',(account.account_id,)).fetchone()
            if row:
                prior=Account.model_validate(verified(row[1],row[2])); replay(prior)
                if row[1]==payload: return row[0]
                if expected_revision!=row[0] or revision<row[0] or account.model_dump(exclude={'trades'})!=prior.model_dump(exclude={'trades'}) or account.trades[:row[0]]!=prior.trades:
                    raise StorageConflict("저장 원장이 바뀌었습니다. 최신 계좌를 불러온 뒤 다시 기록하세요.")
            elif expected_revision not in (None,-1):
                raise StorageConflict("저장 원장이 없어 버전이 일치하지 않습니다.")
            for event in account.trades:
                body=safe_json(event)
                existing=db.execute('SELECT payload FROM events WHERE account_id=? AND id=?',(account.account_id,event.event_id)).fetchone()
                if existing and existing[0]!=body: raise StorageConflict("중복 거래 ID의 내용이 다릅니다.")
                db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(account.account_id,event.event_id,body))
            db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,(SELECT COALESCE(MAX(updated),0)+1 FROM accounts))',
                       (account.account_id,revision,payload,digest(payload)))
        return revision

    def load_account(self,account_id=None):
        with self.connection() as db:
            row=(db.execute('SELECT payload,hash,revision FROM accounts WHERE id=?',(account_id,)).fetchone() if account_id else
                 db.execute('SELECT payload,hash,revision FROM accounts ORDER BY updated DESC LIMIT 1').fetchone())
            if row is None: return None
            try:
                account=Account.model_validate(verified(row[0],row[1])); replay(account)
                if len(account.trades)!=row[2]: raise ValueError
                events=db.execute('SELECT id,payload FROM events WHERE account_id=?',(account.account_id,)).fetchall()
                if dict(events)!={event.event_id:safe_json(event) for event in account.trades}: raise ValueError
                return account,row[2]
            except (ValueError,TypeError):
                raise StorageError("저장 계좌/거래 검증에 실패했습니다. 자동 초기화하지 않습니다.") from None

    def list_accounts(self):
        with self.connection() as db:
            return db.execute('SELECT id,revision FROM accounts ORDER BY updated DESC').fetchall()

    def save_analysis(self,snapshot):
        snapshot=validate_snapshot(snapshot); payload=safe_json(snapshot)
        with self.transaction() as db:
            prior=db.execute('SELECT payload,hash FROM analyses WHERE id=?',(snapshot.record_id,)).fetchone()
            if prior:
                verified(*prior)
                if prior[0]!=payload: raise StorageConflict("동일 분석 ID의 내용이 다릅니다.")
                return snapshot.record_id
            db.execute('INSERT INTO analyses VALUES(?,?,?)',(snapshot.record_id,payload,digest(payload)))
        return snapshot.record_id

    def load_analysis(self,ident):
        with self.connection() as db:
            row=db.execute('SELECT payload,hash FROM analyses WHERE id=?',(ident,)).fetchone()
            if row is None: raise StorageError("해당 분석 기록이 없습니다.")
            try: return validate_snapshot(verified(*row))
            except (ValueError,TypeError,KeyError,AIError): raise StorageError("분석 기록의 구조/해시/근거 검증에 실패했습니다.") from None

    def list_analyses(self):
        with self.connection() as db:
            return [r[0] for r in db.execute('SELECT id FROM analyses ORDER BY rowid DESC LIMIT 200')]

    def save_valuation(self,account,quotes,*,now):
        value=evaluate(account,quotes,now=now)
        if value.status!='COMPLETE': raise StorageError("가격이 부족한 평가는 저장하지 않습니다.")
        payload=safe_json({'valuation':value,'quotes':quotes})
        with self.transaction() as db:
            row=db.execute('SELECT revision,payload,hash FROM accounts WHERE id=?',(account.account_id,)).fetchone()
            if row is None or row[0]!=len(account.trades) or row[1]!=safe_json(account): raise StorageConflict("평가와 저장 원장 버전이 다릅니다.")
            verified(row[1],row[2])
            db.execute('INSERT OR IGNORE INTO valuations VALUES(?,?,?,?,?)',(account.account_id,len(account.trades),value.data_as_of_utc,payload,digest(payload)))
        return value

    def load_valuations(self,account_id):
        loaded=self.load_account(account_id)
        if loaded is None: return []
        account,_=loaded
        with self.connection() as db:
            rows=db.execute('SELECT payload,hash FROM valuations WHERE account_id=? ORDER BY rowid',(account_id,)).fetchall()
        values=[]
        try:
            for row in rows:
                data=verified(*row); value=Valuation.model_validate(data['valuation'])
                historic=account.model_copy(update={'trades':account.trades[:value.account_revision]})
                quotes={key:Quote.model_validate(item) for key,item in data['quotes'].items()}
                if evaluate(historic,quotes,now=value.evaluated_at_utc)!=value: raise ValueError
                values.append(value)
            return values
        except (ValueError,TypeError,KeyError):
            raise StorageError("평가 이력 검증에 실패했습니다.") from None
