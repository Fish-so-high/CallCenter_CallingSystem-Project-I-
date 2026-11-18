import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen") 

import itertools
from datetime import datetime, timedelta

import pytest

from core import CallEvent, CallCenterEngine, EngineConfig, Employee, IncomingCall

T0 = datetime(2026, 10, 29, 9, 0, 0)
_counter = itertools.count(1)


def sec(n: float) -> timedelta:
    return timedelta(seconds=n)


@pytest.fixture
def make_call():
    def _make(event=CallEvent.SUCCESS, time_call=1.0, phone=None, received_at=T0, **kw):
        phone = phone or f"09{next(_counter):08d}"
        return IncomingCall(phone, event, time_call, received_at, **kw)
    return _make


@pytest.fixture
def make_engine():
    def _make(n_employees=3, max_queue=100, **cfg):
        emps = [Employee(f"E{i}", f"Nhân viên {i}") for i in range(1, n_employees + 1)]
        return CallCenterEngine(emps, EngineConfig(max_queue_size=max_queue, **cfg))
    return _make


@pytest.fixture
def collect():
    from core import EventType

    def _attach(engine):
        results = []
        engine.dispatcher.subscribe(
            EventType.CALL_FINISHED, lambda t, r: results.append(r))
        return results
    return _attach


# ======================================================================
# TEST DATABASES
# ======================================================================
import os
import uuid
from datetime import datetime as _dt
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from core import CallResult, CallStatus, CallTag

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def _test_url():
    raw = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not raw:
        pytest.skip("Chưa đặt TEST_DATABASE_URL: bỏ qua test cần PostgreSQL (xem tests/README.md)")
    url = make_url(raw)
    if url.database and url.database == os.environ.get("DB_NAME", "callcenter_db"):
        pytest.fail("TEST_DATABASE_URL đang trỏ tới database thật (DB_NAME). Hãy dùng một database riêng cho test.")
    return url


def _schema_engine(url, schema):
    return create_engine(url, connect_args={"options": f"-csearch_path={schema}"}, pool_pre_ping=True)


@pytest.fixture(scope="session")
def pg_admin():
    url = _test_url()
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.fail(f"Không kết nối được TEST_DATABASE_URL: {exc}")
    yield url, eng
    eng.dispose()


@pytest.fixture
def fresh_schema_engine(pg_admin):
    url, admin = pg_admin
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = _schema_engine(url, schema)
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture(scope="session")
def migrated_engine(pg_admin):
    from database.migrate import apply_migrations
    url, admin = pg_admin
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = _schema_engine(url, schema)
    apply_migrations(eng)
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture
def db(migrated_engine):
    from database import Database
    with migrated_engine.begin() as c:
        c.execute(text("TRUNCATE call_records, employee_monthly_stats, employees CASCADE"))
    database = Database(migrated_engine, retry_delay=0, sleep=lambda _s: None)
    yield database


@pytest.fixture
def repos(db):
    from database import CallRecordRepository, EmployeeRepository, MonthlyStatsRepository
    emp = EmployeeRepository(db)
    emp.upsert_many([Employee("E1", "Nhân viên 1"), Employee("E2", "Nhân viên 2")])
    return type("Repos", (), {
        "employees": emp,
        "calls": CallRecordRepository(db, VN),
        "stats": MonthlyStatsRepository(db, VN),
    })()


_phone_seq = itertools.count(1)


def make_result(tag=CallTag.SUCCESS, employee="E1", ended=None, duration=2.0, call_id=None,
                received=None, started=None, phone=None):
    ended = ended or _dt(2026, 10, 29, 10, 0, 5)
    has_emp = tag not in (CallTag.REJECTED, CallTag.ABANDONED)
    started = started if started is not None else (ended - timedelta(seconds=duration) if has_emp else None)
    received = received or (started or ended) - timedelta(seconds=1)
    status = {CallTag.SUCCESS: CallStatus.COMPLETED, CallTag.MISS: CallStatus.MISSED,
              CallTag.BREAK: CallStatus.ABANDONED, CallTag.REJECTED: CallStatus.REJECTED,
              CallTag.ABANDONED: CallStatus.ABANDONED}[tag]
    return CallResult(call_id or uuid.uuid4().hex, phone or f"09{next(_phone_seq):08d}",
                      employee if has_emp else None, received, started, ended, duration, status, tag)
 