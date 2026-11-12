import pytest

from core import Employee, EmployeeStatus as S
from core.employee_manager import (
    EmployeeError, EmployeeManager, load_employees, parse_employees, save_employees,
)


@pytest.fixture
def mgr():
    return EmployeeManager([Employee("A", "An"), Employee("B", "Bình")])


def test_add_validates_and_rejects_duplicates(mgr):
    mgr.add("C", "Châu")
    for bad in ("", "  ", "A", "x|y", "has space"):
        with pytest.raises(EmployeeError):
            mgr.add(bad, "n")
    with pytest.raises(EmployeeError):
        mgr.add("D", "n", S.BUSY)
    assert len(mgr) == 3


def test_update_name(mgr):
    assert mgr.update("A", "An Mới").name == "An Mới"
    with pytest.raises(EmployeeError):
        mgr.update("Z", "x")


def test_busy_employee_cannot_be_marked_busy_again(mgr):
    mgr.mark_busy("A")
    with pytest.raises(EmployeeError):
        mgr.mark_busy("A")


def test_break_and_offline_employees_cannot_be_marked_busy(mgr):
    mgr.request_break("A"); mgr.request_offline("B")
    for e in ("A", "B"):
        with pytest.raises(EmployeeError):
            mgr.mark_busy(e)


def test_break_return_cycle(mgr):
    assert mgr.request_break("A") and mgr.get("A").status is S.BREAK
    assert mgr.request_break("A") is False
    assert mgr.request_available("A") and mgr.get("A").status is S.AVAILABLE
    assert mgr.request_available("A") is False


def test_busy_break_request_is_pending_until_release(mgr):
    mgr.mark_busy("A")
    assert mgr.request_break("A")
    assert mgr.get("A").status is S.BUSY and mgr.get("A").pending_status is S.BREAK
    assert mgr.release("A") is S.BREAK
    assert mgr.get("A").pending_status is None


def test_pending_can_be_cancelled(mgr):
    mgr.mark_busy("A"); mgr.request_offline("A")
    assert mgr.request_available("A")
    assert mgr.release("A") is S.AVAILABLE


def test_release_requires_busy(mgr):
    with pytest.raises(EmployeeError):
        mgr.release("A")


def test_offline_flag_and_reactivate(mgr):
    mgr.request_offline("A")
    assert mgr.get("A").is_active is False
    assert mgr.request_break("A") is False        # OFFLINE không thể sang BREAK
    assert mgr.request_available("A") and mgr.get("A").is_active


def test_parse_file_and_errors():
    text = "# c\n\nNV1|An\nNV2|Bình|break\nNV1|Trùng\nbad line\nNV3|X|BUSY\nNV4|Y|WRONG\n"
    r = parse_employees(text)
    assert [(e.employee_id, e.status) for e in r.employees] == [("NV1", S.AVAILABLE), ("NV2", S.BREAK)]
    assert len(r.errors) == 4


def test_missing_file_creates_template_without_employees(tmp_path):
    p = tmp_path / "data" / "employees.txt"
    r = load_employees(p)
    assert r.template_created and r.employees == [] and p.exists()
    assert "employee_id|employee_name" in p.read_text(encoding="utf-8")
    assert load_employees(p).employees == []       # file mẫu không chứa nhân viên nào


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "employees.txt"
    emps = [Employee("NV1", "Nguyễn An"), Employee("NV2", "Trần Bình", S.OFFLINE)]
    save_employees(p, emps)
    got = load_employees(p).employees
    assert [(e.employee_id, e.name, e.status) for e in got] == [
        ("NV1", "Nguyễn An", S.AVAILABLE), ("NV2", "Trần Bình", S.OFFLINE)]
