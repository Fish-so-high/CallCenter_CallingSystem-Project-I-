"""Công cụ dùng chung cho test: chờ điều kiện, backend CSDL giả có thể gây lỗi theo kịch bản."""
from __future__ import annotations

import time
from typing import Callable, List, Set

from core.models import CallResult, Employee
# from database import DatabaseUnavailableError, RepositoryError, StoredCall, UnknownEmployeeError


def wait_until(cond: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01, pump=None) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pump:
            pump()
        if cond():
            return True
        time.sleep(interval)
    if pump:
        pump()
    return bool(cond())


class FakeEmployees:
    def __init__(self, b: "FakeBackend") -> None:
        self.b = b

    def upsert_many(self, employees) -> int:
        self.b._check_alive()
        emps = list(employees)
        self.b.known |= {e.employee_id for e in emps}
        self.b.log.append(("sync", len(emps)))
        return len(emps)


class FakeCalls:
    def __init__(self, b: "FakeBackend") -> None:
        self.b = b

    def _one(self, r: CallResult) -> bool:
        if r.call_id in self.b.poison:
            raise RepositoryError("bản ghi vi phạm ràng buộc")
        if self.b.require_known and r.employee_id and r.employee_id not in self.b.known:
            raise UnknownEmployeeError("nhân viên chưa có")
        if r.call_id in self.b.ids:
            return False
        self.b.ids.add(r.call_id)
        self.b.saved.append(r)
        return True

    def save_many(self, results) -> int:
        self.b._check_alive()
        if self.b.fail_next > 0:
            self.b.fail_next -= 1
            raise DatabaseUnavailableError("mất kết nối giả lập")
        if self.b.delay:
            time.sleep(self.b.delay)
        results = list(results)
        snapshot_ids, snapshot_saved = set(self.b.ids), list(self.b.saved)
        try:                                  # atomic như thật: lỗi thì không lưu gì
            n = sum(1 for r in results if self._one(r))
        except Exception:
            self.b.ids, self.b.saved = snapshot_ids, snapshot_saved
            raise
        self.b.log.append(("save", len(results)))
        return n

    def save(self, r: CallResult) -> bool:
        self.b._check_alive()
        ok = self._one(r)
        self.b.log.append(("save", 1))
        return ok

    def iter_by_day(self, day):
        self.b._check_alive()
        for r in list(self.b.saved):
            if r.ended_at.date() == day:
                yield StoredCall(r.call_id, r.phone_number, r.employee_id, r.received_at, r.started_at,
                                 r.ended_at, r.duration_seconds, r.tag, r.ended_at)


class FakeBackend:
    """Thay thế Backend PostgreSQL trong test PersistenceService / GUI."""

    def __init__(self) -> None:
        self.saved: List[CallResult] = []
        self.ids: Set[str] = set()
        self.known: Set[str] = set()
        self.poison: Set[str] = set()
        self.log: list = []
        self.fail_next = 0          # số lần save_many tới sẽ ném DatabaseUnavailableError
        self.alive = True
        self.require_known = False
        self.employees = FakeEmployees(self)
        self.calls = FakeCalls(self)
        self.closed = False
        self.delay = 0.0            # giả lập CSDL chậm (giây mỗi lần save_many)

    def _check_alive(self) -> None:
        if not self.alive:
            raise DatabaseUnavailableError("PostgreSQL tắt")

    def check(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed = True
