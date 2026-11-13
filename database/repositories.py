from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.timeutils import day_bounds, to_local, year_month
from core.models import CallResult, CallTag, Employee, EmployeeStatus

from .connection import Database
from .models import DAY_KEY, CallRecordModel, EmployeeModel, EmployeeMonthlyStatModel

log = logging.getLogger(__name__)

_SQLSTATE_FK_VIOLATION = "23503"


class RepositoryError(RuntimeError):
    pass

class UnknownEmployeeError(RepositoryError):
    """Bản ghi tham chiếu nhân viên chưa có trong bảng employees (cần đồng bộ trước)."""
    

@dataclass(frozen=True)
class StoredCall:
    call_id: str
    phone_number: str
    employee_id: Optional[str]
    received_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    duration_seconds: float
    tag: CallTag
    recorded_at: datetime
    
@dataclass(frozen=True)
class MonthlyStat:
    employee_id: str
    year_month: str
    successful_calls: int
    total_talk_seconds: float
    
    @property
    def total_talk_hours(self) -> float:
        return self.total_talk_seconds / 3600.0
    
def _translate(exc: IntegrityError) -> RepositoryError:
    state = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if state == _SQLSTATE_FK_VIOLATION:
        return UnknownEmployeeError("Nhân viên chưa tồn tại trong bảng employees: Hãy đồng bộ nhân viên trước khi ghi")
    return RepositoryError(f"Dữ liệu vi phạm rành buộc CSDL: {str(exc.orig).splitlines()[0]}")


# ------ Employees ------ #
class EmployeeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db
    
    def upsert_many(self, employees: Iterable[Employee]) -> int:
        rows = [{"employee_id": e.employee_id, "employee_name": e.name,
                 "status": e.status.value, "is_active": e.is_active} for e in employees]
        if not rows:
            return 0
        
        def op(s: Session) -> int:
            stmt = pg_insert(EmployeeModel).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[EmployeeModel.employee_id],
                set_={"employee_name": stmt.excluded.employee_name, "status": stmt.excluded.status,
                    "is_active": stmt.excluded.is_active}
         )
            s.execute(stmt)
            return len(rows)
    
        try:
            return self._db.run(op)
        except IntegrityError as exc:
            raise _translate(exc) from exc
        
    def get(self, employee_id: str) -> Optional[Employee]:
        def op(s: Session) -> Optional[Employee]:
            m = s.get(EmployeeModel, employee_id)
            return None if m is None else Employee(
                m.employee_id, m.employee_name, EmployeeStatus(m.status),
            )
        return self._db.run(op)
    
    def list_all(self) -> List[Employee]:
        def op(s: Session) -> List[Employee]:
            rows = s.scalars(select(EmployeeModel).order_by(EmployeeModel.employee_id))
            return [Employee(m.employee_id, m.employee_name, EmployeeStatus(m.status)) for m in rows]
        return self._db.run(op)
    
    def set_status(self, employee_id: str, status: EmployeeStatus) -> bool:
        def op(s: Session) -> bool:
            m = s.get(EmployeeModel, employee_id)
            if m is None:
                return False
            m.status = status.value
            m.is_active = status != EmployeeStatus.OFFLINE
            return True
        return self._db.run(op)
    
