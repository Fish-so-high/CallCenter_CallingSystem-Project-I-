import random
import time

import pytest

from core import (
    CallEvent, CallFlowEngine, CallStatus, CallTag, EngineConfig, Employee, EmployeeStatus as S,
    EventType, IncomingCall, ReleasePolicy,
)
from tests.conftest import T0, sec


# ------------------------------------------------------------------ FIFO & ghép cặp
def test_calls_are_assigned_in_fifo_order_to_employees_in_fifo_order(make_engine, make_call):
    eng = make_engine(2)
    c1, c2, c3 = (eng.submit(make_call(time_call=5), T0) for _ in range(3))
    assert (c1.employee_id, c2.employee_id) == ("E1", "E2")
    assert c3.status is CallStatus.WAITING and c3.employee_id is None
    eng.tick(T0 + sec(5))   # c1, c2 kết thúc cùng lúc -> E1 rồi E2 về cuối hàng; c3 vào E1
    assert c3.employee_id == "E1" and c3.status is CallStatus.IN_PROGRESS
    assert list(eng.queues.service) == ["E2"]


def test_one_employee_per_call_and_busy_employee_not_reassigned(make_engine, make_call):
    eng = make_engine(1)
    c1 = eng.submit(make_call(time_call=3), T0)
    c2 = eng.submit(make_call(time_call=3), T0)
    assert c1.employee_id == "E1" and c2.employee_id is None
    assert eng.employees.get("E1").status is S.BUSY
    assert len(eng.queues.service) == 0 and len(eng.queues.calling) == 1
    eng.tick(T0 + sec(1))                       # chưa đến hạn: c2 vẫn chờ
    assert c2.status is CallStatus.WAITING


def test_finished_employee_goes_to_back_of_service_queue(make_engine, make_call):
    eng = make_engine(3)
    eng.submit(make_call(time_call=1), T0)      # E1 bận
    eng.tick(T0 + sec(1))
    assert list(eng.queues.service) == ["E2", "E3", "E1"]


def test_no_available_employee_calls_keep_waiting(make_engine, make_call):
    eng = make_engine(0)
    for _ in range(3):
        eng.submit(make_call(), T0)
    eng.tick(T0 + sec(100))
    assert len(eng.queues.calling) == 3 and eng.stats["total"] == 0


# ---------------------------------------------------------------- Success / Miss / Break
def test_success_result(make_engine, make_call, collect):
    eng = make_engine(1); res = collect(eng)
    c = make_call(CallEvent.SUCCESS, 2.0)
    eng.submit(c, T0 + sec(0))
    assert eng.tick(T0 + sec(1.9)) == 0
    assert eng.tick(T0 + sec(2)) == 1
    r = res[0]
    assert (r.tag, r.status, r.employee_id) == (CallTag.SUCCESS, CallStatus.COMPLETED, "E1")
    assert r.started_at == T0 and r.ended_at == T0 + sec(2) and r.duration_seconds == 2.0
    assert eng.employees.get("E1").status is S.AVAILABLE


def test_miss_has_zero_duration_and_releases_employee(make_engine, make_call, collect):
    eng = make_engine(1); res = collect(eng)
    eng.submit(make_call(CallEvent.MISS, 0), T0)
    eng.tick(T0)
    r = res[0]
    assert (r.tag, r.status, r.duration_seconds) == (CallTag.MISS, CallStatus.MISSED, 0.0)
    assert list(eng.queues.service) == ["E1"]


def test_break_is_recorded_not_dropped(make_engine, make_call, collect):
    eng = make_engine(1); res = collect(eng)
    eng.submit(make_call(CallEvent.BREAK, 0.4), T0)
    eng.tick(T0 + sec(0.4))
    r = res[0]
    assert (r.tag, r.status, r.duration_seconds) == (CallTag.BREAK, CallStatus.ABANDONED, 0.4)
    assert r.employee_id == "E1" and list(eng.queues.service) == ["E1"]


