from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget

from core.models import EmployeeStatus, EmployeeView, EngineSnapshot
from gui.widgets.flow_layout import FlowLayout

STATE_TEXT = {
    EmployeeStatus.AVAILABLE: "Rảnh", EmployeeStatus.BUSY: "Đang xử lý",
    EmployeeStatus.BREAK: "Tạm nghỉ", EmployeeStatus.OFFLINE: "Offline",
}
PENDING_TEXT = {EmployeeStatus.BREAK: "sắp nghỉ", EmployeeStatus.OFFLINE: "sắp offline"}


class AgentCard(QFrame):
    action_requested = Signal(str, str)   

    def __init__(self, employee_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.employee_id = employee_id
        self.setObjectName("agentCard")
        self.setFixedSize(138, 84)
        self.setProperty("state", "available")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(1)
        self.id_label = QLabel(employee_id); self.id_label.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.status_label = QLabel(""); self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("font-size: 11px;")
        for w in (self.id_label, self.status_label, self.detail_label):
            lay.addWidget(w)
        self._view: Optional[EmployeeView] = None

    @property
    def state(self) -> str:
        return self.property("state")

    def update_view(self, v: EmployeeView) -> None:
        if v == self._view:
            return
        self._view = v
        status = STATE_TEXT[v.status]
        if v.pending_status:
            status += f" ({PENDING_TEXT.get(v.pending_status, '')})"
        self.status_label.setText(status)
        if v.status == EmployeeStatus.AVAILABLE and v.queue_position:
            self.detail_label.setText(f"Hàng đợi #{v.queue_position}")
        elif v.status == EmployeeStatus.BUSY and v.current_phone:
            self.detail_label.setText(v.current_phone)
        else:
            self.detail_label.setText("")
        self.setToolTip(f"{v.employee_id} - {v.name}\nTrạng thái: {STATE_TEXT[v.status]}\nNhấn chuột phải để thao tác")
        self.setProperty("state", v.status.value.lower())
        self.style().unpolish(self); self.style().polish(self)

    def contextMenuEvent(self, event) -> None:
        v = self._view
        if v is None:
            return
        menu = QMenu(self)
        acts = []
        if v.status == EmployeeStatus.AVAILABLE or (v.status == EmployeeStatus.BUSY and v.pending_status is None):
            acts.append(("Cho tạm nghỉ" if v.status == EmployeeStatus.AVAILABLE else "Tạm nghỉ sau cuộc gọi", "break"))
        if v.status in (EmployeeStatus.BREAK, EmployeeStatus.OFFLINE):
            acts.append(("Cho quay lại hoạt động", "available"))
        if v.status == EmployeeStatus.BUSY and v.pending_status is not None:
            acts.append(("Hủy yêu cầu nghỉ", "available"))
        if v.status != EmployeeStatus.OFFLINE:
            acts.append(("Ngừng hoạt động (Offline)", "offline"))
        for text, action in acts:
            menu.addAction(text).setData(action)
        chosen = menu.exec(event.globalPos())
        if chosen is not None:
            self.action_requested.emit(self.employee_id, chosen.data())


class ServiceQueueView(QFrame):
    employee_action = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        title = QLabel("Service Queue"); title.setObjectName("sectionTitle")
        self.counter = QLabel("Số nhân viên hoạt động: 0 / 0"); self.counter.setObjectName("counter")
        head.addWidget(title); head.addStretch(1); head.addWidget(self.counter)
        root.addLayout(head)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); body.setObjectName("flowHost"); body_lay = QVBoxLayout(body); body_lay.setContentsMargins(0, 0, 0, 0)
        self._idle_caption = QLabel("ĐANG RẢNH · theo thứ tự nhận cuộc gọi"); self._idle_caption.setObjectName("groupCaption")
        self._idle_host = QWidget(); self._idle_host.setObjectName("flowHost"); self._idle_layout = FlowLayout(self._idle_host)
        self._idle_empty = QLabel("Không có nhân viên rảnh"); self._idle_empty.setObjectName("muted")
        self._busy_caption = QLabel("ĐANG BẬN · NGHỈ · OFFLINE"); self._busy_caption.setObjectName("groupCaption")
        self._busy_host = QWidget(); self._busy_host.setObjectName("flowHost"); self._busy_layout = FlowLayout(self._busy_host)
        self._busy_empty = QLabel("Không có nhân viên bận"); self._busy_empty.setObjectName("muted")
        for w in (self._idle_caption, self._idle_host, self._idle_empty, self._busy_caption, self._busy_host,
                  self._busy_empty):
            body_lay.addWidget(w)
        body_lay.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.cards: Dict[str, AgentCard] = {}
        self._signature: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None

    def update_snapshot(self, snap: EngineSnapshot) -> None:
        self.counter.setText(f"Số nhân viên hoạt động: {snap.working_count} / {snap.total_employees}")
        by_id = {e.employee_id: e for e in snap.employees}
        for emp_id, view in by_id.items():
            card = self.cards.get(emp_id)
            if card is None:
                card = self.cards[emp_id] = AgentCard(emp_id)
                card.action_requested.connect(self.employee_action)
            card.update_view(view)
        idle = tuple(i for i in snap.service_queue if i in by_id)
        others = tuple(i for i in by_id if i not in idle)
        sig = (idle, others)
        if sig != self._signature:
            self._signature = sig
            self._fill(self._idle_layout, self._idle_host, idle)
            self._fill(self._busy_layout, self._busy_host, others)
            self._idle_empty.setVisible(not idle)
            self._busy_empty.setVisible(not others)
            self._idle_host.setVisible(bool(idle))
            self._busy_host.setVisible(bool(others))

    def _fill(self, layout: FlowLayout, host: QWidget, ids: Tuple[str, ...]) -> None:
        while layout.count():
            layout.takeAt(0)
        for emp_id in ids:
            card = self.cards[emp_id]
            card.setParent(host)
            layout.addWidget(card)
            card.show()
        layout.invalidate()
        host.updateGeometry()
