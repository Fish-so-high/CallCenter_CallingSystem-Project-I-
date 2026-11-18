"""Cầu nối giữa các luồng nền (services) và GUI thread.

Quy tắc an toàn luồng:
  * Callback của service chạy ở luồng nền -> chỉ `emit` một Signal nội bộ (thread-safe).
  * Mọi Signal nội bộ nối tới slot của chính controller bằng Qt.QueuedConnection -> slot luôn chạy ở GUI thread.
  * View chỉ nhận Signal công khai (đã ở GUI thread) và chỉ gọi các phương thức mỏng của controller.
Controller KHÔNG chứa nghiệp vụ: mọi quyết định nằm ở services/ và core/.
"""
from __future__ import annotations

from concurrent.futures import Future
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from config.runtime import RuntimeConfig
from core.models import CallResult, Employee, EngineSnapshot
from services import AutomationService, DbStatus, OpResult, SimulationService


class AppController(QObject):
    # ---- Public Signal ----
    snapshot_ready = Signal(object)          
    results_ready = Signal(object)             
    db_status_changed = Signal(object)         
    notice = Signal(str, str)                 
    state_changed = Signal(bool, bool)         
    export_finished = Signal(object, object)   
    new_day = Signal(object)                   
    schedule_result = Signal(object)          

    # ---- Secret Signal ----
    _snapshot_in = Signal(object)
    _result_in = Signal(object)
    _notice_in = Signal(str, str)
    _state_in = Signal(bool, bool)
    _export_in = Signal(object, object)
    _new_day_in = Signal(object)
    _schedule_in = Signal(object)

    FLUSH_MS = 100
    DB_POLL_MS = 500

    def __init__(self, service: SimulationService, automation: Optional[AutomationService] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.service = service
        self.automation = automation
        self._min_day: Optional[date] = None    
        self._buffer: List[CallResult] = []
        self._last_db: Optional[DbStatus] = None

        q = Qt.ConnectionType.QueuedConnection
        self._snapshot_in.connect(self.snapshot_ready, q)
        self._result_in.connect(self._collect_result, q)
        self._notice_in.connect(self.notice, q)
        self._state_in.connect(self.state_changed, q)
        self._export_in.connect(self.export_finished, q)
        self._new_day_in.connect(self._handle_new_day, q)
        self._schedule_in.connect(self.schedule_result, q)

        service.on_snapshot.append(self._snapshot_in.emit)
        service.on_result.append(self._result_in.emit)
        service.on_notice.append(self._notice_in.emit)
        service.on_state.append(self._state_in.emit)
        if service.persistence is not None:
            service.persistence.on_event.append(self._notice_in.emit)

        if automation is not None:
            automation.on_notice.append(self._notice_in.emit)
            automation.on_new_day.append(self._new_day_in.emit)
            automation.on_result.append(self._schedule_in.emit)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(self.FLUSH_MS)
        self._flush_timer.timeout.connect(self.flush_results)
        self._flush_timer.start()
        self._db_timer = QTimer(self)
        self._db_timer.setInterval(self.DB_POLL_MS)
        self._db_timer.timeout.connect(self._poll_db)
        self._db_timer.start()

    # ---------------------------------- Data View
    @property
    def config(self) -> RuntimeConfig:
        return self.service.config

    @property
    def export_dir(self) -> Path:
        return RuntimeConfig.resolve(self.service.config.export_dir)

    @property
    def employees_file(self) -> Optional[Path]:
        return self.service.employees_file

    @property
    def running(self) -> bool:
        return self.service.running

    @property
    def generator_enabled(self) -> bool:
        return self.service.generator_enabled

    def schedule_summary(self) -> str:
        return self.automation.summary() if self.automation else "Lịch tự động: không có"

    def schedule_details(self) -> str:
        return self.automation.details() if self.automation else ""

    def employees(self) -> List[Employee]:
        return self.service.employees()

    def snapshot(self) -> EngineSnapshot:
        return self.service.snapshot()

    # -------------------------------- Behavior
    def toggle_run(self) -> OpResult:
        return self.service.toggle()

    def set_generator_enabled(self, enabled: bool) -> OpResult:
        return self.service.set_generator_enabled(enabled)

    def apply_config(self, cfg: RuntimeConfig) -> OpResult:
        return self.service.apply_config(cfg)

    def abandon_call(self, call_id: str) -> OpResult:
        return self.service.abandon_waiting(call_id)

    def employee_action(self, employee_id: str, action: str) -> OpResult:
        fn = {"break": self.service.set_break, "available": self.service.set_available,
              "offline": self.service.set_offline}.get(action)
        return fn(employee_id) if fn else OpResult(False, f"Thao tác không hợp lệ: {action}")

    def add_employee(self, employee_id: str, name: str) -> OpResult:
        return self.service.add_employee(employee_id, name)

    def rename_employee(self, employee_id: str, name: str) -> OpResult:
        return self.service.update_employee(employee_id, name)

    def request_export(self, day: Optional[date] = None) -> None:
        fut = self.service.export_day(day)
        fut.add_done_callback(self._on_export_done)

    # -------------------------------------------------- Secret core
    def _on_export_done(self, fut: "Future") -> None:        
        try:
            self._export_in.emit(fut.result(), None)
        except Exception as exc:
            self._export_in.emit(None, str(exc))

    def _collect_result(self, result: CallResult) -> None:  
        if self._min_day is not None and result.ended_at.date() < self._min_day:
            return                                          
        self._buffer.append(result)

    def _handle_new_day(self, closed_day: date) -> None:    
        self._buffer.clear()
        self._min_day = closed_day + timedelta(days=1)
        self.new_day.emit(closed_day)

    def flush_results(self) -> None:
        if self._buffer:
            batch, self._buffer = self._buffer, []
            self.results_ready.emit(batch)

    def _poll_db(self) -> None:
        p = self.service.persistence
        if p is None:
            return
        st = p.status()
        if st != self._last_db:
            self._last_db = st
            self.db_status_changed.emit(st)

    def shutdown(self) -> None:
        self._flush_timer.stop()
        self._db_timer.stop()
        if self.automation:
            self.automation.stop()
        self.service.shutdown()