def test_to_break_release_policy_for_miss(make_engine, make_call):
    eng = make_engine(2, miss_release_policy=ReleasePolicy.TO_BREAK)
    eng.submit(make_call(CallEvent.MISS, 0), T0)
    eng.tick(T0)
    assert eng.employees.get("E1").status is S.BREAK
    assert list(eng.queues.service) == ["E2"]
    assert eng.set_available("E1", T0) and list(eng.queues.service) == ["E2", "E1"]


# ----------------------------------------------------------------- từ chối khi đầy
def test_rejected_when_calling_queue_full(make_engine, make_call, collect):
    eng = make_engine(1, max_queue=2); res = collect(eng)
    calls = [eng.submit(make_call(time_call=10), T0) for _ in range(4)]
    # c0 vào thẳng nhân viên, c1 c2 chờ (đầy), c3 bị từ chối
    assert [c.status for c in calls] == [CallStatus.IN_PROGRESS, CallStatus.WAITING,
                                         CallStatus.WAITING, CallStatus.REJECTED]
    assert len(eng.queues.calling) == 2
    assert len(res) == 1 and res[0].tag is CallTag.REJECTED
    assert res[0].employee_id is None and res[0].started_at is None
    assert eng.stats["Rejected"] == 1


def test_queue_accepts_again_after_space_frees(make_engine, make_call):
    eng = make_engine(1, max_queue=1)
    eng.submit(make_call(time_call=1), T0)
    eng.submit(make_call(time_call=1), T0)                 # chờ
    assert eng.submit(make_call(), T0).status is CallStatus.REJECTED
    eng.tick(T0 + sec(1))                                  # c2 vào nhân viên, hàng trống
    assert eng.submit(make_call(), T0 + sec(1)).status is CallStatus.WAITING


# ------------------------------------------------------------------- nghỉ / quay lại
def test_idle_employee_on_break_gets_no_calls_and_can_return(make_engine, make_call):
    eng = make_engine(2)
    assert eng.set_break("E1")
    assert eng.employees.get("E1").status is S.BREAK and "E1" not in eng.queues.service
    c1 = eng.submit(make_call(time_call=5), T0)
    c2 = eng.submit(make_call(time_call=5), T0)
    assert c1.employee_id == "E2" and c2.status is CallStatus.WAITING   # E1 nghỉ nên c2 chờ
    assert eng.set_available("E1", T0 + sec(1))
    assert c2.employee_id == "E1" and c2.started_at == T0 + sec(1)


def test_busy_employee_break_takes_effect_after_call(make_engine, make_call):
    eng = make_engine(1)
    eng.submit(make_call(time_call=2), T0)
    assert eng.set_break("E1")
    e = eng.employees.get("E1")
    assert e.status is S.BUSY and e.pending_status is S.BREAK
    waiting = eng.submit(make_call(time_call=1), T0)
    eng.tick(T0 + sec(2))
    assert e.status is S.BREAK and e.pending_status is None
    assert waiting.status is CallStatus.WAITING          # không được nhận khi đang nghỉ
    eng.set_available("E1", T0 + sec(3))
    assert waiting.employee_id == "E1"


def test_pending_break_can_be_cancelled(make_engine, make_call):
    eng = make_engine(1)
    eng.submit(make_call(time_call=2), T0)
    eng.set_break("E1"); eng.set_available("E1", T0)
    eng.tick(T0 + sec(2))
    assert eng.employees.get("E1").status is S.AVAILABLE and list(eng.queues.service) == ["E1"]


def test_offline_employee_is_removed_and_inactive(make_engine, make_call):
    eng = make_engine(2)
    assert eng.set_offline("E1")
    assert not eng.employees.get("E1").is_active and "E1" not in eng.queues.service
    assert eng.set_available("E1") and eng.employees.get("E1").is_active


def test_initial_non_available_employees_not_in_queue():
    eng = CallFlowEngine([Employee("A", "a"), Employee("B", "b", S.BREAK), Employee("C", "c", S.OFFLINE)])
    assert list(eng.queues.service) == ["A"]


