from __future__ import annotations

import logging
import queue
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from config.runtime import SETTINGS_PATH, RuntimeConfig
from core import CallCenterEngine, EventType
from core.employee_manager import EmployeeError, save_employees
from core.models import CallResult, Employee, EmployeeStatus, EngineSnapshot
from generator.input_reader import ReplaySource
from generator.test_case_generator import GeneratorWorker, RandomSource
from storage import ExportError, ExportResult, export_day

from .persistence import PersistenceService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpResult:
    ok: bool
    message: str


class SimulationService:
    def __init__(self, config: RuntimeConfig, employees: Iterable[Employee],
                 persistence: Optional[PersistenceService] = None, employees_file: Optional[Path] = None,
                 *, clock: Callable[[], datetime] = datetime.now, config_path: Optional[Path] = SETTINGS_PATH,
                 loop_interval: float = 0.02, snapshot_interval: float = 0.1,
                 inbox_size: int = 10_000, generator_record_path: Optional[Path] = None) -> None:
        self.config = config
        self.persistence = persistence
        self.employees_file = employees_file
        self.config_path = config_path
        self._clock = clock
        self._loop_interval = loop_interval
        self._snapshot_interval = snapshot_interval
        self._record_path = generator_record_path

        self.engine = CallCenterEngine(list(employees), config.engine_config())
        self.on_snapshot: List[Callable[[EngineSnapshot], None]] = []
        self.on_result: List[Callable[[CallResult], None]] = []
        self.on_notice: List[Callable[[str, str], None]] = []          
        self.on_state: List[Callable[[bool, bool], None]] = []         

        self._inbox: "queue.Queue" = queue.Queue(maxsize=inbox_size)
        self._gen_cfg = config.generator_config()
        self._generator: Optional[GeneratorWorker] = None
        self._running = False
        self._gen_enabled = True
        self._dirty = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="callcenter-engine-loop", daemon=True)
        self._exporter = ThreadPoolExecutor(max_workers=1, thread_name_prefix="callcenter-export")

        d = self.engine.dispatcher
        d.subscribe(EventType.CALL_FINISHED, self._on_finished)
        d.subscribe(EventType.STATE_CHANGED, lambda t, p: self._mark_dirty())
        d.subscribe(EventType.EMPLOYEE_STATUS_CHANGED, lambda t, p: self._sync_employees())
        if persistence is not None:
            persistence.request_employee_sync()

    # ------------------------------ Loop
    def start_loop(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._running = False
        self._stop.set()
        if self._generator:
            self._generator.stop()
        self._thread.join(2.0)
        self._exporter.shutdown(wait=False, cancel_futures=True)
        if self.persistence:
            self.persistence.stop()

    # -------------------------------- Run / Stop
    @property
    def running(self) -> bool:
        return self._running

    @property
    def generator_enabled(self) -> bool:
        return self._gen_enabled

    def start(self) -> OpResult:
        if self._running:
            return OpResult(True, "Mô phỏng đang chạy")
        now = self._clock()
        self.engine.resume(now)
        self._running = True
        self._ensure_generator()
        self._apply_generator_state()
        self._notify_state()
        log.info("Bắt đầu / tiếp tục mô phỏng")
        return OpResult(True, "Đã khởi chạy mô phỏng")

    def pause(self) -> OpResult:
        if not self._running:
            return OpResult(True, "Mô phỏng đang tạm dừng")
        self._running = False
        self.engine.pause(self._clock())
        self._apply_generator_state()
        self._notify_state()
        log.info("Tạm dừng mô phỏng")
        return OpResult(True, "Đã tạm dừng mô phỏng")

    def toggle(self) -> OpResult:
        return self.pause() if self._running else self.start()

    def set_generator_enabled(self, enabled: bool) -> OpResult:
        self._gen_enabled = bool(enabled)
        if self._gen_enabled and self._generator is not None and self._generator.source.exhausted:
            self._generator = None   
        if self._running:
            self._ensure_generator()
        self._apply_generator_state()
        self._notify_state()
        return OpResult(True, "Đã bật bộ sinh cuộc gọi" if self._gen_enabled else "Đã tắt bộ sinh cuộc gọi")

    # ------------------------------- Generator
    def _build_source(self):
        rng = random.Random(self.config.seed)
        if self.config.source_mode == "replay":
            return ReplaySource(RuntimeConfig.resolve(self.config.replay_file), lambda: self._gen_cfg, rng,
                                self._clock)
        return RandomSource(lambda: self._gen_cfg, rng, self._clock)

    def _ensure_generator(self) -> None:
        if self._generator is not None or not self._gen_enabled:
            return
        record = self._record_path if (self.config.record_generated and self.config.source_mode == "random") else None
        self._generator = GeneratorWorker(self._build_source(), self._inbox,
                                          waiting_provider=lambda: len(self.engine.queues.calling),
                                          on_exhausted=self._on_exhausted, record_path=record)
        self._generator.start()

    def _apply_generator_state(self) -> None:
        if self._generator is None:
            return
        if self._running and self._gen_enabled:
            self._generator.enable()
        else:
            self._generator.disable()

    def _on_exhausted(self) -> None:
        self._gen_enabled = False
        self._notice("info", "Đã phát hết file test case; bộ sinh cuộc gọi tự tắt")
        self._notify_state()

    def _reset_generator(self) -> None:
        if self._generator:
            self._generator.stop()
            self._generator = None
        while True:
            try:
                self._inbox.get_nowait()
            except queue.Empty:
                break
        if self._running:
            self._ensure_generator()
            self._apply_generator_state()

    # --------------------------- Config setting
    def apply_config(self, new: RuntimeConfig) -> OpResult:
        errors = new.validate()
        if errors:
            return OpResult(False, "; ".join(errors))
        old = self.config
        restart_gen = (new.source_mode, new.seed, new.replay_file, new.record_generated) != \
                      (old.source_mode, old.seed, old.replay_file, old.record_generated)
        self.config = new
        self._gen_cfg = new.generator_config()       
        self.engine.queues.calling.capacity = new.max_queue_size
        self.engine.config.miss_release_policy = type(self.engine.config.miss_release_policy)(new.miss_release_policy)
        self.engine.config.break_release_policy = type(self.engine.config.break_release_policy)(new.break_release_policy)
        if restart_gen:
            self._gen_enabled = True
            self._reset_generator()
            self._notify_state()
        self._mark_dirty()
        if self.config_path is not None:
            try:
                new.save(self.config_path)
            except OSError as exc:
                return OpResult(True, f"Đã áp dụng nhưng không lưu được cấu hình: {exc}")
        return OpResult(True, "Đã lưu và áp dụng cài đặt")

    # ------------------------------ Employee
    def _employee_op(self, fn, ok_message: str) -> OpResult:
        try:
            changed = fn()
        except EmployeeError as exc:
            return OpResult(False, str(exc))
        if changed is False:
            return OpResult(False, "Không có thay đổi (trạng thái hiện tại không cho phép thao tác này)")
        self._sync_employees()
        self._save_roster()
        return OpResult(True, ok_message)

    def set_break(self, employee_id: str) -> OpResult:
        return self._employee_op(lambda: self.engine.set_break(employee_id), f"{employee_id} tạm nghỉ")

    def set_offline(self, employee_id: str) -> OpResult:
        return self._employee_op(lambda: self.engine.set_offline(employee_id), f"{employee_id} ngừng hoạt động")

    def set_available(self, employee_id: str) -> OpResult:
        return self._employee_op(lambda: self.engine.set_available(employee_id, self._clock()),
                                 f"{employee_id} quay lại hoạt động")

    def add_employee(self, employee_id: str, name: str) -> OpResult:
        return self._employee_op(lambda: self.engine.add_employee(employee_id, name, self._clock()),
                                 f"Đã thêm nhân viên {employee_id.strip()}")

    def update_employee(self, employee_id: str, name: str) -> OpResult:
        return self._employee_op(lambda: self.engine.update_employee(employee_id, name),
                                 f"Đã cập nhật nhân viên {employee_id}")

    def abandon_waiting(self, call_id: str) -> OpResult:
        ok = self.engine.abandon_waiting(call_id, self._clock())
        return OpResult(ok, "Khách đã cúp máy, cuộc gọi được xóa khỏi hàng đợi" if ok
                        else "Cuộc gọi không còn trong hàng đợi")

    def employees(self) -> List[Employee]:
        return [Employee(e.employee_id, e.name, e.status, e.pending_status) for e in self.engine.employees.all()]

    def _sync_employees(self) -> None:
        if self.persistence:
            self.persistence.request_employee_sync()
        self._mark_dirty()

    def _save_roster(self) -> None:
        if self.employees_file is None:
            return
        roster = [Employee(e.employee_id, e.name,
                           EmployeeStatus.OFFLINE if e.status == EmployeeStatus.OFFLINE else EmployeeStatus.AVAILABLE)
                  for e in self.engine.employees.all()]
        try:
            save_employees(self.employees_file, roster)
        except OSError as exc:
            self._notice("warning", f"Không ghi được {Path(self.employees_file).name}: {exc}")

    # ----------------------------- Export CSV
    def export_day(self, day: Optional[date] = None) -> "Future[ExportResult]":
        day = day or self._clock().date()
        export_dir = RuntimeConfig.resolve(self.config.export_dir)

        def job() -> ExportResult:
            p = self.persistence
            repo = p.calls_repository if p else None
            if repo is None:
                raise ExportError("PostgreSQL chưa sẵn sàng nên không thể xuất dữ liệu")
            p.wait_idle(3.0)       # đưa các kết quả đang chờ ghi vào CSDL trước khi xuất
            return export_day(repo, day, export_dir)

        return self._exporter.submit(job)

    # ================================================================== nội bộ
    def snapshot(self) -> EngineSnapshot:
        return self.engine.snapshot()

    def start_new_day(self) -> None:
        self.engine.reset_stats()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _on_finished(self, _t, result: CallResult) -> None:
        if self.persistence:
            self.persistence.submit_result(result)
        for cb in list(self.on_result):
            self._safe(cb, result)

    def _notice(self, kind: str, message: str) -> None:
        for cb in list(self.on_notice):
            self._safe(cb, kind, message)

    def _notify_state(self) -> None:
        for cb in list(self.on_state):
            self._safe(cb, self._running, self._gen_enabled)

    @staticmethod
    def _safe(cb, *args) -> None:
        try:
            cb(*args)
        except Exception:
            log.exception("Callback %r lỗi", cb)

    def _loop(self) -> None:
        last_emit = 0.0
        while not self._stop.is_set():
            try:
                now = self._clock()
                if self._running:
                    for _ in range(500):         
                        try:
                            call = self._inbox.get_nowait()
                        except queue.Empty:
                            break
                        self.engine.submit(call, now)
                    self.engine.tick(now)
                mono = time.monotonic()
                if self._dirty and mono - last_emit >= self._snapshot_interval:
                    self._dirty = False
                    last_emit = mono
                    snap = self.engine.snapshot()
                    for cb in list(self.on_snapshot):
                        self._safe(cb, snap)
            except Exception:
                log.exception("Vòng lặp engine gặp lỗi")
            self._stop.wait(self._loop_interval)
