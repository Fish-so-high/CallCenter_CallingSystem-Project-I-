import time

import pytest

from core.models import CallTag, Employee
from services import PersistenceService
from tests.conftest import make_result
from tests.helpers import FakeBackend, wait_until

EMPS = [Employee("E1", "a"), Employee("E2", "b")]


def make_service(backend=None, factory=None, **kw):
    backend = backend or FakeBackend()
    events = []
    svc = PersistenceService(factory or (lambda: backend), lambda: EMPS, retry_interval=0.05, **kw)
    svc.on_event.append(lambda k, m: events.append((k, m)))
    return svc, backend, events


def results(n):
    return [make_result(CallTag.SUCCESS, "E1", duration=1) for _ in range(n)]


def test_writes_results_and_syncs_employees_first():
    svc, be, events = make_service(); svc.start()
    for r in results(5):
        svc.submit_result(r)
    assert svc.wait_idle(3)
    assert be.log[0][0] == "sync"                      
    assert len(be.saved) == 5 and svc.status().saved == 5 and svc.status().connected
    assert events == [("success", "Đã kết nối PostgreSQL")]
    svc.stop()


def test_database_down_at_start_buffers_then_flushes_in_order():
    be = FakeBackend()
    state = {"up": False}

    def factory():
        if not state["up"]:
            raise ConnectionError("connection refused")
        return be

    svc, _, events = make_service(be, factory); svc.start()
    rs = results(20)
    for r in rs:
        svc.submit_result(r)
    assert wait_until(lambda: events and events[0][0] == "error")
    st = svc.status()
    assert not st.connected and st.pending == 20 and "không khả dụng" in st.message
    assert be.saved == []
    state["up"] = True
    assert wait_until(lambda: len(be.saved) == 20, 3)
    assert [r.call_id for r in be.saved] == [r.call_id for r in rs]      
    assert [k for k, _ in events] == ["error", "success"]             
    svc.stop()


def test_connection_lost_midway_no_loss_no_duplicates():
    be = FakeBackend(); be.fail_next = 3
    svc, _, events = make_service(be); svc.start()
    rs = results(30)
    for r in rs:
        svc.submit_result(r)
    assert wait_until(lambda: len(be.saved) == 30, 4)
    assert len({r.call_id for r in be.saved}) == 30
    assert [r.call_id for r in be.saved] == [r.call_id for r in rs]
    assert any(k == "error" for k, _ in events) and svc.status().connected
    svc.stop()


def test_reconnects_when_database_returns():
    be = FakeBackend()
    svc, _, events = make_service(be); svc.start()
    svc.submit_result(results(1)[0]); assert svc.wait_idle(2)
    be.alive = False
    for r in results(5):
        svc.submit_result(r)
    assert wait_until(lambda: not svc.status().connected, 3)
    assert svc.status().pending == 5
    be.alive = True
    assert wait_until(lambda: len(be.saved) == 6, 4) and svc.status().connected
    kinds = [k for k, _ in events]
    assert kinds[0] == "success" and "error" in kinds and kinds[-1] == "success"
    svc.stop()


def test_unknown_employee_triggers_resync_and_retry():
    be = FakeBackend(); be.require_known = True
    svc, _, _ = make_service(be); svc.start()
    svc.submit_result(make_result(CallTag.SUCCESS, "E2", duration=1))
    assert svc.wait_idle(3) and len(be.saved) == 1
    svc.stop()


def test_poison_record_is_isolated_and_counted():
    be = FakeBackend()
    good1, bad, good2 = results(3)
    be.poison.add(bad.call_id)
    svc, _, _ = make_service(be); svc.start()
    for r in (good1, bad, good2):
        svc.submit_result(r)
    assert svc.wait_idle(3)
    assert [r.call_id for r in be.saved] == [good1.call_id, good2.call_id]
    assert svc.status().dropped == 1 and svc.status().saved == 2
    svc.stop()


def test_buffer_limit_drops_oldest_when_database_unreachable():
    def factory():
        raise ConnectionError("down")
    svc, _, _ = make_service(factory=factory, buffer_limit=10); svc.start()
    rs = results(25)
    for r in rs:
        svc.submit_result(r)
    st = svc.status()
    assert st.pending == 10 and st.dropped == 15
    assert [r.call_id for r in svc._pending] == [r.call_id for r in rs[-10:]]       
    svc.stop(timeout=1)


def test_wait_idle_returns_false_when_disconnected_quickly():
    def factory():
        raise ConnectionError("down")
    svc, _, _ = make_service(factory=factory); svc.start()
    svc.submit_result(results(1)[0])
    wait_until(lambda: not svc.status().connected)
    t = time.monotonic()
    assert svc.wait_idle(5) is False and time.monotonic() - t < 1.0     
    svc.stop(timeout=1)


def test_calls_repository_only_when_connected():
    svc, be, _ = make_service(); assert svc.calls_repository is None
    svc.start(); assert wait_until(lambda: svc.calls_repository is be.calls)
    be.alive = False; svc.submit_result(results(1)[0])
    assert wait_until(lambda: svc.calls_repository is None, 3)
    svc.stop(timeout=1)


def test_stop_flushes_pending_results():
    svc, be, _ = make_service(); svc.start()
    for r in results(50):
        svc.submit_result(r)
    svc.stop()
    assert len(be.saved) == 50 and be.closed


def test_wait_idle_does_not_return_while_a_batch_is_still_being_written():
   
    be = FakeBackend(); be.delay = 0.3
    svc, _, _ = make_service(be); svc.start()
    for r in results(5):
        svc.submit_result(r)
    assert wait_until(lambda: svc.status().pending == 5 and not svc._pending, 2)     
    assert svc.status().pending == 5                                                
    assert svc.wait_idle(3) is True
    assert len(be.saved) == 5 and svc.status().pending == 0
    svc.stop()


def test_stop_waits_for_inflight_batch():
    be = FakeBackend(); be.delay = 0.3
    svc, _, _ = make_service(be); svc.start()
    for r in results(7):
        svc.submit_result(r)
    wait_until(lambda: not svc._pending)
    svc.stop()
    assert len(be.saved) == 7


def test_real_postgres_end_to_end(db, tmp_path):
    from sqlalchemy import text
    from database import CallRecordRepository, EmployeeRepository
    from services.persistence import Backend
    from tests.conftest import VN
    backend = Backend(db, EmployeeRepository(db), CallRecordRepository(db, VN))
    svc = PersistenceService(lambda: backend, lambda: EMPS, retry_interval=0.05)
    svc.start()
    for r in results(40):
        svc.submit_result(r)
    assert svc.wait_idle(5)
    with db.engine.connect() as c:
        assert c.execute(text("select count(*) from call_records")).scalar() == 40
        assert c.execute(text("select count(*) from employees")).scalar() == 2
        assert c.execute(text("select successful_calls from employee_monthly_stats")).scalar() == 40
    svc.stop()
