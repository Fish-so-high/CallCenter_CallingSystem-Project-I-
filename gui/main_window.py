from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QSplitter, QVBoxLayout, QWidget

from gui.controllers import AppController
from gui.dialogs.employee_dialog import EmployeeDialog
from gui.dialogs.export_dialog import ExportDialog
from gui.dialogs.settings_dialog import SettingsDialog
from gui.views.calling_queue_view import CallingQueueView
from gui.views.history_view import HistoryView
from gui.views.service_queue_view import ServiceQueueView
from gui.widgets.sidebar import Sidebar
from gui.widgets.toast import ToastManager


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle("CallCenter")
        self.setMinimumSize(1100, 700)
        self.resize(1360, 820)

        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        content = QWidget(); col = QVBoxLayout(content); col.setContentsMargins(14, 14, 14, 14)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        top = QWidget(); top_lay = QHBoxLayout(top); top_lay.setContentsMargins(0, 0, 0, 0); top_lay.setSpacing(14)
        self.service_view = ServiceQueueView()
        self.calling_view = CallingQueueView()
        top_lay.addWidget(self.service_view, 1)      
        top_lay.addWidget(self.calling_view, 1)
        self.history_view = HistoryView()
        self.splitter.addWidget(top); self.splitter.addWidget(self.history_view)
        self.splitter.setStretchFactor(0, 5); self.splitter.setStretchFactor(1, 4)
        self.splitter.setChildrenCollapsible(False)
        col.addWidget(self.splitter)
        root.addWidget(content, 1)

        self.toasts = ToastManager(self)
        self._wire()
        self.sidebar.set_state(controller.running, controller.generator_enabled)
        self._refresh_schedule()
        snap = controller.snapshot()
        self._on_snapshot(snap)

    # -------------------------------- Connect signal
    def _wire(self) -> None:
        c, s = self.controller, self.sidebar
        c.snapshot_ready.connect(self._on_snapshot)
        c.results_ready.connect(self.history_view.add_results)
        c.db_status_changed.connect(s.set_db_status)
        c.notice.connect(self._on_notice)
        c.state_changed.connect(s.set_state)
        c.export_finished.connect(self._on_export_finished)
        c.new_day.connect(self._on_new_day)
        c.schedule_result.connect(self._on_schedule_result)

        s.run_clicked.connect(self._on_run)
        s.generator_toggled.connect(self._on_generator_toggled)
        s.export_clicked.connect(self._on_export)
        s.employees_clicked.connect(self._on_employees)
        s.data_clicked.connect(self._on_open_data)
        s.settings_clicked.connect(self._on_settings)
        self.service_view.employee_action.connect(self._on_employee_action)
        self.calling_view.abandon_requested.connect(self._on_abandon)

    def _on_schedule_result(self, _result) -> None:
        self._refresh_schedule()

    def _refresh_schedule(self) -> None:
        self.sidebar.set_schedule(self.controller.schedule_summary(), self.controller.schedule_details())

    def _on_new_day(self, closed_day) -> None:
        self.history_view.clear()
        self._refresh_schedule()

    def _on_snapshot(self, snap) -> None:
        self.service_view.update_snapshot(snap)
        self.calling_view.update_snapshot(snap)
        self.sidebar.set_stats(snap.stats)

    # ---------------------------------------- Behavior
    def _on_notice(self, kind: str, message: str) -> None:
        self.toasts.show(message, kind)

    def _toast(self, kind: str, message: str) -> None:
        self.toasts.show(message, kind)

    def _result(self, res) -> None:
        self._toast("success" if res.ok else "warning", res.message)

    def _on_run(self) -> None:
        self._result(self.controller.toggle_run())

    def _on_generator_toggled(self, enabled: bool) -> None:
        self._result(self.controller.set_generator_enabled(enabled))

    def _on_export(self) -> None:
        dlg = ExportDialog(self.controller.export_dir, datetime.now().date(), self)
        try:
            if dlg.exec():
                self.controller.request_export(dlg.selected_day())
                self._toast("info", "Đang trích xuất dữ liệu...")
        finally:
            dlg.deleteLater()

    def _on_export_finished(self, result, error) -> None:
        if error:
            self._toast("error", f"Xuất CSV thất bại: {error}")
        elif result is not None and not result.written:
            self._toast("warning", f"Bỏ qua: {result.path.name} đã tồn tại")
        elif result is not None:
            self._toast("success", f"Đã xuất {result.rows:,} dòng ra {result.path.name}")

    def _on_employees(self) -> None:
        dlg = EmployeeDialog(self.controller, self._toast, self)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    def _on_open_data(self) -> None:
        folder = self.controller.export_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._toast("error", f"Không tạo được thư mục dữ liệu: {exc}")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self._toast("info", f"Đã mở thư mục {folder.name}")
        else:
            self._toast("error", f"Không mở được thư mục: {folder}")

    def _on_settings(self) -> None:
        dlg = SettingsDialog(self.controller.config, len(self.controller.employees()), self)
        dlg.apply_callback = self.controller.apply_config
        try:
            if dlg.exec():
                self._toast("success", dlg.message)
                self._refresh_schedule()
        finally:
            dlg.deleteLater()

    def _on_employee_action(self, employee_id: str, action: str) -> None:
        self._result(self.controller.employee_action(employee_id, action))

    def _on_abandon(self, call_id: str) -> None:
        self._result(self.controller.abandon_call(call_id))

    # -------------------------------------- QT
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.toasts.reposition()

    def closeEvent(self, event) -> None:
        self.controller.shutdown()
        super().closeEvent(event)
