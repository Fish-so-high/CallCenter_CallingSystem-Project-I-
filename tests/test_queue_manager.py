import pytest

from core.models import Call, CallEvent
from core.queue_manager import QueueInvariantError, QueueManager
from tests.conftest import T0


def mk(i):
    return Call(f"id{i}", f"09{i:08d}", CallEvent.SUCCESS, 1.0, T0)


def test_calling_queue_is_fifo():
    q = QueueManager(10)
    for i in range(3):
        assert q.enqueue_call(mk(i))
    assert [q.calling.pop().call_id for _ in range(3)] == ["id0", "id1", "id2"]


def test_service_queue_is_fifo_and_no_duplicates():
    q = QueueManager()
    for e in ("A", "B", "C"):
        assert q.enqueue_employee(e)
    assert q.enqueue_employee("A") is False
    assert [q.service.pop() for _ in range(3)] == ["A", "B", "C"]


def test_capacity_rejects_when_full():
    q = QueueManager(2)
    assert q.enqueue_call(mk(1)) and q.enqueue_call(mk(2))
    assert q.enqueue_call(mk(3)) is False
    assert len(q.calling) == 2


def test_same_call_cannot_be_queued_twice():
    q = QueueManager(5)
    c = mk(1)
    assert q.enqueue_call(c) and q.enqueue_call(c) is False


def test_remove_from_middle_compacts_queue():
    q = QueueManager(10)
    for i in range(4):
        q.enqueue_call(mk(i))
    assert q.calling.remove("id1").call_id == "id1"
    assert q.calling.remove("id1") is None
    assert [c.call_id for c in q.calling] == ["id0", "id2", "id3"]
    q.enqueue_employee("A"); q.enqueue_employee("B"); q.enqueue_employee("C")
    assert q.service.remove("B") and not q.service.remove("B")
    assert list(q.service) == ["A", "C"] and q.service.position("C") == 2


def test_pop_pair_takes_heads():
    q = QueueManager()
    q.enqueue_call(mk(1)); q.enqueue_call(mk(2))
    q.enqueue_employee("A"); q.enqueue_employee("B")
    call, emp = q.pop_pair()
    assert (call.call_id, emp) == ("id1", "A")
    assert len(q.calling) == 1 and list(q.service) == ["B"]


def test_pop_pair_requires_both():
    q = QueueManager()
    q.enqueue_call(mk(1))
    with pytest.raises(IndexError):
        q.pop_pair()


def test_invariant_detects_both_non_empty():
    q = QueueManager()
    q.enqueue_call(mk(1)); q.enqueue_employee("A")
    with pytest.raises(QueueInvariantError):
        q.check_invariant()


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        QueueManager(0)
