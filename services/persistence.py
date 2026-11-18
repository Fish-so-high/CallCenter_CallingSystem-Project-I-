from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import tzinfo
from typing import Callable, Deque, List, Optional

from config.settings import DatabaseSettings
from core.models import CallResult, Employee
from database import (
    CallRecordRepository, Database, DatabaseUnavailableError, EmployeeRepository, RepositoryError,
    UnknownEmployeeError, apply_migrations,
)
from database.connection import describe_error

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DbStatus:
    connected: bool
    message: str
    pending: int      
    saved: int         
    dropped: int      


@dataclass
class Backend:
    db: Database
    employees: EmployeeRepository
    calls: CallRecordRepository

    def check(self) -> bool:
        return self.db.check_connection()

    def close(self) -> None:
        self.db.close()


def build_backend(settings: DatabaseSettings, tz: tzinfo) -> Backend:
    db = Database.from_settings(settings)
    try:
        apply_migrations(db.engine)
    except Exception:
        db.close()
        raise
    return Backend(db, EmployeeRepository(db), CallRecordRepository(db, tz))


class PersistenceService:
    def __init__(self, backend_factory: Callable[[], Backend], employees_provider: Callable[[], List[Employee]],
                 *, retry_interval: float = 5.0, buffer_limit: int = 100_000, batch_size: int = 200) -> None:
        self._factory = backend_factory
        self._employees = employees_provider
        self.retry_interval = retry_interval
        self.buffer_limit = buffer_limit
        self.batch_size = batch_size
        self.on_event: List[Callable[[str, str], None]] = []   

        self._lock = threading.Lock()
        self._pending: Deque[CallResult] = deque()
        self._inflight = 0                       
        self._backend: Optional[Backend] = None
        self._connected = False
        self._announced: Optional[bool] = None   
        self._message = "Đang kết nối PostgreSQL..."
        self._sync_needed = True
        self._saved = 0
        self._dropped = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="callcenter-persistence", daemon=True)

    # ---------------------------------- Loop
    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self.wait_idle(min(timeout, 2.0))
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)
        with self._lock:
            left = len(self._pending)
        if left:
            log.warning("Thoát khi còn %d kết quả chưa ghi được vào CSDL", left)
        if self._backend:
            self._backend.close()

    # ---------------- API
    def submit_result(self, result: CallResult) -> None:
        with self._lock:
            if len(self._pending) >= self.buffer_limit:
                self._pending.popleft()
                self._dropped += 1
                if self._dropped == 1 or self._dropped % 1000 == 0:
                    log.error("Bộ đệm ghi CSDL đầy (%d): đã loại %d kết quả cũ nhất", self.buffer_limit, self._dropped)
            self._pending.append(result)
        self._wake.set()

    def request_employee_sync(self) -> None:
        with self._lock:
            self._sync_needed = True
        self._wake.set()

    # ---------------- Status
    def status(self) -> DbStatus:
        with self._lock:
            return DbStatus(self._connected, self._message, len(self._pending) + self._inflight,
                            self._saved, self._dropped)

    @property
    def calls_repository(self) -> Optional[CallRecordRepository]:
        return self._backend.calls if (self._backend and self._connected) else None

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Chờ cho tới khi mọi kết quả đã được ghi (hoặc hết thời gian). Dùng trước khi xuất CSV."""
        deadline = threading.Event()
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if not self._pending and not self._inflight and not self._sync_needed:
                    return True
                if not self._connected:
                    return False
            deadline.wait(0.02)
        return False

    # -------------------- State
    def _set_state(self, connected: bool, message: str) -> None:
        with self._lock:
            changed = connected != self._announced
            self._announced = connected
            self._connected, self._message = connected, message
        if changed:
            kind = "success" if connected else "error"
            for cb in list(self.on_event):
                try:
                    cb(kind, message)
                except Exception:
                    log.exception("Handler on_event lỗi")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._ensure_connected():
                    self._stop.wait(self.retry_interval)
                    continue
                if not self._process_once():
                    self._wake.wait(0.2)
                    self._wake.clear()
            except Exception:
                log.exception("Luồng ghi CSDL gặp lỗi bất ngờ")
                self._stop.wait(1.0)

    def _ensure_connected(self) -> bool:
        if self._backend is None:
            try:
                self._backend = self._factory()
            except Exception as exc:
                self._set_state(False, f"PostgreSQL không khả dụng: {describe_error(exc)}")
                log.warning("Kết nối PostgreSQL thất bại: %s", describe_error(exc))
                return False
            self._sync_needed = True
            self._set_state(True, "Đã kết nối PostgreSQL")
            return True
        if not self._connected:
            if self._backend.check():
                self._sync_needed = True
                self._set_state(True, "Đã kết nối lại PostgreSQL")
                return True
            return False
        return True

    def _lost(self, exc: BaseException) -> None:
        self._set_state(False, f"Mất kết nối PostgreSQL: {describe_error(exc)}")

    def _process_once(self) -> bool:
        assert self._backend is not None
        if self._sync_needed:
            try:
                self._backend.employees.upsert_many(self._employees())
                self._sync_needed = False
            except DatabaseUnavailableError as exc:
                self._lost(exc)
                return True
            except RepositoryError as exc:
                log.error("Đồng bộ nhân viên lỗi: %s", exc)
                self._sync_needed = False
        with self._lock:
            n = min(self.batch_size, len(self._pending))
            batch = [self._pending.popleft() for _ in range(n)]
            self._inflight = len(batch)
        if not batch:
            return False
        try:
            self._save(batch)
        except DatabaseUnavailableError as exc:
            with self._lock:            
                self._pending.extendleft(reversed(batch))
                self._inflight = 0
            self._lost(exc)
            return True
        except BaseException:
            with self._lock:
                self._inflight = 0
                self._dropped += len(batch)
            log.exception("Lỗi bất ngờ khi ghi %d kết quả: các bản ghi này bị bỏ", len(batch))
            return True
        with self._lock:
            self._inflight = 0
        return True

    def _save(self, batch: List[CallResult]) -> None:
        backend = self._backend
        try:
            backend.calls.save_many(batch)
            self._count_saved(len(batch))
            return
        except UnknownEmployeeError:
            try:
                backend.employees.upsert_many(self._employees())
                backend.calls.save_many(batch)
                self._count_saved(len(batch))
                return
            except UnknownEmployeeError:
                pass
        except RepositoryError:
            pass
        for r in batch:                        
            try:
                backend.calls.save(r)
                self._count_saved(1)
            except RepositoryError as exc:
                with self._lock:
                    self._dropped += 1
                log.error("Bỏ bản ghi %s (%s): %s", r.call_id, r.tag.value, exc)

    def _count_saved(self, n: int) -> None:
        with self._lock:
            self._saved += n