def test_add_employee_picks_up_waiting_call(make_engine, make_call):
    eng = make_engine(0)
    c = eng.submit(make_call(), T0)
    eng.add_employee("N1", "Mới", T0 + sec(1))
    assert c.employee_id == "N1"
    with pytest.raises(ValueError):
        eng.add_employee("N1", "Trùng")


# ------------------------------------------------------------------ chống xử lý trùng
def test_same_call_is_not_processed_twice(make_engine, collect):
    eng = make_engine(2); res = collect(eng)
    inc = IncomingCall("0912345678", "Success", 1, T0, call_id="same")
    assert eng.submit(inc, T0) is not None
    assert eng.submit(inc, T0) is None
    eng.tick(T0 + sec(5))
    assert len(res) == 1 and eng.stats["total"] == 1


def test_abandon_waiting_removes_and_compacts(make_engine, make_call, collect):
    eng = make_engine(1); res = collect(eng)
    eng.submit(make_call(time_call=9), T0)
    a = eng.submit(make_call(), T0); b = eng.submit(make_call(), T0); c = eng.submit(make_call(), T0)
    assert eng.abandon_waiting(b.call_id, T0 + sec(1))
    assert eng.abandon_waiting(b.call_id) is False              # đã xóa
    assert [w.call_id for w in eng.snapshot().waiting] == [a.call_id, c.call_id]
    assert res[0].tag is CallTag.ABANDONED and res[0].status is CallStatus.ABANDONED


# ---------------------------------------------------------- không treo với event 0 giây
def test_many_zero_duration_calls_do_not_hang(make_engine, make_call, collect):
    eng = make_engine(3, max_queue=20_000); res = collect(eng)
    n = 10_000
    start = time.perf_counter()
    for _ in range(n):
        eng.submit(make_call(CallEvent.MISS, 0), T0)
    eng.tick(T0)
    assert time.perf_counter() - start < 10
    assert len(res) == n and eng.stats["Miss"] == n
    assert len(eng.queues.calling) == 0 and eng.processor.active_calls() == []
    assert eng.tick(T0) == 0                       # không còn gì để làm


# ------------------------------------------------------------------ tạm dừng / tiếp tục
def test_pause_freezes_time_and_resume_shifts_deadlines(make_engine, make_call, collect):
    eng = make_engine(1); res = collect(eng)
    eng.submit(make_call(time_call=2), T0)
    eng.pause(T0 + sec(1))
    assert eng.tick(T0 + sec(50)) == 0 and not res
    eng.resume(T0 + sec(11))                         # đã dừng 10s; cuộc gọi còn 1s (1s đã trôi trước khi dừng)
    assert eng.tick(T0 + sec(11.5)) == 0
    assert eng.tick(T0 + sec(12)) == 1
    assert res[0].started_at == T0 + sec(10) and res[0].ended_at == T0 + sec(12)
    assert res[0].duration_seconds == 2.0


def test_calls_submitted_while_paused_wait_until_resume(make_engine, make_call):
    eng = make_engine(1)
    eng.pause(T0)
    c = eng.submit(make_call(time_call=1), T0 + sec(1))
    assert c.status is CallStatus.WAITING
    eng.resume(T0 + sec(5))
    assert c.status is CallStatus.IN_PROGRESS and c.started_at == T0 + sec(5)


# ------------------------------------------------------------------ sự kiện & quan sát
def test_events_are_published_in_order(make_engine, make_call):
    eng = make_engine(1)
    seen = []
    eng.dispatcher.subscribe(None, lambda t, p: seen.append(t))
    eng.submit(make_call(time_call=1), T0)
    eng.tick(T0 + sec(1))
    order = [t for t in seen if t is not EventType.STATE_CHANGED]
    assert order == [EventType.CALL_RECEIVED, EventType.CALL_QUEUED, EventType.CALL_STARTED,
                     EventType.CALL_FINISHED]


