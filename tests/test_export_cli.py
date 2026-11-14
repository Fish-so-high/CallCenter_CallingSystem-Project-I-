from datetime import date, datetime

import pytest

from config.settings import DatabaseSettings
from core import CallTag
from database import Database
from storage.export_cli import main, parse_day
from tests.conftest import make_result

TODAY = date(2026, 10, 30)


@pytest.mark.parametrize("text,expected", [
    ("today", TODAY), ("YESTERDAY", date(2026, 10, 29)), ("29-10-2026", date(2026, 10, 29)),
    ("2026-10-29", date(2026, 10, 29)), ("29/10/2026", date(2026, 10, 29)),
])
def test_parse_day(text, expected):
    assert parse_day(text, TODAY) == expected


@pytest.mark.parametrize("bad", ["", "hôm qua", "32-13-2026", "2026"])
def test_parse_day_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_day(bad, TODAY)


def test_invalid_day_returns_exit_code_2(capsys):
    assert main(["--day", "linh tinh"]) == 2
    assert "Ngày không hợp lệ" in capsys.readouterr().err


def test_database_unreachable_returns_exit_code_1_without_leaking_password(tmp_path, capsys):
    dead = Database.from_settings(DatabaseSettings(host="127.0.0.1", port=1, password="mat-khau-bi-mat",
                                                  connect_timeout=1), retry_attempts=1)
    assert main(["--day", "29-10-2026", "--out", str(tmp_path)], database=dead, today=TODAY) == 1
    err = capsys.readouterr().err
    assert "PostgreSQL" in err and "mat-khau-bi-mat" not in err
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------- cần PostgreSQL thật
def test_exports_requested_day_from_real_database(repos, db, tmp_path, capsys):
    for i in range(4):
        repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 29, 10, 0, i)))
    repos.calls.save(make_result(CallTag.SUCCESS, "E1", ended=datetime(2026, 10, 30, 10, 0, 0)))
    assert main(["--day", "yesterday", "--out", str(tmp_path)], database=db, today=TODAY) == 0
    out = tmp_path / "calls-29-10-2026.csv"
    assert len(out.read_text(encoding="utf-8-sig").splitlines()) == 5
    assert "Đã xuất 4 dòng" in capsys.readouterr().out
    assert main(["--day", "29-10-2026", "--out", str(tmp_path)], database=db, today=TODAY) == 0   # chạy lại: ghi đè


def test_empty_day_exit_code_3_and_allow_empty(repos, db, tmp_path, capsys):
    assert main(["--day", "01-01-2026", "--out", str(tmp_path)], database=db, today=TODAY) == 3
    assert list(tmp_path.iterdir()) == []
    assert main(["--day", "01-01-2026", "--out", str(tmp_path), "--allow-empty"], database=db, today=TODAY) == 0
    assert len((tmp_path / "calls-01-01-2026.csv").read_text(encoding="utf-8-sig").splitlines()) == 1
