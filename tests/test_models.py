import pytest

from core import CallEvent, CallStatus, CallTag, IncomingCall
from core.models import EVENT_OUTCOME, validate_phone
from tests.conftest import T0


@pytest.mark.parametrize("phone", ["0912345678", "0812345678", "0212345678", "0312345678"])
def test_valid_phone(phone):
    assert validate_phone(phone) == phone


@pytest.mark.parametrize("phone", ["", "091234567", "09123456789", "0412345678", "abcdefghij", "9123456789"])
def test_invalid_phone(phone):
    with pytest.raises(ValueError):
        validate_phone(phone)


def test_event_parse_case_insensitive():
    assert CallEvent.parse("success") is CallEvent.SUCCESS
    assert CallEvent.parse(" MISS ") is CallEvent.MISS
    assert CallEvent.parse(CallEvent.BREAK) is CallEvent.BREAK
    with pytest.raises(ValueError):
        CallEvent.parse("Rejected")


@pytest.mark.parametrize("t", [-1, float("nan"), float("inf")])
def test_incoming_rejects_bad_time_call(t):
    with pytest.raises(ValueError):
        IncomingCall("0912345678", "Success", t, T0)


def test_incoming_normalizes_and_is_frozen():
    c = IncomingCall("0912345678", "break", "0.5", T0)
    assert c.event is CallEvent.BREAK and c.time_call == 0.5
    with pytest.raises(Exception):
        c.phone_number = "0900000000"


def test_event_status_tag_are_distinct_concepts():
    assert EVENT_OUTCOME[CallEvent.SUCCESS] == (CallStatus.COMPLETED, CallTag.SUCCESS)
    assert EVENT_OUTCOME[CallEvent.MISS] == (CallStatus.MISSED, CallTag.MISS)
    assert EVENT_OUTCOME[CallEvent.BREAK] == (CallStatus.ABANDONED, CallTag.BREAK)
