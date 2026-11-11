from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class EventType(str, Enum):
    CALL_RECEIVED = "call_received"                      # payload: Call
    CALL_QUEUED = "call_queued"                          # payload: Call
    CALL_STARTED = "call_started"                        # payload: Call (đã gán nhân viên)
    CALL_FINISHED = "call_finished"                      # payload: CallResult
    EMPLOYEE_STATUS_CHANGED = "employee_status_changed"  # payload: EmployeeChange
    STATE_CHANGED = "state_changed"                      # payload: None - trạng thái hàng đợi


Handler = Callable[[EventType, Any], None]


class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: Dict[Optional[EventType], List[Handler]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: Optional[EventType], handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                lst = self._handlers.get(event_type, [])
                if handler in lst:
                    lst.remove(handler)

        return unsubscribe

    def publish(self, event_type: EventType, payload: Any = None) -> None:
        with self._lock:
            targets = list(self._handlers.get(event_type, ())) + list(self._handlers.get(None, ()))
        for handler in targets:
            try:
                handler(event_type, payload)
            except Exception:
                log.exception("Handler %r lỗi khi xử lý sự kiện %s", handler, event_type.value)
