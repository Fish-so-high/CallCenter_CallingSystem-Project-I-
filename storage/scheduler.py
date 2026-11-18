from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    OK = "OK"
    SKIPPED = "SKIPPED"     
    FAILED = "FAILED"


@dataclass(frozen=True)
class RunContext:
    task: str
    day: date
    due_at: datetime
    now: datetime
    attempt: int          
    catch_up: bool       


@dataclass(frozen=True)
class JobOutcome:
    status: TaskStatus
    message: str
    path: Optional[Path] = None
    rows: int = 0


@dataclass(frozen=True)
class TaskResult:
    task: str
    day: date
    status: TaskStatus
    message: str
    attempt: int
    final: bool                  
    finished_at: datetime
    path: Optional[Path] = None
    rows: int = 0
    catch_up: bool = False


@dataclass
class ScheduledTask:
    name: str
    time_provider: Callable[[], time]
    day_offset: int
    job: Callable[[RunContext], JobOutcome]
    enabled: Callable[[], bool] = lambda: True

    def due_at(self, day: date, tz: tzinfo) -> datetime:
        return datetime.combine(day + timedelta(days=self.day_offset), self.time_provider(), tzinfo=tz)


class SchedulerState:

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self._data: Dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = {str(k): str(v) for k, v in raw.get("last_done_day", {}).items()}
            except (OSError, ValueError, AttributeError) as exc:
                log.warning("Không đọc được %s (%s): coi như chưa có trạng thái", self.path, exc)

    def get(self, task: str) -> Optional[date]:
        try:
            return date.fromisoformat(self._data[task])
        except (KeyError, ValueError):
            return None

    def set(self, task: str, day: date) -> None:
        self._data[task] = day.isoformat()
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"last_done_day": self._data}, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:   
            log.error("Không ghi được trạng thái lập lịch %s: %s", self.path, exc)


class DailyScheduler:
    def __init__(self, tasks: List[ScheduledTask], tz: tzinfo, state: Optional[SchedulerState] = None, *,
                 clock: Optional[Callable[[], datetime]] = None, enabled: Callable[[], bool] = lambda: True,
                 retry_delay: float = 60.0, max_retries: int = 30, max_catch_up_days: int = 7,
                 late_after: float = 300.0, poll_interval: float = 1.0) -> None:
        self.tasks = tasks
        self.tz = tz
        self.state = state or SchedulerState()
        self._clock = clock or (lambda: datetime.now(tz))
        self._enabled = enabled
        self.retry_delay = retry_delay
        self.max_retries = max_retries
        self.max_catch_up_days = max_catch_up_days
        self.late_after = timedelta(seconds=late_after)
        self.poll_interval = poll_interval
        self.on_result: List[Callable[[TaskResult], None]] = []
        self.last_results: Dict[str, TaskResult] = {}
        self._attempts: Dict[Tuple[str, date], Tuple[int, datetime]] = {}    
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -------------------------------------- Tinh ngay den han
    def latest_due_day(self, task: ScheduledTask, now: datetime) -> date:
        day = now.astimezone(self.tz).date() + timedelta(days=1)
        while task.due_at(day, self.tz) > now:
            day -= timedelta(days=1)
        return day

    def pending_days(self, task: ScheduledTask, now: datetime) -> List[date]:
        latest = self.latest_due_day(task, now)
        last = self.state.get(task.name)
        if last is None:
            return [latest]
        n = (latest - last).days
        if n <= 0:
            return []
        n = min(n, self.max_catch_up_days)
        return [latest - timedelta(days=i) for i in range(n - 1, -1, -1)]

    def next_due(self, task: ScheduledTask, now: Optional[datetime] = None) -> datetime:
        now = (now or self._clock()).astimezone(self.tz)
        if self.pending_days(task, now):
            return now                       
        return task.due_at(self.latest_due_day(task, now) + timedelta(days=1), self.tz)

    # ------------------------------------------ Run
    def tick(self, now: Optional[datetime] = None) -> List[TaskResult]:
        """Chạy mọi tác vụ đã đến hạn. An toàn khi gọi thường xuyên; trả về các kết quả phát sinh."""
        now = (now or self._clock()).astimezone(self.tz)
        out: List[TaskResult] = []
        with self._lock:
            if not self._enabled():
                return out
            for task in self.tasks:
                if task.enabled():
                    out.extend(self._run_task(task, now))
        for r in out:
            self.last_results[r.task] = r
            for cb in list(self.on_result):
                try:
                    cb(r)
                except Exception:
                    log.exception("Handler on_result lỗi")
        return out

    def _run_task(self, task: ScheduledTask, now: datetime) -> List[TaskResult]:
        results: List[TaskResult] = []
        for day in self.pending_days(task, now):
            key = (task.name, day)
            tries, retry_at = self._attempts.get(key, (0, now))
            if now < retry_at:
                break                                       
            due = task.due_at(day, self.tz)
            ctx = RunContext(task.name, day, due, now, tries + 1, now - due > self.late_after)
            started = datetime.now(self.tz)
            try:
                outcome = task.job(ctx)
            except Exception as exc:
                log.exception("Tác vụ %s ngày %s lỗi (lần %d)", task.name, day, ctx.attempt)
                outcome = JobOutcome(TaskStatus.FAILED, f"{exc}")
            if outcome.status is not TaskStatus.FAILED:
                self.state.set(task.name, day)
                self._attempts.pop(key, None)
                log.info("Tác vụ %s ngày %s: %s - %s", task.name, day, outcome.status.value, outcome.message)
                results.append(TaskResult(task.name, day, outcome.status, outcome.message, ctx.attempt, True,
                                          started, outcome.path, outcome.rows, ctx.catch_up))
                continue
            give_up = ctx.attempt >= self.max_retries
            log.error("Tác vụ %s ngày %s THẤT BẠI (lần %d/%d): %s", task.name, day, ctx.attempt,
                      self.max_retries, outcome.message)
            if give_up:
                self.state.set(task.name, day)            
                self._attempts.pop(key, None)
                msg = f"{outcome.message} (đã thử {ctx.attempt} lần, bỏ qua; hãy dùng nút Trích xuất CSV)"
            else:
                self._attempts[key] = (ctx.attempt, now + timedelta(seconds=self.retry_delay))
                msg = outcome.message
            results.append(TaskResult(task.name, day, TaskStatus.FAILED, msg, ctx.attempt, give_up, started,
                                      catch_up=ctx.catch_up))
            if not give_up:
                break
        return results

    # --------------------------------------- Run ngam
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="callcenter-scheduler", daemon=True)
        self._thread.start()
        log.info("Bộ lập lịch đã chạy: %s", ", ".join(t.name for t in self.tasks))

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Bộ lập lịch gặp lỗi bất ngờ")
            self._stop.wait(self.poll_interval)
