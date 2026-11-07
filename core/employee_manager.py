from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from models import Employee, EmployeeStatus

# Get_log_command
log = logging.getLogger(__name__)

class EmployeeError(ValueError):
    pass

class EmployeeManager:
    
    def __init__(self, employees: Iterable[Employee] = ()) -> None:
        self._d: Dict[str, Employee] = {}
        for e in employees:
            self.add(e.employee_id, e.name, e.status)
            
    # ------------ Truy van
    def get(self, employee_id: str) -> Employee:
        try:
            return self._d[employee_id]
        except KeyError:
            raise EmployeeError(f"Nhân viên {employee_id!r} không tồn tại!") from None
            
    def all(self) -> List[Employee]:
        return list(self._d.values())
        
    def __contains__(self, employee_id: object) -> bool:
        return employee_id in self._d
    
    def __len__(self) -> int:
        return len(self._d)
    
    def count_by_status(self) -> Dict[EmployeeStatus, int]:
        out = {s: 0 for s in EmployeeStatus}
        for e in self._d.values():
            out[e.status] += 1
        return out
    
    # ----------------------------- Them / Xoa / Edit
    def add(self, employee_id: str, name: str, status: EmployeeStatus = EmployeeStatus.AVAILABLE) -> Employee:
        eid = str(employee_id).strip()
        if not eid or "|" in eid or any(c.isspace() for c in eid):
            raise EmployeeError(f"Mã nhân viên: {employee_id!r} không hợp lệ!")
        if eid in self._d:
            raise EmployeeError(f"Mã nhân viên: {employee_id!r} đã tồn tại! ")
        
        status = EmployeeStatus(status)
        if status == EmployeeStatus.BUSY:
            raise EmployeeError(f"Nhân viên vừa tạo cần làm việc ngay, không thể để trạng thái BUSY")
        emp = Employee(eid, str(name).strip() or eid, status)
        self._d[eid] = emp
        return emp
    
    def update(self, employee_id: str, name: str) -> Employee:
        emp = self.get(employee_id)
        name = str(name).strip()
        if not name or "|" in name:
            raise EmployeeError(f"Tên nhân viên không hợp lệ: {name!r}")
        emp.name = name
        return emp
    
    # -------------------- Change status
    def mark_busy(self, employee_id: str) -> Employee:
        emp = self.get(employee_id)
        if emp.status != EmployeeStatus.AVAILABLE:
            raise EmployeeError(f"Trạng thái của {employee_id} đang là {emp.status.value}, không thể gán cuộc gọi!")
        emp.status = EmployeeStatus.BUSY
        return emp
        
    def release(self, employee_id: str, to_break: bool = False) -> EmployeeStatus:
        emp = self.get(employee_id)
        if emp.status != EmployeeStatus.BUSY:
            raise EmployeeStatus(f"{employee_id} đang ở trạng thái {emp.status.value}, không phải BUSY!")
        if emp.pending_status is not None:
            emp.status, emp.pending_status = emp.pending_status, None
        elif to_break:
            emp.status = EmployeeStatus.BREAK
        else:
            emp.status = EmployeeStatus.AVAILABLE
        return emp.status
    
    def request_break(self, employee_id: str) -> bool:
        emp = self.get(employee_id)
        if emp.status == EmployeeStatus.AVAILABLE:
            emp.status = EmployeeStatus.BREAK
            return True
        if emp.status == EmployeeStatus.BUSY and emp.pending_status != EmployeeStatus.BREAK:
            emp.pending_status = EmployeeStatus.BREAK
            return True
        return False
    
    def request_offline(self, employee_id: str) -> bool:
        emp = self.get(employee_id)
        if emp.status in (EmployeeStatus.BREAK, EmployeeStatus.AVAILABLE):
            emp.status = EmployeeStatus.OFFLINE
            return True
        if emp.status == EmployeeStatus.BUSY and emp.pending_status != EmployeeStatus.OFFLINE:
            emp.pending_status = EmployeeStatus.OFFLINE
            return True
        return False
    
    def request_available(self, employee_id: str) -> bool:
        emp = self.get(employee_id)
        if emp.status in (EmployeeStatus.BREAK, EmployeeStatus.OFFLINE):
            emp.status = EmployeeStatus.AVAILABLE
            return True
        if emp.status == EmployeeStatus.BUSY and emp.pending_status is not None:
            emp.pending_status = None
            return True
        return False
    
    
    
    