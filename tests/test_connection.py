"""Test lỗi kết nối: KHÔNG cần PostgreSQL (trỏ vào cổng không có dịch vụ)."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from config.settings import DatabaseSettings
from database import Database, DatabaseUnavailableError
from database.connection import _is_retryable


def dead_db(sleeps, attempts=3):
    s = DatabaseSettings(host="127.0.0.1", port=1, user="u", password="s3cret-pass", connect_timeout=1)
    return Database.from_settings(s, retry_attempts=attempts, retry_delay=0.1, sleep=sleeps.append)


def test_unreachable_server_raises_clear_error_after_retries():
    sleeps = []
    db = dead_db(sleeps)
    with pytest.raises(DatabaseUnavailableError) as ei:
        db.run(lambda s: s.execute(text("SELECT 1")))
    assert sleeps == [0.1, 0.2]                      # 3 lần thử, 2 lần chờ tăng dần
    assert "s3cret-pass" not in str(ei.value)        # không lộ mật khẩu
    assert "PostgreSQL" in str(ei.value)


def test_check_connection_false_when_down():
    assert dead_db([]).check_connection() is False


def test_transient_error_is_retried_then_succeeds():
    sleeps, calls = [], []
    db = dead_db(sleeps)

    def flaky(_session):
        calls.append(1)
        if len(calls) < 3:
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))
        return "ok"

    assert db.run(flaky) == "ok" and len(calls) == 3 and len(sleeps) == 2


def test_non_operational_errors_are_not_swallowed_or_retried():
    db = dead_db([])
    calls = []

    def bug(_s):
        calls.append(1)
        raise KeyError("lỗi logic")

    with pytest.raises(KeyError):
        db.run(bug)
    assert len(calls) == 1


class _Orig(Exception):
    def __init__(self, state): self.sqlstate = state


@pytest.mark.parametrize("state,retry", [("28P01", False), ("3D000", False), ("42501", False),
                                         ("57P01", True), ("40P01", True), (None, True)])
def test_retry_classification(state, retry):
    assert _is_retryable(OperationalError("x", {}, _Orig(state))) is retry


def test_auth_failure_is_not_retried():
    sleeps, calls = [], []
    db = dead_db(sleeps)

    def bad_auth(_s):
        calls.append(1)
        raise OperationalError("x", {}, _Orig("28P01"))

    with pytest.raises(DatabaseUnavailableError):
        db.run(bad_auth)
    assert len(calls) == 1 and sleeps == []
