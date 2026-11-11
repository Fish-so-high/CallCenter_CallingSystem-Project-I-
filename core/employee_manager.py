from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List

from .models import Employee, EmployeeStatus

log = logging.getLogger(__name__)


class EmployeeError(ValueError):
    pass


class EmployeeManager:
    def __init__(self, employees: Iterable[Employee] = ()) -> None:
        self._d: Dict[str, Employee] = {}
        for e in employees:
            self.add(e.employee_id, e.name, e.status)

    # ------------------------------------------------------------- truy vấn
    def get(self, employee_id: str) -> Employee:
        try:
            return self._d[employee_id]
        except KeyError:
            raise EmployeeError(f"Nhân viên không tồn tại: {employee_id!r}") from None

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

    # ---------------------------------------------------- thêm / chỉnh sửa
    def add(self, employee_id: str, name: str,
            status: EmployeeStatus = EmployeeStatus.AVAILABLE) -> Employee:
        eid = str(employee_id).strip()
        if not eid or "|" in eid or any(c.isspace() for c in eid):
            raise EmployeeError(f"Mã nhân viên không hợp lệ: {employee_id!r}")
        if eid in self._d:
            raise EmployeeError(f"Mã nhân viên đã tồn tại: {eid!r}")
        status = EmployeeStatus(status)
        if status == EmployeeStatus.BUSY:
            raise EmployeeError("Không thể tạo nhân viên ở trạng thái BUSY")
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

    # -------------------------------------------------- chuyển trạng thái
    def mark_busy(self, employee_id: str) -> Employee:
        emp = self.get(employee_id)
        if emp.status != EmployeeStatus.AVAILABLE:
            raise EmployeeError(f"Không thể gán cuộc gọi cho {employee_id}: trạng thái {emp.status.value}")
        emp.status = EmployeeStatus.BUSY
        return emp

    def release(self, employee_id: str, to_break: bool = False) -> EmployeeStatus:
        emp = self.get(employee_id)
        if emp.status != EmployeeStatus.BUSY:
            raise EmployeeError(f"{employee_id} không ở trạng thái BUSY (đang {emp.status.value})")
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
        return False  # đã BREAK, OFFLINE, hoặc đã có yêu cầu tương tự

    def request_offline(self, employee_id: str) -> bool:
        emp = self.get(employee_id)
        if emp.status in (EmployeeStatus.AVAILABLE, EmployeeStatus.BREAK):
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
            emp.pending_status = None  # hủy yêu cầu nghỉ đang chờ
            return True
        return False


# ------------------------------------------------------------------ file TXT
TEMPLATE = """\
# DANH SÁCH NHÂN VIÊN
# Mỗi dòng khai báo một nhân viên theo định dạng: employee_id|employee_name[|status]
#   - employee_id  : mã duy nhất, không chứa ký tự '|' hoặc khoảng trắng
#   - employee_name: tên hiển thị
#   - status       : (không bắt buộc) AVAILABLE (mặc định), BREAK hoặc OFFLINE
# Ví dụ: NV001|Họ và tên nhân viên
"""


@dataclass
class LoadResult:
    employees: List[Employee] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    template_created: bool = False


def write_template(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")


def parse_employees(text: str) -> LoadResult:
    result = LoadResult()
    seen: set = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (2, 3) or not parts[0]:
            result.errors.append(f"Dòng {n}: sai định dạng, cần employee_id|employee_name[|status]: {raw!r}")
            continue
        status = EmployeeStatus.AVAILABLE
        if len(parts) == 3 and parts[2]:
            try:
                status = EmployeeStatus(parts[2].upper())
            except ValueError:
                result.errors.append(f"Dòng {n}: trạng thái không hợp lệ {parts[2]!r}")
                continue
            if status == EmployeeStatus.BUSY:
                result.errors.append(f"Dòng {n}: không được khai báo trạng thái BUSY")
                continue
        if parts[0] in seen or any(c.isspace() for c in parts[0]):
            result.errors.append(f"Dòng {n}: mã trùng hoặc chứa khoảng trắng: {parts[0]!r}")
            continue
        seen.add(parts[0])
        result.employees.append(Employee(parts[0], parts[1] or parts[0], status))
    return result


def load_employees(path: Path) -> LoadResult:
    path = Path(path)
    if not path.exists():
        write_template(path)
        log.warning("Chưa có %s, đã tạo file mẫu. Hãy khai báo nhân viên rồi chạy lại.", path)
        return LoadResult(template_created=True)
    result = parse_employees(path.read_text(encoding="utf-8"))
    for err in result.errors:
        log.warning("%s: %s", path.name, err)
    return result


def save_employees(path: Path, employees: Iterable[Employee]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [TEMPLATE.rstrip("\n").split("# Ví dụ")[0].rstrip("\n")]
    for e in employees:
        status = EmployeeStatus.AVAILABLE if e.status == EmployeeStatus.BUSY else e.status
        lines.append(f"{e.employee_id}|{e.name}|{status.value}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
