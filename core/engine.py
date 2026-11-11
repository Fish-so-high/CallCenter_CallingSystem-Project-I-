from __future__ import annotations

import logging
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Optional

from .call_processor import ActiveCall, CallProcessor
from .employee_manager import EmployeeManager
from .event_dispatcher import EventDispatcher, EventType
from .models import (
    Call, CallEvent, CallResult, CallStatus, CallTag, Employee, EmployeeChange,
    EmployeeStatus, EmployeeView, EngineSnapshot, IncomingCall, WaitingCallView,
)
from .queue_manager import QueueManager

log = logging.getLogger(__name__)


class ReleasePolicy(str, Enum):
    REQUEUE = "requeue"      
    TO_BREAK = "to_break"   


@dataclass
class EngineConfig:
    max_queue_size: int = 100
    miss_release_policy: ReleasePolicy = ReleasePolicy.REQUEUE
    break_release_policy: ReleasePolicy = ReleasePolicy.REQUEUE
    recent_id_memory: int = 50_000  

    def __post_init__(self) -> None:
        if self.max_queue_size < 1:
            raise ValueError("max_queue_size phải >= 1")
        self.miss_release_policy = ReleasePolicy(self.miss_release_policy)
        self.break_release_policy = ReleasePolicy(self.break_release_policy)


class _RecentIds:
    def __init__(self, limit: int) -> None:
        self._d: "OrderedDict[str, None]" = OrderedDict()
        self._limit = max(1, limit)

    def add(self, key: str) -> bool:
        if key in self._d:
            return False
        self._d[key] = None
        if len(self._d) > self._limit:
            self._d.popitem(last=False)
        return True


