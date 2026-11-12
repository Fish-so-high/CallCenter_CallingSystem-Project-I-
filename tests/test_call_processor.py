import pytest

from core.call_processor import CallProcessor, ProcessingError
from core.models import Call, CallEvent, CallStatus, CallTag
from tests.conftest import T0, sec


def mk(i, event=CallEvent.SUCCESS, t=1.0):
    return Call(f"id{i}", f"09{i:08d}", event, t, T0)


def test_start_sets_in_progress_and_end_time():
    p = CallProcessor()
    c = mk(1, t=2.5)
    a = p.start(c, "E1", T0)
    assert c.status is CallStatus.IN_PROGRESS and c.employee_id == "E1"
    assert a.end_at == T0 + sec(2.5)


def test_pop_due_ordered_by_end_time():
    p = CallProcessor()
    p.start(mk(1, t=3), "E1", T0)
    p.start(mk(2, t=1), "E2", T0)
    p.start(mk(3, t=2), "E3", T0)
    assert p.pop_due(T0) == []
    due = p.pop_due(T0 + sec(2))
    assert [a.call.call_id for a in due] == ["id2", "id3"]
    assert len(p) == 1


def test_employee_cannot_take_two_calls():
    p = CallProcessor()
    p.start(mk(1), "E1", T0)
    with pytest.raises(ProcessingError):
        p.start(mk(2), "E1", T0)


def test_call_cannot_be_started_twice():
    p = CallProcessor()
    c = mk(1)
    p.start(c, "E1", T0)
    with pytest.raises(ProcessingError):
        p.start(c, "E2", T0)


def test_miss_duration_forced_to_zero():
    p = CallProcessor()
    a = p.start(mk(1, CallEvent.MISS, t=5.0), "E1", T0)
    assert a.duration == 0.0 and a.end_at == T0


@pytest.mark.parametrize("event,status,tag", [
    (CallEvent.SUCCESS, CallStatus.COMPLETED, CallTag.SUCCESS),
    (CallEvent.MISS, CallStatus.MISSED, CallTag.MISS),
    (CallEvent.BREAK, CallStatus.ABANDONED, CallTag.BREAK),
])
def test_build_result(event, status, tag):
    p = CallProcessor()
    a = p.start(mk(1, event, 0.5), "E1", T0)
    r = p.build_result(a)
    assert (r.status, r.tag, r.employee_id) == (status, tag, "E1")
    assert r.started_at == T0 and r.ended_at == a.end_at


def test_shift_moves_deadlines():
    p = CallProcessor()
    p.start(mk(1, t=1), "E1", T0)
    p.shift(sec(10))
    assert p.pop_due(T0 + sec(5)) == []
    assert len(p.pop_due(T0 + sec(11))) == 1