def test_failing_handler_does_not_break_engine(make_engine, make_call, collect):
    eng = make_engine(1)
    def boom(t, p): raise RuntimeError("db down")
    eng.dispatcher.subscribe(EventType.CALL_FINISHED, boom)
    res = collect(eng)
    eng.submit(make_call(time_call=1), T0)
    eng.tick(T0 + sec(1))
    assert len(res) == 1 and eng.employees.get("E1").status is S.AVAILABLE


def test_snapshot_reflects_state(make_engine, make_call):
    eng = make_engine(3, max_queue=5)
    for _ in range(4):
        eng.submit(make_call(time_call=9), T0)
    snap = eng.snapshot()
    assert len(snap.waiting) == 1 and snap.service_queue == ()
    assert snap.working_count == 3 and snap.total_employees == 3
    assert all(e.status is S.BUSY and e.current_phone for e in snap.employees)
    eng.tick(T0 + sec(9))
    snap = eng.snapshot()
    assert snap.stats["Success"] == 3 and snap.waiting == ()


# ----------------------------------------------- mô phỏng ngẫu nhiên kiểm tra bất biến
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_random_simulation_keeps_invariants_and_conserves_calls(seed, make_engine, make_call, collect):
    rng = random.Random(seed)
    eng = make_engine(5, max_queue=15); res = collect(eng)
    now, submitted = T0, 0
    ids = list(eng.employees._d)

    def check():
        eng.queues.check_invariant()
        in_queue = set(eng.queues.service)
        avail = {e.employee_id for e in eng.employees.all() if e.status is S.AVAILABLE}
        busy = {e.employee_id for e in eng.employees.all() if e.status is S.BUSY}
        assert in_queue == avail                                  # chỉ nhân viên rảnh nằm trong hàng
        assert busy == {a.employee_id for a in eng.processor.active_calls()}
        assert not (in_queue & busy)                              # bận thì không nằm trong hàng
        assert len(res) + len(eng.queues.calling) + len(eng.processor) == submitted

    for _ in range(600):
        op = rng.random()
        now += sec(rng.uniform(0, 1.5))
        if op < 0.55:
            ev = rng.choice(list(CallEvent))
            t = 0 if ev is CallEvent.MISS else rng.uniform(0, 3)
            if eng.submit(make_call(ev, t), now) is not None:
                submitted += 1
        elif op < 0.75:
            eng.tick(now)
        elif op < 0.83:
            eng.set_break(rng.choice(ids))
        elif op < 0.91:
            eng.set_available(rng.choice(ids), now)
        elif op < 0.95:
            eng.set_offline(rng.choice(ids))
        else:
            waiting = eng.snapshot().waiting
            if waiting:
                eng.abandon_waiting(rng.choice(waiting).call_id, now)
        check()

    for i in ids:                                         # xả hết
        eng.set_available(i, now)
    for _ in range(50):
        now += sec(10)
        eng.tick(now)
    check()
    assert len(eng.queues.calling) == 0 and len(eng.processor) == 0
    assert len(res) == submitted
    assert len({r.call_id for r in res}) == submitted     # mỗi cuộc gọi đúng một kết quả


def test_reset_stats_clears_counters_but_keeps_queues_and_employees(make_engine, make_call):
    eng = make_engine(1)
    eng.submit(make_call(time_call=1), T0); eng.submit(make_call(time_call=1), T0)    # 1 đang gọi, 1 chờ
    eng.submit(make_call(CallEvent.MISS, 0), T0)
    eng.tick(T0 + sec(1))
    assert eng.stats["total"] >= 1
    waiting_before = len(eng.queues.calling)
    eng.reset_stats()
    assert eng.stats["total"] == 0 and all(v == 0 for v in eng.stats.values())
    assert len(eng.queues.calling) + len(eng.processor) == waiting_before + len(eng.processor)
    eng.tick(T0 + sec(5))
    assert eng.stats["total"] >= 1                                   # tiếp tục đếm từ 0