# ------ Call ------ #
class CallRecordRepository:
    def __init__(self, db: Database, tz: tzinfo) -> None:
        self._db = db
        self._tz = tz
    
    
    # --------- Insert
    def _insert(self, s: Session, r: CallResult) -> bool:
        received = to_local(r.received_at, self._tz)
        started = to_local(r.started_at, self._tz)
        ended = to_local(r.ended_at, self._tz)
        stmt = (pg_insert(CallRecordModel).values(
            call_id=r.call_id, phone_number=r.phone_number, employee_id=r.employee_id,
            received_at=received, started_at=started, ended_at=ended,
            duration_seconds=round(float(r.duration_seconds), 3), tag=r.tag.value).on_conflict_do_nothing(index_elements=[CallRecordModel.call_id]).returning(CallRecordModel.call_id
        ))
        inserted = s.execute(stmt).first() is not None
        if inserted and r.tag == CallTag.SUCCESS and r.employee_id is not None:
            ym = year_month(ended, self._tz)
            secs = round(float(r.duration_seconds), 3)
            up = pg_insert(EmployeeMonthlyStatModel).values(
                employee_id=r.employee_id, year_month=ym, successful_calls=1, total_talk_seconds=secs)
            s.execute(up.on_conflict_do_update(
                index_elements=[EmployeeMonthlyStatModel.employee_id, EmployeeMonthlyStatModel.year_month],
                set_={"successful_calls": EmployeeMonthlyStatModel.successful_calls + 1,
                "total_talk_seconds": EmployeeMonthlyStatModel.total_talk_seconds + secs}
            ))
        return inserted
    
    def save(self, result: CallResult) -> bool:
        try:
            return self._db.run(lambda s: self._insert(s, result))
        except IntegrityError as exc:
            raise _translate(exc) from exc
        
    def save_many(self, results: Sequence[CallResult]) -> int:
        def op(s: Session) -> int:
            return sum(1 for r in results if self._insert(s, r))
        try:
            return self._db.run(op) if results else 0
        except IntegrityError as exc:
            raise _translate(exc) from exc
        
    # --------- Read
    @staticmethod
    def _to_stored(m: CallRecordModel, tz: tzinfo) -> StoredCall:
        return StoredCall(m.call_id, m.phone_number, m.employee_id, to_local(m.received_at, tz),
                          to_local(m.started_at, tz), to_local(m.ended_at, tz), float(m.duration_seconds),
                          CallTag(m.tag), to_local(m.recorded_at, tz)                  
        )
        
    def iter_by_day(self, day: date, page_size: int = 2000) -> Iterator[StoredCall]:
        start, end = day_bounds(day, self._tz)
        last: Optional[Tuple[datetime, str]] = None
        while True:
            def op(s: Session, after=last) -> List[StoredCall]:
                q = select(CallRecordModel).where(DAY_KEY >= start, DAY_KEY < end)
                if after is not None:
                    q = q.where(tuple_(DAY_KEY, CallRecordModel.call_id) > tuple_(after[0], after[1]))
                q = q.order_by(DAY_KEY, CallRecordModel.call_id).limit(page_size)
                return [self._to_stored(m, self._tz) for m in s.scalars(q)]
            
            page = self._db.run(op)
            if not page:
                return
            yield from page
            tail = page[-1]
            last = (tail.ended_at or tail.received_at, tail.call_id)
            if len(page) < page_size:
                return
            
    def list_by_day(self, day: date) -> List[StoredCall]:
        return list(self.iter_by_day(day))
    
    def count_by_day(self, day: date) -> List[StoredCall]:
        start, end = day_bounds(day, self._tz)
        return self._db.run(lambda s: s.scalar(
            select(func.count()).select_from(CallRecordModel).where(DAY_KEY >= start, DAY_KEY < end)
        ) or 0)
    
# ----- Status ------ #
class MonthlyStatsRepository:
    def __init__(self, db: Database, tz: tzinfo) -> None:
        self._db = db
        self._tz = tz
        
    @staticmethod
    def _to_stat(m: EmployeeMonthlyStatModel) -> MonthlyStat:
        return MonthlyStat(m.employee_id, m.year_month, m.successful_calls, float(m.total_talk_seconds))
    
    def get(self, employee_id: str, ym: str) -> Optional[MonthlyStat]:
        def op(s: Session) -> Optional[MonthlyStat]:
            m = s.get(EmployeeMonthlyStatModel, (employee_id, ym))
            return None if m is None else self._to_stat(m)
        return self._db.run(op)
    
    def list_month(self, ym: str) -> List[MonthlyStat]:
        def op(s: Session) -> List[MonthlyStat]:
            rows = s.scalar(select(EmployeeMonthlyStatModel)
                            .where(EmployeeMonthlyStatModel.year_month == ym)
                            .order_by(EmployeeMonthlyStatModel.employee_id)
            )
            return [self._to_stat(m) for m in rows]
        return self._db.run(op)
    
    def rebuild_month(self, ym: str) -> int:
        year, month = int(ym[:4]), int(ym[5:7])
        first = date(year, month, 1)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        lo, _ = day_bounds(first, self._tz)
        hi, _ = day_bounds(nxt, self._tz)

        def op(s: Session) -> int:
            s.execute(delete(EmployeeMonthlyStatModel).where(EmployeeMonthlyStatModel.year_month == ym))
            agg = s.execute(
                select(CallRecordModel.employee_id, func.count(), func.coalesce(func.sum(CallRecordModel.duration_seconds), 0))
                .where(CallRecordModel.tag == CallTag.SUCCESS.value, CallRecordModel.employee_id.is_not(None),
                       DAY_KEY >= lo, DAY_KEY < hi)
                .group_by(CallRecordModel.employee_id)).all()
            for emp, n, secs in agg:
                s.add(EmployeeMonthlyStatModel(employee_id=emp, year_month=ym, successful_calls=n,
                                               total_talk_seconds=float(secs)))
            return len(agg)
        return self._db.run(op)

            





