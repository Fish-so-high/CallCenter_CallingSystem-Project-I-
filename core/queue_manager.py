from __future__ import annotations

from collections import deque
from typing import Deque, Iterator, List, Optional, Set, Tuple

from .models import Call

class QueueInvarientError(RuntimeError):
    pass

#-------------- Queue cuoc goi --------------#
class CallingQueue:
    
    def __init__(self, capacity: int) -> None:
        self._d: Deque[Call] = deque()
        self._ids: Set[str] = set()
        self._capacity = 0
        self._capacity = capacity
        
    @property
    def capacity(self) -> int:
        return self._capacity
    
    @capacity.setter
    def capacity(self, value: int) -> None:
        if int(value) < 1:
            raise ValueError("Size của Calling_queue không hợp lệ!")
        self.capacity = int(value)
        
    def is_full(self) -> bool:
        return len(self._d) >= self._capacity
    
    def push(self, call: Call) -> bool:
        if self.is_full() or call.call_id in self._ids:
            return False
        self._d.append(call)
        self._ids.add(call.call_id)
        return True
    
    def pop(self) -> Call:
        call = self._d.popleft()
        self._ids.discard(call.call_id)
        return call
    
    def peek(self) -> Call:
        return self._d[0]
    
    def remove(self, call_id: str) -> Optional[Call]:
        if call_id not in self._ids:
            return None
        for call in self._d:
            if call.call_id == call_id:
                self._d.remove(call)
                self._ids.discard(call_id)
                return call
        return None
    
    def __len__(self) -> int:
        return len(self._d)
    
    def __iter__(self) -> Iterator[Call]:
        return iter(list(self._d))
    
    def __contains__(self, call_id: object) -> bool:
        return call_id in self._ids

#-------------- Queue nhan vien --------------#
class ServiceQueue:
    def __init__(self) -> None:
        self._d: Deque[str] = deque()
        self._ids: Set[str] = set()
        
    def push(self, employee_id: str) -> bool:
        if employee_id in self._ids:
            return False
        self._d.append(employee_id)
        self._ids.add(employee_id)
        return True
    
    def pop(self) -> str:
        eid = self._d.popleft()
        self._ids.discard(eid)
        return eid
    
    def peek(self) -> str:
        return self._d[0]
    
    def remove(self, employee_id: str) -> bool:
        if employee_id not in self._ids:
            return False
        self._d.remove(employee_id)
        self._ids.discard(employee_id)
        return True
    
    def position(self, employee_id: str) -> Optional[int]:
        if employee_id not in self._ids:
            return False
        
    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._d))

    def __contains__(self, employee_id: object) -> bool:
        return employee_id in self._ids

#-------------- Dieu phoi queue --------------#
class QueueManager:
    
    def __init__(self, calling_capacity: int = 100) -> None:
        self.calling = CallingQueue(calling_capacity)
        self.service = ServiceQueue()
        
    def enqueue_call(self, call : Call) -> bool:
        return self.calling.push(call)
    
    def enqueue_employee(self, employee_id: str) -> bool:
        return self.service.push(employee_id)
    
    def has_pair(self) -> bool:
        return len(self.calling) > 0 and len(self.service) > 0
    
    def pop_pair(self):
        if not self.has_pair():
            raise IndexError("Không thể ghép cuộc gọi với nhân viên!")
        return self.calling.pop(), self.service.pop()
    
    def waiting_list(self) -> List[Call]:
        return list(self.calling)
    
    def check_invariant(self) -> None:
        if len(self.calling) and len(self.service):
            raise QueueInvarientError (f"Lỗi cả 2 hàng chờ đều không rỗng!")
    
    
    
    
    