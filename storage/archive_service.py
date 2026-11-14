from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional

from .csv_exporter import CallSource, ExportError, OnExists, export_day
from .scheduler import JobOutcome, RunContext, TaskStatus

log = logging.getLogger(__name__)


class ArchiveService:
    def __init__(self, source_provider: Callable[[], Optional[CallSource]], export_dir_provider: Callable[[], Path],
                 wait_idle: Callable[[float], bool] = lambda timeout: True, *,
                 export_empty_days: bool = False, idle_timeout: float = 10.0) -> None:
        self._source = source_provider
        self._dir = export_dir_provider
        self._wait_idle = wait_idle
        self.export_empty_days = export_empty_days
        self.idle_timeout = idle_timeout
        self.on_day_closed: List[Callable[[date], None]] = []    

    def daily_export(self, ctx: RunContext) -> JobOutcome:
        return self._export(ctx.day, label="xuất trong ngày")

    def close_day(self, ctx: RunContext) -> JobOutcome:
        if ctx.attempt == 1 and not ctx.catch_up:
            for cb in list(self.on_day_closed):
                try:
                    cb(ctx.day)
                except Exception:
                    log.exception("Handler on_day_closed lỗi")
        return self._export(ctx.day, label="chốt ngày")

    def _export(self, day: date, label: str) -> JobOutcome:
        try:
            return self._do_export(day, label)
        except ExportError as exc:
            return JobOutcome(TaskStatus.FAILED, str(exc))

    def _do_export(self, day: date, label: str) -> JobOutcome:
        source = self._source()
        if source is None:
            raise ExportError("PostgreSQL chưa sẵn sàng nên chưa thể xuất dữ liệu")
        if not self._wait_idle(self.idle_timeout):
            raise ExportError("Dữ liệu chưa ghi hết vào PostgreSQL (đang mất kết nối hoặc ghi chậm)")
        res = export_day(source, day, self._dir(), on_exists=OnExists.OVERWRITE,
                         skip_empty=not self.export_empty_days)
        if res.empty:
            return JobOutcome(TaskStatus.SKIPPED, f"Ngày {day:%d/%m/%Y} không có cuộc gọi nào, không tạo file ({label})")
        note = ", ghi đè file cũ" if res.existed else ""
        return JobOutcome(TaskStatus.OK, f"{label.capitalize()}: đã xuất {res.rows:,} dòng ra {res.path.name}{note}",
                          res.path, res.rows)
