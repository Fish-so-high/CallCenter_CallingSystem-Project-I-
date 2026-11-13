from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EmployeeModel(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("status IN ('AVAILABLE', 'BUSY', 'BREAK', 'OFFLINE')", name="ck_employees_status"),
    )

    employee_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    employee_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="AVAILABLE")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class CallRecordModel(Base):
    __tablename__ = "call_records"
    __table_args__ = (
        CheckConstraint("phone_number ~ '^(09|08|02|03)[0-9]{8}$'", name="ck_call_records_phone"),
        CheckConstraint("tag IN ('Success', 'Miss', 'Break', 'Rejected', 'Abandoned')", name="ck_call_records_tag"),
        CheckConstraint("duration_seconds >= 0", name="ck_call_records_duration"),
        CheckConstraint("started_at IS NULL OR ended_at IS NULL OR ended_at >= started_at",
                        name="ck_call_records_time_order"),
        CheckConstraint("tag IN ('Rejected', 'Abandoned') OR employee_id IS NOT NULL",
                        name="ck_call_records_employee_required"),
    )

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False)
    employee_id: Mapped[Optional[str]] = mapped_column(ForeignKey("employees.employee_id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float] = mapped_column(Numeric(10, 3, asdecimal=False), nullable=False,
                                                    server_default="0")
    tag: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())


DAY_KEY = func.coalesce(CallRecordModel.ended_at, CallRecordModel.received_at)
Index("ix_call_records_day_key", DAY_KEY, CallRecordModel.call_id)


class EmployeeMonthlyStatModel(Base):
    __tablename__ = "employee_monthly_stats"
    __table_args__ = (
        CheckConstraint("year_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_stats_year_month"),
        CheckConstraint("successful_calls >= 0", name="ck_stats_calls"),
        CheckConstraint("total_talk_seconds >= 0", name="ck_stats_seconds"),
    )

    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.employee_id"), primary_key=True)
    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    successful_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_talk_seconds: Mapped[float] = mapped_column(Numeric(14, 3, asdecimal=False), nullable=False,
                                                      server_default="0")
