from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
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
    

# -------------------- Cau hinh Employee.txt ------------------- #

TEMPLATE = """\
#DANH SÁCH NHÂN VIÊN 
# - Syntax: "employee_id|employee_name[|status]"
# - Dòng trống và dòng bắt đầu bằng '#' sẽ bị bỏ qua.
# - Ví dụ : NV001|Họ và tên nhân viên
"""

@dataclass
class LoadResult:
    employees: List[Employee] = field(default_factory = list)
    errors: List[str] = field(default_factory = list)
    template_created: bool = False
    
def write_templete(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text(TEMPLATE, encoding = "utf-8")
    
def parse_employees(text: str) -> LoadResult:
    result = LoadResult()
    seen: set = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("\uffef")
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (2, 3) or not parts[0]:
            result.errors.append(f"Dòng {n}: sai định dạng! (Lỗi Syntax)")
            continue
        status = EmployeeStatus.AVAILABLE
        if len(parts) == 3 and parts[2]:
            try:
                status = EmployeeStatus(parts[2].upper())
            except ValueError:
                result.errors.append(f"Dòng {n}: Trạng thái {parts[2]!r} không hợp lệ!")
                continue
            if status == EmployeeStatus.BUSY:
                result.errors.append(f"Dòng {n}: Lần đầu khai báo không được nhận trạng thái BUSY!")
                continue
        if parts[0] in seen or any(c.isspace() for c in parts[0]):
            result.error.append(f"Dòng {n}: Mã nhân viên {parts[0]!r} đã tồn tại hoặc chứa khoảng trắng!")
            continue
        seen.add(parts[0])
        result.employees.append(Employee(parts[0], parts[1] or parts[0], status))
    return result

def load_employee(path: Path) -> LoadResult:
    path = Path(path)
    if not path.exists():
        write_templete(path)
        log.warning("Chưa tồn tại %s, đã tạo file mẫu. Hãy thêm nhân viên và chạy lại!", path)
        return LoadResult(template_created = True)
    result = parse_employees(path.read_text(encoding = "utf-8"))
    for err in result.errors:
        log.warning("%s: %s", path.name, err)
    return result

def save_employee(path: Path, employees: Iterable[Employee]) -> None:
    path = Path(path)
    path.parent.mkdir(parents = True, exist_ok = True)
    lines = [TEMPLATE.rstrip("\n").split("# - Ví dụ")[0].rstrip("\n")]
    for e in employees:
        status = EmployeeStatus.AVAILABLE if e.status == EmployeeStatus.BUSY else e.status
        lines.append(f"{e.employee_id}|{e.name}|{status.value}")
    fd, tmp = tempfile.mkstemp(dir = str(path.parent), suffix = ".tmp")
    try:
        with os.fdopen(fd, "w", encoding = "utf-8", newline = "\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    