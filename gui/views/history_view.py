from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QTableView, QVBoxLayout, QWidget

from core.models import CallResult
from gui.theme import TAG_COLORS

HEADERS = ("Số điện thoại", "Nhân viên trả lời", "Thời gian nhận cuộc gọi", "Thời gian cuộc gọi kết thúc",
           "Tổng thời gian xử lý", "Tag")


class HistoryModel(QAbstractTableModel):
    MAX_ROWS = 5000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[CallResult] = []      # mới nhất ở đầu
        self.total = 0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = self._rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (r.phone_number, r.employee_id or "—", f"{r.received_at:%H:%M:%S}",
                    f"{r.ended_at:%H:%M:%S}", f"{r.duration_seconds:.2f} s", r.tag.value)[c]
        if role == Qt.ItemDataRole.ToolTipRole and c in (2, 3):
            return f"{(r.received_at if c == 2 else r.ended_at):%d/%m/%Y %H:%M:%S.%f}"[:-3]
        if role == Qt.ItemDataRole.ForegroundRole and c == 5:
            return QColor(TAG_COLORS.get(r.tag.value, "#ffffff"))
        if role == Qt.ItemDataRole.FontRole and c == 5:
            from PySide6.QtGui import QFont
            f = QFont(); f.setBold(True); return f
        if role == Qt.ItemDataRole.TextAlignmentRole and c == 4:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def add_results(self, results: Sequence[CallResult]) -> None:
        if not results:
            return
        newest_first = list(reversed(results))
        self.beginInsertRows(QModelIndex(), 0, len(newest_first) - 1)
        self._rows[0:0] = newest_first
        self.endInsertRows()
        self.total += len(results)
        extra = len(self._rows) - self.MAX_ROWS
        if extra > 0:
            self.beginRemoveRows(QModelIndex(), self.MAX_ROWS, len(self._rows) - 1)
            del self._rows[self.MAX_ROWS:]
            self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.total = 0
        self.endResetModel()


class HistoryView(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        title = QLabel("Lịch sử cuộc gọi"); title.setObjectName("sectionTitle")
        self.counter = QLabel("0 cuộc gọi"); self.counter.setObjectName("counter")
        head.addWidget(title); head.addStretch(1); head.addWidget(self.counter)
        root.addLayout(head)
        self.model = HistoryModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for col, mode in enumerate((QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.Stretch,
                                    QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                                    QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Fixed)):
            header.setSectionResizeMode(col, mode)
        header.resizeSection(5, 110)     
        root.addWidget(self.table, 1)

    def add_results(self, results: Sequence[CallResult]) -> None:
        self.model.add_results(results)
        shown = self.model.rowCount()
        self.counter.setText(f"{self.model.total:,} cuộc gọi" + (f" (hiển thị {shown:,} gần nhất)" if shown < self.model.total else ""))

    def clear(self) -> None:
        self.model.clear()
        self.counter.setText("0 cuộc gọi")
