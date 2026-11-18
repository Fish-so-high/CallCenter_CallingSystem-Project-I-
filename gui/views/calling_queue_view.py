from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListView, QMenu, QStackedLayout, QVBoxLayout, QWidget

from core.models import EngineSnapshot, WaitingCallView

CALL_ID_ROLE = Qt.ItemDataRole.UserRole + 1


class CallingQueueModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: Tuple[WaitingCallView, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{index.row() + 1:>3}.  {item.phone_number}    nhận lúc {item.received_at:%H:%M:%S}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Nhấn chuột phải: khách cúp máy (xóa khỏi hàng đợi)\nMã cuộc gọi: {item.call_id}"
        if role == CALL_ID_ROLE:
            return item.call_id
        return None

    def set_items(self, items: Sequence[WaitingCallView]) -> bool:
        items = tuple(items)
        if [i.call_id for i in items] == [i.call_id for i in self._items]:
            return False
        self.beginResetModel()
        self._items = items
        self.endResetModel()
        return True


class CallingQueueView(QFrame):
    abandon_requested = Signal(str)     

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        title = QLabel("Calling Queue"); title.setObjectName("sectionTitle")
        self.counter = QLabel("Số cuộc gọi: 0"); self.counter.setObjectName("counter")
        head.addWidget(title); head.addStretch(1); head.addWidget(self.counter)
        root.addLayout(head)

        self.model = CallingQueueModel(self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setAlternatingRowColors(True)
        self.list.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.list.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.empty = QLabel("Không có cuộc gọi đang chờ"); self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        host = QWidget(); stack = QStackedLayout(host); stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(self.list); stack.addWidget(self.empty)
        self._stack = stack
        root.addWidget(host, 1)

    def update_snapshot(self, snap: EngineSnapshot) -> None:
        self.counter.setText(f"Số cuộc gọi: {len(snap.waiting)}")
        self.model.set_items(snap.waiting)
        self._stack.setCurrentWidget(self.list if snap.waiting else self.empty)

    def _menu(self, pos) -> None:
        idx = self.list.indexAt(pos)
        if not idx.isValid():
            return
        call_id = idx.data(CALL_ID_ROLE)
        menu = QMenu(self)
        act = menu.addAction("Khách cúp máy (xóa khỏi hàng đợi)")
        if menu.exec(self.list.viewport().mapToGlobal(pos)) is act:
            self.abandon_requested.emit(call_id)
