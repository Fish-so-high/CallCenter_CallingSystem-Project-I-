from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"

def get_timezone(name: str = DEFAULT_TIMEZONE) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Múi giờ không hợp lệ: {name!r}. Hãy cài đặt 'tzdata' (python -m pip install tzdata).") from exc

def to_local(dt: Optional[datetime], tz: tzinfo) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo = tz)
    return dt.astimezone(tz)


def day_bounds(day: date, tz: tzinfo) -> Tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo = tz)
    end = datetime.combine(day + timedelta(days = 1), time.min, tzinfo = tz)
    return start, end

def year_month(dt: datetime, tz: tzinfo) -> str:
    local = to_local(dt, tz)
    return f"{local.year:04d}-{local.month:02d}"   



