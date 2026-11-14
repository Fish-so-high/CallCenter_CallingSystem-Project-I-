import csv
from datetime import date, datetime

import pytest

from core import CallTag
from database import StoredCall
from storage import ExportError
from storage.archive_service import ArchiveService
from storage.scheduler import RunContext, TaskStatus
from tests.conftest import VN

D = date(2026, 10, 29)


def stored(i, day=D, h=10):
    t = datetime(day.year, day.month, day.day, h, 0, i % 60, tzinfo=VN)
    return StoredCall(f"id{i}", f"09{i:08d}", "E1", t, t, t, 1.0, CallTag.SUCCESS, t)


class Source:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def iter_by_day(self, day):
        return iter([r for r in self.rows if r.ended_at.date() == day])


def ctx(day=D, attempt=1, catch_up=False, task="daily-export"):
    return RunContext(task, day, datetime(2026, 10, 29, 19, tzinfo=VN), datetime(2026, 10, 29, 19, tzinfo=VN),
                      attempt, catch_up)


def make(tmp_path, rows=(), idle=True, source=True):
    src = Source(rows)
    svc = ArchiveService(lambda: src if source else None, lambda: tmp_path, lambda timeout: idle)
    return svc, src


def read(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_daily_export_writes_csv_for_the_day(tmp_path):
    svc, _ = make(tmp_path, [stored(i) for i in range(5)] + [stored(9, day=date(2026, 10, 30))])
    out = svc.daily_export(ctx())
    assert out.status is TaskStatus.OK and out.rows == 5 and out.path == tmp_path / "calls-29-10-2026.csv"
    assert len(read(out.path)) == 6 and "5 dòng" in out.message


def test_export_overwrites_earlier_snapshot_with_latest_data(tmp_path):
    svc, src = make(tmp_path, [stored(i) for i in range(3)])
    svc.daily_export(ctx())
    src.rows += [stored(i, h=23) for i in range(10, 14)]
    out = svc.close_day(ctx(task="day-rollover"))
    assert out.rows == 7 and len(read(out.path)) == 8 and "ghi đè" in out.message
    assert [p.name for p in tmp_path.iterdir()] == ["calls-29-10-2026.csv"]


def test_empty_day_is_skipped_without_creating_or_touching_files(tmp_path):
    svc, _ = make(tmp_path, [])
    out = svc.daily_export(ctx())
    assert out.status is TaskStatus.SKIPPED and list(tmp_path.iterdir()) == []
    (tmp_path / "calls-29-10-2026.csv").write_text("cũ", encoding="utf-8")
    assert svc.daily_export(ctx()).status is TaskStatus.SKIPPED
    assert (tmp_path / "calls-29-10-2026.csv").read_text(encoding="utf-8") == "cũ"


def test_can_export_empty_days_when_enabled(tmp_path):
    svc, _ = make(tmp_path, [])
    svc.export_empty_days = True
    out = svc.daily_export(ctx())
    assert out.status is TaskStatus.OK and out.rows == 0 and len(read(out.path)) == 1


def test_database_unavailable_raises_so_scheduler_retries(tmp_path):
    svc, _ = make(tmp_path, [stored(1)], source=False)
    out = svc.daily_export(ctx())
    assert out.status is TaskStatus.FAILED and "PostgreSQL" in out.message      # FAILED (không ném) -> không in traceback
    assert list(tmp_path.iterdir()) == []


def test_refuses_to_export_while_results_are_still_being_written(tmp_path):
    svc, _ = make(tmp_path, [stored(1)], idle=False)
    out = svc.daily_export(ctx())
    assert out.status is TaskStatus.FAILED and "chưa ghi hết" in out.message
    assert list(tmp_path.iterdir()) == []                      # không bao giờ xuất file thiếu dữ liệu


def test_file_write_error_is_reported(tmp_path):
    blocker = tmp_path / "x"; blocker.write_text("file chứ không phải thư mục")
    src = Source([stored(1)])
    svc = ArchiveService(lambda: src, lambda: blocker, lambda t: True)
    out = svc.daily_export(ctx())
    assert out.status is TaskStatus.FAILED and out.message


def test_unexpected_bug_still_propagates_with_traceback(tmp_path):
    class Boom:
        def iter_by_day(self, day):
            raise ZeroDivisionError("lỗi lập trình")
    svc = ArchiveService(lambda: Boom(), lambda: tmp_path, lambda t: True)
    out = svc.daily_export(ctx())                              # export_day đổi mọi lỗi ghi thành ExportError
    assert out.status is TaskStatus.FAILED and "lỗi lập trình" in out.message


# ------------------------------------------------------------------- phân kỳ
def test_close_day_notifies_new_day_once_on_first_on_time_attempt(tmp_path):
    svc, _ = make(tmp_path, [stored(1)]); closed = []
    svc.on_day_closed.append(closed.append)
    svc.close_day(ctx(attempt=1))
    svc.close_day(ctx(attempt=2))                              # thử lại (xuất lỗi lần trước): không mở ngày mới lần nữa
    svc.close_day(ctx(attempt=1, catch_up=True))               # chạy bù khi mở lại app: không reset màn hình
    assert closed == [D]


def test_new_day_opens_even_if_export_fails_and_failed_export_is_retried(tmp_path):
    svc, _ = make(tmp_path, [stored(1)], source=False); closed = []
    svc.on_day_closed.append(closed.append)
    assert svc.close_day(ctx(attempt=1)).status is TaskStatus.FAILED
    assert closed == [D]                                       # 00:00 vẫn sang ngày mới dù xuất chưa được


def test_handler_error_does_not_break_export(tmp_path):
    svc, _ = make(tmp_path, [stored(1)])
    svc.on_day_closed.append(lambda d: 1 / 0)
    assert svc.close_day(ctx()).status is TaskStatus.OK
