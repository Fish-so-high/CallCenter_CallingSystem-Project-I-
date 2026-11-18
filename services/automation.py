from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, tzinfo
from pathlib import Path
from typing import Callable, List, Optional

from config.runtime import RuntimeConfig, parse_hhmm
from storage.archive_service import ArchiveService
from storage.scheduler import DailyScheduler, SchedulerState, ScheduledTask, TaskResult, TaskStatus

from .persistence import PersistenceService
from .simulation import SimulationService

log = logging.getLogger(__name__)
MIDNIGHT = time(0, 0)


class AutomationService:
    def __init__(self, persistence: PersistenceService, simulation: SimulationService, tz: tzinfo,
                 state_path: Optional[Path] = None, *, clock: Optional[Callable[[], datetime]] = None,
                 poll_interval: float = 1.0, retry_delay: float = 60.0, max_retries: int = 30,
                 max_catch_up_days: int = 7, startup_grace: float = 15.0,
                 monotonic: Callable[[], float] = _time.monotonic) -> None:
        self.simulation = simulation
        self.persistence = persistence
        self.startup_grace = startup_grace
        self._mono = monotonic
        self._t0 = monotonic()
        self.retry_delay = retry_delay
        self.on_notice: List[Callable[[str, str], None]] = []       
        self.on_new_day: List[Callable[[date], None]] = []          
        self.on_result: List[Callable[[TaskResult], None]] = []

        self.archive = ArchiveService(lambda: persistence.calls_repository,
                                      lambda: RuntimeConfig.resolve(simulation.config.export_dir),
                                      persistence.wait_idle)
        self.archive.on_day_closed.append(self._day_closed)
        self.tasks = [
            ScheduledTask("daily-export", lambda: parse_hhmm(simulation.config.auto_export_time) or time(19, 0), 0,
                          self.archive.daily_export),
            ScheduledTask("day-rollover", lambda: MIDNIGHT, 1, self.archive.close_day),
        ]
        self.scheduler = DailyScheduler(
            self.tasks, tz, SchedulerState(state_path), clock=clock,
            enabled=self._ready, retry_delay=retry_delay,
            max_retries=max_retries, max_catch_up_days=max_catch_up_days, poll_interval=poll_interval)
        self.scheduler.on_result.append(self._on_result)

    def _ready(self) -> bool:
        if not self.simulation.config.auto_schedule_enabled:
            return False
        return self.persistence.status().connected or (self._mono() - self._t0) >= self.startup_grace

    def start(self) -> None:
        self._t0 = self._mono()
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()

    # -------------------------------- Status
    def summary(self) -> str:
        cfg = self.simulation.config
        if not cfg.auto_schedule_enabled:
            return "Lịch tự động: đang tắt"
        return f"Tự động: xuất {cfg.auto_export_time} · phân kỳ 00:00"

    def details(self) -> str:
        now = datetime.now(self.scheduler.tz) if self.scheduler._clock is None else self.scheduler._clock()
        lines = ["Ứng dụng phải đang mở thì lịch tự động mới chạy."]
        for t in self.tasks:
            label = "Xuất CSV" if t.name == "daily-export" else "Phân kỳ ngày"
            nxt = self.scheduler.next_due(t, now)
            last = self.scheduler.last_results.get(t.name)
            lines.append(f"{label}: lần tới {nxt:%d/%m %H:%M}" + (f" · gần nhất: {last.message}" if last else ""))
        return "\n".join(lines)

    # --------------------------------------------------------------- Event
    def _day_closed(self, day: date) -> None:
        self.simulation.start_new_day()
        log.info("Sang ngày mới (đã chốt ngày %s)", day)
        for cb in list(self.on_new_day):
            self._safe(cb, day)
        self._notice("info", f"Đã sang ngày mới: bảng lịch sử và thống kê được làm mới (dữ liệu ngày {day:%d/%m/%Y} vẫn nằm trong CSDL)")

    def _on_result(self, r: TaskResult) -> None:
        for cb in list(self.on_result):
            self._safe(cb, r)
        tag = "[Tự động, bù] " if r.catch_up else "[Tự động] "
        if r.status is TaskStatus.OK:
            self._notice("success", tag + f"{r.message} (ngày {r.day:%d/%m/%Y})")
        elif r.status is TaskStatus.FAILED and (r.final or r.attempt == 1):
            extra = "" if r.final else f" Sẽ thử lại sau {int(self.retry_delay)} giây."
            self._notice("error", tag + f"Lỗi ngày {r.day:%d/%m/%Y}: {r.message}.{extra}")

    def _notice(self, kind: str, message: str) -> None:
        for cb in list(self.on_notice):
            self._safe(cb, kind, message)

    @staticmethod
    def _safe(cb, *args) -> None:
        try:
            cb(*args)
        except Exception:
            log.exception("Callback %r lỗi", cb)
