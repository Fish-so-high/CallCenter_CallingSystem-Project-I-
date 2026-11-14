import csv
from datetime import date, datetime

import pytest

from core import CallTag
from database import StoredCall
from storage import ExportError, OnExists, csv_filename, export_day
from storage.csv_exporter import HEADERS
from tests.conftest import VN, make_result

DAY = date(2026, 10, 29)


def stored(i=1, emp="E1", tag=CallTag.SUCCESS, dur=2.5, ended=datetime(2026, 10, 29, 10, 0, 5, tzinfo=VN)):
    return StoredCall(f"id{i}", f"09{i:08d}", emp, ended, ended, ended, dur, tag, ended)


class FakeSource:
    def __init__(self, rows, boom=False):
        self.rows, self.boom = rows, boom

    def iter_by_day(self, day):
        for n, r in enumerate(self.rows):
            if self.boom and n == 1:
                raise RuntimeError("DB mất kết nối giữa chừng")
            yield r


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_filename_format():
    assert csv_filename(date(2026, 1, 5)) == "calls-05-01-2026.csv"


def test_columns_and_rows(tmp_path):
    res = export_day(FakeSource([stored(1), stored(2, None, CallTag.REJECTED, 0)]), DAY, tmp_path)
    assert res.path == tmp_path / "calls-29-10-2026.csv" and res.rows == 2 and res.written and not res.existed
    rows = read(res.path)
    assert rows[0] == list(HEADERS) and len(HEADERS) == 6
    assert rows[1] == ["0900000001", "E1", "2026-10-29 10:00:05", "2026-10-29 10:00:05", "2.50", "Success"]
    assert rows[2][1] == "" and rows[2][5] == "Rejected"


def test_headers_are_vietnamese_per_spec():
    assert HEADERS[:4] == ("Số điện thoại", "Nhân viên trả lời", "Thời gian nhận cuộc gọi",
                           "Thời gian cuộc gọi kết thúc")
    assert HEADERS[5] == "Tag"


def test_file_has_utf8_bom_for_excel(tmp_path):
    p = export_day(FakeSource([]), DAY, tmp_path).path
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")


def test_empty_day_writes_header_only(tmp_path):
    res = export_day(FakeSource([]), DAY, tmp_path)
    assert res.rows == 0 and len(read(res.path)) == 1


def test_creates_missing_directory(tmp_path):
    out = tmp_path / "a" / "b"
    assert export_day(FakeSource([stored()]), DAY, out).path.exists()


def test_overwrite_replaces_with_latest_and_leaves_no_temp_files(tmp_path):
    export_day(FakeSource([stored(1)]), DAY, tmp_path)
    res = export_day(FakeSource([stored(1), stored(2)]), DAY, tmp_path)
    assert res.existed and res.written and len(read(res.path)) == 3
    assert [p.name for p in tmp_path.iterdir()] == ["calls-29-10-2026.csv"]


def test_skip_keeps_existing_file(tmp_path):
    first = export_day(FakeSource([stored(1)]), DAY, tmp_path).path
    before = first.read_bytes()
    res = export_day(FakeSource([stored(1), stored(2)]), DAY, tmp_path, on_exists=OnExists.SKIP)
    assert res.existed and not res.written and first.read_bytes() == before


def test_error_policy_raises_and_keeps_file(tmp_path):
    first = export_day(FakeSource([stored(1)]), DAY, tmp_path).path
    before = first.read_bytes()
    with pytest.raises(ExportError, match="đã tồn tại"):
        export_day(FakeSource([]), DAY, tmp_path, on_exists=OnExists.ERROR)
    assert first.read_bytes() == before


def test_failure_midway_keeps_old_file_and_cleans_temp(tmp_path):
    first = export_day(FakeSource([stored(1)]), DAY, tmp_path).path
    before = first.read_bytes()
    with pytest.raises(ExportError):
        export_day(FakeSource([stored(1), stored(2), stored(3)], boom=True), DAY, tmp_path)
    assert first.read_bytes() == before                       # không bao giờ để file dở dang
    assert [p.name for p in tmp_path.iterdir()] == ["calls-29-10-2026.csv"]


