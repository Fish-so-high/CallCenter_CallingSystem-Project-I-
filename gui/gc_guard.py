from __future__ import annotations

import gc
from typing import Optional

from PySide6.QtCore import QObject, QTimer


class GuiGarbageCollector(QObject):
    def __init__(self, parent: Optional[QObject] = None, interval_ms: int = 250) -> None:
        super().__init__(parent)
        self._was_enabled = gc.isenabled()
        gc.disable()
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.collect)
        self._timer.start()

    def collect(self) -> None:
        self._tick += 1
        if self._tick % 120 == 0:
            gc.collect(2)          
        elif self._tick % 8 == 0:
            gc.collect(1)         
        else:
            gc.collect(0)

    def stop(self) -> None:
        self._timer.stop()
        gc.collect()
        if self._was_enabled:
            gc.enable()