class CallFlowEngine:
    def __init__(self, employees: Iterable[Employee] = (), config: Optional[EngineConfig] = None,
                 dispatcher: Optional[EventDispatcher] = None) -> None:
        self.config = config or EngineConfig()
        self.dispatcher = dispatcher or EventDispatcher()
        self._lock = threading.RLock()
        self.employees = EmployeeManager(employees)
        self.queues = QueueManager(self.config.max_queue_size)
        self.processor = CallProcessor()
        self._seen = _RecentIds(self.config.recent_id_memory)
        self._stats: Counter = Counter()
        self._paused = False
        self._paused_at: Optional[datetime] = None

        for emp in self.employees.all():
            if emp.status == EmployeeStatus.AVAILABLE:
                self.queues.enqueue_employee(emp.employee_id)
        log.info("Engine khởi tạo: %d nhân viên, sức chứa hàng đợi %d",
                 len(self.employees), self.config.max_queue_size)

    # ================================================================ cuộc gọi
    def submit(self, incoming: IncomingCall, now: Optional[datetime] = None) -> Optional[Call]:
        now = now or datetime.now()
        with self._lock:
            if not self._seen.add(incoming.call_id):
                log.warning("Bỏ qua cuộc gọi trùng call_id=%s (%s)", incoming.call_id, incoming.phone_number)
                return None
            call = Call.from_incoming(incoming)
            log.info("Nhận cuộc gọi %s (event=%s, time_call=%.2fs)", call.phone_number,
                     call.event.value, call.time_call)
            self.dispatcher.publish(EventType.CALL_RECEIVED, call)
            if not self.queues.enqueue_call(call):
                call.status = CallStatus.REJECTED
                call.ended_at = now
                log.warning("Từ chối cuộc gọi %s: Calling_queue đầy (%d)", call.phone_number,
                            self.queues.calling.capacity)
                self._record(CallResult.rejected(call, now))
            else:
                self.dispatcher.publish(EventType.CALL_QUEUED, call)
                self._dispatch(now)
            self.dispatcher.publish(EventType.STATE_CHANGED)
            return call

    def tick(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now()
        with self._lock:
            if self._paused:
                return 0
            finished = 0
            while True:
                self._dispatch(now)
                due = self.processor.pop_due(now)
                if not due:
                    break
                for active in due:
                    self._finish(active)
                    finished += 1
            if finished:
                self.dispatcher.publish(EventType.STATE_CHANGED)
            return finished

    def abandon_waiting(self, call_id: str, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        with self._lock:
            call = self.queues.calling.remove(call_id)
            if call is None:
                return False
            call.status = CallStatus.ABANDONED
            call.ended_at = now
            log.info("Khách %s cúp máy khi đang chờ", call.phone_number)
            self._record(CallResult.abandoned_waiting(call, now))
            self.dispatcher.publish(EventType.STATE_CHANGED)
            return True

    # ---------------------------------------------------------------- nội bộ
    def _dispatch(self, now: datetime) -> None:
        if self._paused:
            return
        while self.queues.has_pair():
            emp_id = self.queues.service.peek()
            emp = self.employees.get(emp_id)
            if emp.status != EmployeeStatus.AVAILABLE: 
                log.error("Nhân viên %s trong Service_queue nhưng đang %s, loại khỏi hàng", emp_id, emp.status.value)
                self.queues.service.pop()
                continue
            call, emp_id = self.queues.pop_pair()
            self.employees.mark_busy(emp_id)
            self.processor.start(call, emp_id, now)
            log.info("Phân phối %s cho nhân viên %s", call.phone_number, emp_id)
            self.dispatcher.publish(EventType.CALL_STARTED, call)
        self.queues.check_invariant()

    def _finish(self, active: ActiveCall) -> None:
        call = active.call
        result = CallProcessor.build_result(active)
        call.status = result.status
        call.ended_at = result.ended_at
        policy = {
            CallEvent.MISS: self.config.miss_release_policy,
            CallEvent.BREAK: self.config.break_release_policy,
        }.get(call.event, ReleasePolicy.REQUEUE)
        new_status = self.employees.release(active.employee_id, to_break=policy == ReleasePolicy.TO_BREAK)
        if new_status == EmployeeStatus.AVAILABLE:
            self.queues.enqueue_employee(active.employee_id)  
        else:
            log.info("Nhân viên %s chuyển sang %s sau cuộc gọi", active.employee_id, new_status.value)
            self.dispatcher.publish(
                EventType.EMPLOYEE_STATUS_CHANGED,
                EmployeeChange(active.employee_id, EmployeeStatus.BUSY, new_status, None))
        self._record(result)

    def _record(self, result: CallResult) -> None:
        self._stats[result.tag.value] += 1
        log.info("Kết thúc cuộc gọi %s: %s (nhân viên=%s, %.2fs)", result.phone_number,
                 result.tag.value, result.employee_id, result.duration_seconds)
        self.dispatcher.publish(EventType.CALL_FINISHED, result)

    # ================================================================ nhân viên
    def _employee_op(self, employee_id: str, op) -> bool:
        emp = self.employees.get(employee_id)
        before = (emp.status, emp.pending_status)
        changed = op(employee_id)
        if changed and (emp.status, emp.pending_status) != before:
            log.info("Nhân viên %s: %s -> %s (chờ: %s)", employee_id, before[0].value, emp.status.value,
                     emp.pending_status.value if emp.pending_status else "-")
            self.dispatcher.publish(
                EventType.EMPLOYEE_STATUS_CHANGED,
                EmployeeChange(employee_id, before[0], emp.status, emp.pending_status))
            self.dispatcher.publish(EventType.STATE_CHANGED)
        return changed

    def set_break(self, employee_id: str) -> bool:
        with self._lock:
            changed = self._employee_op(employee_id, self.employees.request_break)
            if self.employees.get(employee_id).status != EmployeeStatus.AVAILABLE:
                self.queues.service.remove(employee_id)
            return changed

    def set_offline(self, employee_id: str) -> bool:
        with self._lock:
            changed = self._employee_op(employee_id, self.employees.request_offline)
            if self.employees.get(employee_id).status != EmployeeStatus.AVAILABLE:
                self.queues.service.remove(employee_id)
            return changed

    def set_available(self, employee_id: str, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        with self._lock:
            changed = self._employee_op(employee_id, self.employees.request_available)
            if self.employees.get(employee_id).status == EmployeeStatus.AVAILABLE:
                self.queues.enqueue_employee(employee_id)
                self._dispatch(now)
            return changed

    def add_employee(self, employee_id: str, name: str, now: Optional[datetime] = None) -> Employee:
        now = now or datetime.now()
        with self._lock:
            emp = self.employees.add(employee_id, name)
            self.queues.enqueue_employee(emp.employee_id)
            log.info("Thêm nhân viên %s (%s)", emp.employee_id, emp.name)
            self._dispatch(now)
            self.dispatcher.publish(EventType.STATE_CHANGED)
            return emp

    def update_employee(self, employee_id: str, name: str) -> Employee:
        with self._lock:
            emp = self.employees.update(employee_id, name)
            self.dispatcher.publish(EventType.STATE_CHANGED)
            return emp

    # ============================================================ tạm dừng / tiếp tục
    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        with self._lock:
            if not self._paused:
                self._paused, self._paused_at = True, now
                log.info("Tạm dừng engine")

    def resume(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now()
        with self._lock:
            if not self._paused:
                return
            delta = now - (self._paused_at or now)
            self._paused, self._paused_at = False, None
            self.processor.shift(delta)
            log.info("Tiếp tục engine (đã dừng %.2fs)", delta.total_seconds())
            self._dispatch(now)
            self.dispatcher.publish(EventType.STATE_CHANGED)

    # ================================================================== quan sát
    @property
    def stats(self) -> dict:
        with self._lock:
            out = {t.value: self._stats.get(t.value, 0) for t in CallTag}
            out["total"] = sum(out.values())
            return out

    def reset_stats(self) -> None:
        with self._lock:
            self._stats.clear()
            self.dispatcher.publish(EventType.STATE_CHANGED)

    def snapshot(self) -> EngineSnapshot:
        with self._lock:
            waiting = tuple(WaitingCallView(c.call_id, c.phone_number, c.received_at)
                            for c in self.queues.calling)
            views = []
            for e in self.employees.all():
                active = self.processor.active_for(e.employee_id)
                views.append(EmployeeView(
                    e.employee_id, e.name, e.status, e.pending_status,
                    active.call.phone_number if active else None,
                    self.queues.service.position(e.employee_id)))
            working = sum(1 for e in self.employees.all()
                          if e.status in (EmployeeStatus.AVAILABLE, EmployeeStatus.BUSY))
            return EngineSnapshot(waiting, tuple(self.queues.service), tuple(views), self.stats,
                                  working, len(self.employees), self._paused)
