from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.models import EmployeeStatus, EngineSnapshot
from gui.controllers import AppController
from gui.views.service_queue_view import PENDING_TEXT, STATE_TEXT


class EmployeeForm(QDialog):
    def __init__(self, controller: AppController, employee_id: str = "", name: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._editing = bool(employee_id)
        self.setWindowTitle("Sửa nhân viên" if self._editing else "Thêm nhân viên")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.id_edit = QLineEdit(employee_id); self.id_edit.setEnabled(not self._editing)
        self.id_edit.setPlaceholderText("Ví dụ: NV011 (duy nhất, không chứa khoảng trắng hoặc |)")
        self.name_edit = QLineEdit(name); self.name_edit.setPlaceholderText("Họ và tên")
        form.addRow("Mã nhân viên:", self.id_edit)
        form.addRow("Họ tên:", self.name_edit)
        lay.addLayout(form)
        self.error = QLabel(""); self.error.setStyleSheet("color: #ff8d92;"); self.error.setWordWrap(True)
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Lưu")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.accepted.connect(self._submit); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.message = ""

    def _submit(self) -> None:
        eid, name = self.id_edit.text().strip(), self.name_edit.text().strip()
        res = (self._controller.rename_employee(eid, name) if self._editing
               else self._controller.add_employee(eid, name))
        if res.ok:
            self.message = res.message
            self.accept()
        else:
            self.error.setText(res.message)


class EmployeeDialog(QDialog):
    COLUMNS = ("Mã", "Họ tên", "Trạng thái", "Đang chờ chuyển")

    def __init__(self, controller: AppController, notify, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._notify = notify           
        self.setWindowTitle("Danh sách nhân viên")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 90); self.table.setColumnWidth(1, 220); self.table.setColumnWidth(2, 120)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.add_btn = self._btn("Thêm...", self._add, row)
        self.edit_btn = self._btn("Sửa tên...", self._edit, row)
        self.break_btn = self._btn("Tạm nghỉ", lambda: self._act("break"), row)
        self.back_btn = self._btn("Quay lại", lambda: self._act("available"), row)
        self.off_btn = self._btn("Offline", lambda: self._act("offline"), row)
        row.addStretch(1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.open_btn = self._btn("Mở employees.txt", self._open_file, row2)
        row2.addStretch(1)
        close = QPushButton("Đóng"); close.clicked.connect(self.accept); row2.addWidget(close)
        lay.addLayout(row2)

        controller.snapshot_ready.connect(self.refresh)
        self.refresh(controller.snapshot())

    def done(self, result: int) -> None:
        try:
            self._controller.snapshot_ready.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        super().done(result)

    @staticmethod
    def _btn(text, slot, layout) -> QPushButton:
        b = QPushButton(text); b.clicked.connect(slot); layout.addWidget(b); return b

    def refresh(self, snap: EngineSnapshot) -> None:
        selected = self.selected_id()
        self.table.setRowCount(len(snap.employees))
        for r, e in enumerate(snap.employees):
            pending = PENDING_TEXT.get(e.pending_status, "") if e.pending_status else ""
            for c, text in enumerate((e.employee_id, e.name, STATE_TEXT[e.status], pending)):
                self.table.setItem(r, c, QTableWidgetItem(text))
            if e.employee_id == selected:
                self.table.selectRow(r)

    def selected_id(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.table.item(rows[0].row(), 0).text() if rows else None

    def _need_selection(self) -> Optional[str]:
        eid = self.selected_id()
        if eid is None:
            self._notify("warning", "Hãy chọn một nhân viên trong danh sách")
        return eid

    def _add(self) -> None:
        form = EmployeeForm(self._controller, parent=self)
        if form.exec():
            self._notify("success", form.message)

    def _edit(self) -> None:
        eid = self._need_selection()
        if eid:
            name = self.table.item(self.table.currentRow(), 1).text()
            form = EmployeeForm(self._controller, eid, name, parent=self)
            if form.exec():
                self._notify("success", form.message)

    def _act(self, action: str) -> None:
        eid = self._need_selection()
        if eid:
            res = self._controller.employee_action(eid, action)
            self._notify("success" if res.ok else "warning", res.message)

    def _open_file(self) -> None:
        path = self._controller.employees_file
        if path is None or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._notify("error", "Không mở được file employees.txt")
        else:
            self._notify("info", "Đã mở employees.txt. Thay đổi trong file có hiệu lực khi khởi động lại ứng dụng.")
