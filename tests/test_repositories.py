"""Test repository trên PostgreSQL thật (cần TEST_DATABASE_URL)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from core import CallTag, Employee, EmployeeStatus as S
from database import RepositoryError, UnknownEmployeeError
from tests.conftest import VN, make_result


def count(db, sql="SELECT count(*) FROM call_records"):
    with db.engine.connect() as c:
        return c.execute(text(sql)).scalar()


# ------------------------------------------------------------------ nhân viên
def test_employee_upsert_get_update(repos):
    assert repos.employees.get("E1").name == "Nhân viên 1"
    repos.employees.upsert_many([Employee("E1", "Tên mới"), Employee("E3", "Ba", S.BREAK)])
    assert repos.employees.get("E1").name == "Tên mới"
    assert [e.employee_id for e in repos.employees.list_all()] == ["E1", "E2", "E3"]
    assert repos.employees.get("E3").status is S.BREAK
    assert repos.employees.get("ZZZ") is None


def test_employee_set_status_updates_is_active(repos, db):
    assert repos.employees.set_status("E1", S.OFFLINE)
    assert repos.employees.get("E1").status is S.OFFLINE
    assert count(db, "SELECT is_active FROM employees WHERE employee_id='E1'") is False
    assert repos.employees.set_status("E1", S.AVAILABLE)
    assert count(db, "SELECT is_active FROM employees WHERE employee_id='E1'") is True
    assert repos.employees.set_status("ZZZ", S.BREAK) is False


def test_upsert_empty_is_noop(repos):
    assert repos.employees.upsert_many([]) == 0


# ------------------------------------------------------------- ghi cuộc gọi
def test_save_success_round_trip_and_timezone(repos):
    r = make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 29, 10, 0, 5), duration=2.5)
    assert repos.calls.save(r) is True
    [got] = repos.calls.list_by_day(date(2026, 10, 29))
    assert (got.call_id, got.phone_number, got.employee_id, got.tag) == (r.call_id, r.phone_number, "E1", CallTag.SUCCESS)
    assert got.duration_seconds == 2.5
    assert got.ended_at == datetime(2026, 10, 29, 10, 0, 5, tzinfo=VN) and got.ended_at.utcoffset() == timedelta(hours=7)
    assert got.started_at == datetime(2026, 10, 29, 10, 0, 2, 500000, tzinfo=VN)
    assert got.recorded_at is not None


@pytest.mark.parametrize("tag", [CallTag.MISS, CallTag.BREAK])
def test_miss_and_break_saved_but_not_counted_in_stats(repos, tag):
    assert repos.calls.save(make_result(tag, "E1", duration=0.4 if tag is CallTag.BREAK else 0))
    assert repos.stats.list_month("2026-10") == []


@pytest.mark.parametrize("tag", [CallTag.REJECTED, CallTag.ABANDONED])
def test_rejected_and_abandoned_have_no_employee(repos, tag):
    assert repos.calls.save(make_result(tag))
    [got] = repos.calls.list_by_day(date(2026, 10, 29))
    assert got.employee_id is None and got.started_at is None and got.tag is tag
    assert repos.stats.list_month("2026-10") == []


def test_monthly_stats_accumulate_in_same_transaction(repos):
    for secs in (1.5, 2.0, 0.25):
        repos.calls.save(make_result(CallTag.SUCCESS, "E1", duration=secs))
    repos.calls.save(make_result(CallTag.SUCCESS, "E2", duration=3))
    s1 = repos.stats.get("E1", "2026-10")
    assert (s1.successful_calls, s1.total_talk_seconds) == (3, 3.75)
    assert repos.stats.get("E2", "2026-10").successful_calls == 1
    assert repos.stats.get("E1", "2026-11") is None


def test_duplicate_call_is_ignored_and_not_double_counted(repos, db):
    r = make_result(CallTag.SUCCESS, "E1", duration=2, call_id="dup-1")
    assert repos.calls.save(r) is True
    assert repos.calls.save(r) is False
    assert count(db) == 1
    assert repos.stats.get("E1", "2026-10").successful_calls == 1


def test_unknown_employee_raises_and_stores_nothing(repos, db):
    with pytest.raises(UnknownEmployeeError):
        repos.calls.save(make_result(CallTag.SUCCESS, "GHOST"))
    assert count(db) == 0 and count(db, "SELECT count(*) FROM employee_monthly_stats") == 0


def test_save_many_is_atomic(repos, db):
    batch = [make_result(CallTag.SUCCESS, "E1"), make_result(CallTag.SUCCESS, "E2"),
             make_result(CallTag.SUCCESS, "GHOST")]
    with pytest.raises(RepositoryError):
        repos.calls.save_many(batch)
    assert count(db) == 0 and repos.stats.list_month("2026-10") == []   # không ghi một nửa


def test_save_many_counts_only_new_rows(repos):
    a, b = make_result(), make_result()
    assert repos.calls.save_many([a, b]) == 2
    assert repos.calls.save_many([a, b, make_result()]) == 1
    assert repos.calls.save_many([]) == 0


def test_duration_is_rounded_to_milliseconds(repos):
    repos.calls.save(make_result(duration=1.23456789))
    assert repos.calls.list_by_day(date(2026, 10, 29))[0].duration_seconds == 1.235


# ------------------------------------------------------------- truy vấn theo ngày
def test_day_boundaries_use_local_midnight_and_end_time(repos):
    cases = {
        "before":   datetime(2026, 10, 28, 23, 59, 59),    # ngày 28
        "midnight": datetime(2026, 10, 29, 0, 0, 0),       # ngày 29 (nửa đêm thuộc ngày mới)
        "late":     datetime(2026, 10, 29, 23, 59, 59),    # ngày 29
        "next":     datetime(2026, 10, 30, 0, 0, 0),       # ngày 30
    }
    ids = {}
    for name, ended in cases.items():
        r = make_result(CallTag.MISS, "E1", ended=ended, duration=0, call_id=name)
        ids[name] = r.call_id
        repos.calls.save(r)
    # gọi nhận 23:59:59 ngày 28, kết thúc 00:00:01 ngày 29 -> tính vào ngày kết thúc (29)
    repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 29, 0, 0, 1), duration=2,
                                 received=datetime(2026, 10, 28, 23, 59, 59), call_id="cross"))
    got = {d: {c.call_id for c in repos.calls.list_by_day(date(2026, 10, d))} for d in (28, 29, 30)}
    assert got[28] == {"before"} and got[30] == {"next"}
    assert got[29] == {"midnight", "late", "cross"}
    assert repos.calls.count_by_day(date(2026, 10, 29)) == 3


def test_iter_by_day_paginates_with_ties_without_loss_or_duplicates(repos):
    same_time = datetime(2026, 10, 29, 12, 0, 0)
    ids = set()
    for i in range(25):    # nhiều bản ghi cùng thời điểm: kiểm tra tie-break theo call_id
        r = make_result(CallTag.MISS, "E1", ended=same_time, duration=0, call_id=f"id{i:03d}")
        repos.calls.save(r); ids.add(r.call_id)
    repos.calls.save(make_result(CallTag.MISS, "E1", ended=same_time + timedelta(seconds=1), duration=0, call_id="zz"))
    seen = [c.call_id for c in repos.calls.iter_by_day(date(2026, 10, 29), page_size=7)]
    assert len(seen) == 26 and set(seen) == ids | {"zz"} and len(set(seen)) == 26
    assert seen == sorted(seen[:25]) + ["zz"]                 # có thứ tự ổn định


def test_empty_day(repos):
    assert repos.calls.list_by_day(date(2030, 1, 1)) == [] and repos.calls.count_by_day(date(2030, 1, 1)) == 0


# ---------------------------------------------------------------- thống kê tháng
def test_month_boundary_in_local_time(repos):
    repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 31, 23, 59, 59), duration=1))
    repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 11, 1, 0, 0, 0), duration=2))
    assert repos.stats.get("E1", "2026-10").total_talk_seconds == 1
    assert repos.stats.get("E1", "2026-11").total_talk_seconds == 2


def test_rebuild_month_repairs_drift(repos, db):
    for e, secs in (("E1", 1.5), ("E1", 2.0), ("E2", 4.0)):
        repos.calls.save(make_result(CallTag.SUCCESS, e, duration=secs))
    repos.calls.save(make_result(CallTag.MISS, "E2", duration=0))
    expected = repos.stats.list_month("2026-10")
    with db.engine.begin() as c:
        c.execute(text("UPDATE employee_monthly_stats SET successful_calls = 999"))
    assert repos.stats.rebuild_month("2026-10") == 2
    assert repos.stats.list_month("2026-10") == expected
    # tháng khác không bị ảnh hưởng
    repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 12, 5, 9, 0, 0), duration=3))
    repos.stats.rebuild_month("2026-10")
    assert repos.stats.get("E1", "2026-12").successful_calls == 1
