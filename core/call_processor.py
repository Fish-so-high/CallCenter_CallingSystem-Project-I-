from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .models import EVENT_OUTCOME, Call, CallEvent, CallResult, CallStatus


class ProcessingError(RuntimeError):
    pass


@dataclass(eq=False)
class ActiveCall:
    call: Call
    employee_id: str
    started_at: datetime
    end_at: datetime
    duration: float


def effective_duration(call: Call) -> float:
    return 0.0 if call.event == CallEvent.MISS else call.time_call


class CallProcessor:
    def __init__(self) -> None:
        self._active: Dict[str, ActiveCall] = {}      
        self._by_employee: Dict[str, ActiveCall] = {}    
        self._heap: List[Tuple[datetime, int, str]] = []
        self._seq = itertools.count()

    def start(self, call: Call, employee_id: str, now: datetime) -> ActiveCall:
        if call.call_id in self._active:
            raise ProcessingError(f"Cuộc gọi {call.call_id} đã đang được xử lý")
        if employee_id in self._by_employee:
            raise ProcessingError(f"Nhân viên {employee_id} đang xử lý một cuộc gọi khác")
        duration = effective_duration(call)
        active = ActiveCall(call, employee_id, now, now + timedelta(seconds=duration), duration)
        call.status = CallStatus.IN_PROGRESS
        call.employee_id = employee_id
        call.started_at = now
        self._active[call.call_id] = active
        self._by_employee[employee_id] = active
        heapq.heappush(self._heap, (active.end_at, next(self._seq), call.call_id))
        return active

    def pop_due(self, now: datetime) -> List[ActiveCall]:
        due: List[ActiveCall] = []
        while self._heap and self._heap[0][0] <= now:
            _, _, call_id = heapq.heappop(self._heap)
            active = self._active.pop(call_id)
            del self._by_employee[active.employee_id]
            due.append(active)
        return due

    @staticmethod
    def build_result(active: ActiveCall) -> CallResult:
        status, tag = EVENT_OUTCOME[active.call.event]
        call = active.call
        return CallResult(call.call_id, call.phone_number, active.employee_id, call.received_at,
                          active.started_at, active.end_at, active.duration, status, tag)

    def shift(self, delta: timedelta) -> None:
        for a in self._active.values():
            a.started_at += delta
            a.end_at += delta
            a.call.started_at = a.started_at
        self._heap = [(a.end_at, next(self._seq), cid) for cid, a in self._active.items()]
        heapq.heapify(self._heap)

    def active_for(self, employee_id: str) -> Optional[ActiveCall]:
        return self._by_employee.get(employee_id)

    def active_calls(self) -> List[ActiveCall]:
        return list(self._active.values())

    def __len__(self) -> int:
        return len(self._active)

    def __contains__(self, call_id: object) -> bool:
        return call_id in self._active
