from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QWidget

from gui.theme import TOAST_COLORS


class Toast(QLabel):
    WIDTH = 340

    def __init__(self, parent: QWidget, text: str, kind: str) -> None:
        super().__init__(text, parent)
        self.kind = kind
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Nhấn để đóng")
        self.setStyleSheet(f"background: {TOAST_COLORS.get(kind, TOAST_COLORS['info'])}; color: white; "
                           "padding: 10px 14px; border-radius: 9px; font-size: 13px;")
        self.setFixedWidth(self.WIDTH)
        self.ensurePolished()          
        text_h = QFontMetrics(self.font()).boundingRect(
            0, 0, self.WIDTH - 28, 10_000, int(Qt.TextFlag.TextWordWrap), text).height()
        self.setFixedHeight(max(40, text_h + 24))
        self.on_close = None

    def mousePressEvent(self, event) -> None:
        if self.on_close:
            self.on_close(self)


class ToastManager:
    MAX_VISIBLE = 4

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self.toasts: List[Toast] = []

    def show(self, message: str, kind: str = "info", duration_ms: int = 3500) -> Toast:
        while len(self.toasts) >= self.MAX_VISIBLE:
            self.dismiss(self.toasts[0])
        toast = Toast(self._parent, message, kind)
        toast.on_close = self.dismiss
        toast.show()
        toast.raise_()
        self.toasts.append(toast)
        self.reposition()
        QTimer.singleShot(duration_ms if kind != "error" else max(duration_ms, 6000),
                          lambda t=toast: self.dismiss(t))
        return toast

    def dismiss(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
            toast.hide()
            toast.deleteLater()
            self.reposition()

    def reposition(self) -> None:
        margin, gap = 16, 8
        y = self._parent.height() - margin
        for t in reversed(self.toasts):
            y -= t.height()
            t.move(self._parent.width() - t.width() - margin, y)
            t.raise_()
            y -= gap
