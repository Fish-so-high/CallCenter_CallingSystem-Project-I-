from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout, QWidget

from storage import csv_filename


class ExportDialog(QDialog):
    def __init__(self, export_dir: Path, default_day: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trích xuất dữ liệu CSV")
        self.setMinimumWidth(460)
        self._dir = Path(export_dir)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Bạn có muốn trích xuất dữ liệu cuộc gọi ra file CSV không?"))
        form = QFormLayout()
        self.date_edit = QDateEdit(QDate(default_day.year, default_day.month, default_day.day))
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.target = QLabel(); self.target.setWordWrap(True); self.target.setObjectName("muted")
        form.addRow("Ngày dữ liệu:", self.date_edit)
        form.addRow("File xuất:", self.target)
        lay.addLayout(form)
        note = QLabel("Nếu file của ngày này đã tồn tại, nó sẽ được thay bằng dữ liệu mới nhất.")
        note.setObjectName("muted"); note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Xuất CSV")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.date_edit.dateChanged.connect(self._refresh)
        self._refresh()

    def selected_day(self) -> date:
        return self.date_edit.date().toPython()

    def _refresh(self) -> None:
        self.target.setText(str(self._dir / csv_filename(self.selected_day())))