def test_failure_on_first_export_leaves_no_file(tmp_path):
    with pytest.raises(ExportError):
        export_day(FakeSource([stored(1), stored(2)], boom=True), DAY, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_export_dir_is_a_file(tmp_path):
    f = tmp_path / "not_a_dir"
    f.write_text("x")
    with pytest.raises(ExportError):
        export_day(FakeSource([stored()]), DAY, f)


def test_target_is_a_directory(tmp_path):
    (tmp_path / "calls-29-10-2026.csv").mkdir()
    with pytest.raises(ExportError):
        export_day(FakeSource([stored()]), DAY, tmp_path)


def test_unwritable_directory(tmp_path, monkeypatch):
    import os
    def deny(*a, **k): raise PermissionError(13, "Permission denied")
    monkeypatch.setattr("storage.csv_exporter._open_temp", deny)
    with pytest.raises(ExportError, match="quyền ghi"):
        export_day(FakeSource([stored()]), DAY, tmp_path)


def test_replace_blocked_like_file_open_in_excel(tmp_path, monkeypatch):
    export_day(FakeSource([stored(1)]), DAY, tmp_path)
    def deny(*a, **k): raise PermissionError(13, "in use")
    monkeypatch.setattr("storage.csv_exporter.os.replace", deny)
    with pytest.raises(ExportError, match="đang được mở"):
        export_day(FakeSource([stored(1), stored(2)]), DAY, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["calls-29-10-2026.csv"]   # file tạm được dọn


def test_formula_injection_is_neutralised(tmp_path):
    rows = read(export_day(FakeSource([stored(1, emp="=HYPERLINK(\"x\")")]), DAY, tmp_path).path)
    assert rows[1][1].startswith("'=")


# ----------------------------------------------------- tích hợp với PostgreSQL thật
def test_export_from_real_database(repos, tmp_path):
    for i in range(5):
        repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 29, 10, 0, i), duration=1.25))
    repos.calls.save(make_result(CallTag.REJECTED, ended=datetime(2026, 10, 29, 11, 0, 0)))
    repos.calls.save(make_result(CallTag.SUCCESS, "E2", ended=datetime(2026, 10, 30, 0, 0, 0)))   # ngày khác
    res = export_day(repos.calls, DAY, tmp_path)
    rows = read(res.path)
    assert res.rows == 6 == len(rows) - 1
    assert [r[5] for r in rows[1:]] == ["Success"] * 5 + ["Rejected"]
    assert rows[1][4] == "1.25" and rows[1][2] < rows[1][3] + "z"


# --------------------------------------------------------------- skip_empty (lịch tự động)
def test_skip_empty_does_not_create_or_touch_files(tmp_path):
    res = export_day(FakeSource([]), DAY, tmp_path, skip_empty=True)
    assert res.empty and not res.written and res.rows == 0 and list(tmp_path.iterdir()) == []
    old = export_day(FakeSource([stored(1)]), DAY, tmp_path).path
    before = old.read_bytes()
    res = export_day(FakeSource([]), DAY, tmp_path, skip_empty=True)
    assert res.empty and res.existed and old.read_bytes() == before


def test_skip_empty_false_still_writes_header_only(tmp_path):
    res = export_day(FakeSource([]), DAY, tmp_path)
    assert res.written and not res.empty and len(read(res.path)) == 1


def test_skip_empty_with_data_exports_every_row_including_the_first(tmp_path):
    res = export_day(FakeSource([stored(1), stored(2), stored(3)]), DAY, tmp_path, skip_empty=True)
    assert res.rows == 3 and [r[0] for r in read(res.path)[1:]] == ["0900000001", "0900000002", "0900000003"]


def test_exported_file_uses_normal_permissions_not_private_0600(tmp_path):
    import os, stat, sys
    p = export_day(FakeSource([stored()]), DAY, tmp_path).path
    if sys.platform != "win32":
        old = os.umask(0o022); os.umask(old)
        assert stat.S_IMODE(p.stat().st_mode) == 0o666 & ~old        # như file tạo bằng open() thông thường
