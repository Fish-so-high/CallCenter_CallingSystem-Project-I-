from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional, Tuple

PHONE_PATTERN = re.compile(r"^(09|08|02|03)\d{8}$")


class CallEvent(str, Enum):
    SUCCESS = "Success"   
    MISS = "Miss"        
    BREAK = "Break"       

    @classmethod
    def parse(cls, text: "str | CallEvent") -> "CallEvent":
        if isinstance(text, cls):
            return text
        key = str(text).strip().lower()
        for member in cls:
            if member.value.lower() == key:
                return member
        raise ValueError(f"Event không hợp lệ: {text!r} (chỉ nhận Success, Miss, Break)")


class CallStatus(str, Enum):
    WAITING = "WAITING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"
    ABANDONED = "ABANDONED"
    REJECTED = "REJECTED"


class CallTag(str, Enum):
    SUCCESS = "Success"
    MISS = "Miss"
    BREAK = "Break"
    REJECTED = "Rejected"    
    ABANDONED = "Abandoned"  


class EmployeeStatus(str, Enum):
    AVAILABLE = "AVAILABLE"   
    BUSY = "BUSY"            
    BREAK = "BREAK"          
    OFFLINE = "OFFLINE"     

EVENT_OUTCOME: Mapping[CallEvent, Tuple[CallStatus, CallTag]] = {
    CallEvent.SUCCESS: (CallStatus.COMPLETED, CallTag.SUCCESS),
    CallEvent.MISS: (CallStatus.MISSED, CallTag.MISS),
    CallEvent.BREAK: (CallStatus.ABANDONED, CallTag.BREAK),
}


def validate_phone(phone: str) -> str:
    phone = str(phone).strip()
    if not PHONE_PATTERN.match(phone):
        raise ValueError(f"Số điện thoại không hợp lệ: {phone!r} (10 chữ số, đầu 09/08/02/03)")
    return phone


@dataclass(frozen=True)
class IncomingCall:
    phone_number: str
    event: CallEvent
    time_call: float
    received_at: datetime
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        object.__setattr__(self, "phone_number", validate_phone(self.phone_number))
        object.__setattr__(self, "event", CallEvent.parse(self.event))
        t = float(self.time_call)
        if not math.isfinite(t) or t < 0:
            raise ValueError(f"time_call phải là số không âm, nhận được {self.time_call!r}")
        object.__setattr__(self, "time_call", t)
        if not self.call_id:
            raise ValueError("call_id không được rỗng")


@dataclass(eq=False)
class Call:
    call_id: str
    phone_number: str
    event: CallEvent
    time_call: float
    received_at: datetime
    status: CallStatus = CallStatus.WAITING
    employee_id: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    @classmethod
    def from_incoming(cls, inc: IncomingCall) -> "Call":
        return cls(inc.call_id, inc.phone_number, inc.event, inc.time_call, inc.received_at)


@dataclass(frozen=True)
class CallResult:
    call_id: str
    phone_number: str
    employee_id: Optional[str]
    received_at: datetime
    started_at: Optional[datetime]
    ended_at: datetime
    duration_seconds: float
    status: CallStatus
    tag: CallTag

    @classmethod
    def rejected(cls, call: Call, at: datetime) -> "CallResult":
        return cls(call.call_id, call.phone_number, None, call.received_at, None, at, 0.0,
                   CallStatus.REJECTED, CallTag.REJECTED)

    @classmethod
    def abandoned_waiting(cls, call: Call, at: datetime) -> "CallResult":
        return cls(call.call_id, call.phone_number, None, call.received_at, None, at, 0.0,
                   CallStatus.ABANDONED, CallTag.ABANDONED)


@dataclass
class Employee:
    employee_id: str
    name: str
    status: EmployeeStatus = EmployeeStatus.AVAILABLE
    pending_status: Optional[EmployeeStatus] = None

    @property
    def is_active(self) -> bool:
        return self.status != EmployeeStatus.OFFLINE


@dataclass(frozen=True)
class EmployeeChange:
    employee_id: str
    old_status: EmployeeStatus
    new_status: EmployeeStatus
    pending_status: Optional[EmployeeStatus]


# ---- Status_View -------------
@dataclass(frozen=True)
class WaitingCallView:
    call_id: str
    phone_number: str
    received_at: datetime


@dataclass(frozen=True)
class EmployeeView:
    employee_id: str
    name: str
    status: EmployeeStatus
    pending_status: Optional[EmployeeStatus]
    current_phone: Optional[str]
    queue_position: Optional[int]   


@dataclass(frozen=True)
class EngineSnapshot:
    waiting: Tuple[WaitingCallView, ...]
    service_queue: Tuple[str, ...]
    employees: Tuple[EmployeeView, ...]
    stats: Mapping[str, int]
    working_count: int    
    total_employees: int
    paused: bool
