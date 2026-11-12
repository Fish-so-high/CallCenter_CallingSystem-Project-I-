from __future__ import annotations

import logging
import queue
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.models import CallEvent, IncomingCall

log = logging.getLogger(__name__)

PHONE_PREFIXES = ("09", "08", "02", "03")
DEFAULT_EVENT_WEIGHTS: Dict[str, float] = {"Success": 70.0, "Miss": 10.0, "Break": 20.0}

@dataclass
class GeneratorConfig:
    max_call: int = 20
    min_interval: float = 1.0
    max_interval: float = 3.0
    heavy_threshold: int = 50
    event_weights: Dict[str, float] = field(default_factory = lambda: dict(DEFAULT_EVENT_WEIGHTS))
    seed: Optional[int] = None
    
    def validate(self) -> List[str]:
        errs = []
        if self.max_call < 1:
            errs.append("max_call phải >= 1")
        if self.min_interval < 0 or self.max_interval < 0:
            errs.append("Khoảng nghỉ giữa các batch không được âm")
        if self.heavy_threshold < 0:
            errs.append("Ngưỡng queue không được âm")
        w = [self.event_weightss.get(e.value, 0.0) for e in CallEvent]
        if any(x < 0 for x in w):
            errs.append("Xác suất event không được âm")
        elif sum(w) <= 0:
            errs.append("Tổng xác suất của các event phải > 0")
        return errs
    
    def effective_max_call(self, waiting: int) -> int:
        return max(1, self.max_call // 2) if waiting > self.heavy_threshold else self.max_call
    

def random_phone(rng: random.Random) -> str:
    return rng.choice(PHONE_PREFIXES) + "".join(rng.choice("0123456789") for _ in range(8))


def random_time_call(event: CallEvent, rng: random.Random) -> float:
    if event is CallEvent.SUCCESS:
        return round(rng.uniform(0, 3), 2)
    if event is CallEvent.BREAK:
        return round(rng.uniform(0, 1), 2)
    return 0.0


class RandomSource:
    def __init__(self, config_provider: Callable[[], GeneratorConfig], rng: random.Random, clock: Callable[[], datetime] = datetime.now) -> None:
        self._cfg = config_provider
        self._rng = rng
        self._clock = clock
        
    exhausted = False
    
    def next_interval(self) -> float:
        c = self._cfg()
        lo, hi = sorted((c.min_interval, c.max_interval))
        return self._rng.uniform(lo, hi)
    
    def make_batch(self, waiting: int = 0) -> List[IncomingCall]:
        c = self._cfg()
        events = list(CallEvent)
        weights = [max(c.event_weights.get(e.value, 0, 0), 0.0) for e in events]
        n = self._rng.randint(1, max(1, c.effective_max_call(waiting)))
        now = self._clock()
        out = []
        
        for _ in range(n):
            ev = self._rng.choices(events, weights = weights, k = 1)[0]
            out.append(IncomingCall(random_phone(self._rng), ev, random_time_call(ev, self._rng), now))
        return out
    
class GeneratorWorker(threading.Thread):
    def __int__(self, source, out: "queue.Queue[IncomingCall]", 
                waiting_provider: Callable[[], int] = lambda: 0, 
                on_exhausted: Optional[Callable[[], None]] = None,
                record_path: Optional[Path] = None
    ) -> None:
        super().__init__(name = "callflow-generator", daemon = True)
        self.source, self.out, self._waiting = source, out, waiting_provider
        self._on_exhausted = on_exhausted
        self._record_path = Path(record_path) if record_path else None
        self._stop_evt = threading.Event()
        self._enabled = threading.Event()
        self.dropped = 0
        self.produced = 0
        
    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()
    
    def enable(self) -> None:
        self._enabled.set()
        
    def disable(self) -> None:
        self._enabled.clear()
        
    def stop(self) -> None:
        self._stop_evt.set()
        self._enabled.set()
    
    def __record(self, batch: List[IncomingCall]) -> None:
        if not self._record_path or not batch:
            return
        try:
            self._record_path.parent.mkdir(parents = True, exist_ok = True)
            with open(self._record_path, "a", encoding = "utf-8", newline = "\n") as f:
                f.write("".join(f"{c.phone_number}|{c.event.value}|{c.time_call:.2f}\n" for c in batch))
        except OSError as exc:
            log.error("Không ghi được test case ra %s: %s", self._record_path, exc)
            self._record_path = None
    
    def run(self) -> None:
        while not self._stop_evt.is_set():
            if not self._enabled.wait(0.1):
                continue
            if self._stop_evt.is_set():
                break
            try:
                batch = self.source.make_batch(self._waiting())
                for call in batch:
                    try:
                        self.out.put_nowait(call)
                        self.produced += 1
                    except queue.Full:
                        self.dropped += 1
                self.__record(batch)
                if self.source.exhausted:
                    log.info("Nguồn test_case đã hết")
                    self._enabled.clear()
                    if self._on_exhausted:
                        self._on_exhausted()
                    continue
                self._stop_evt.wait(self.source.next_interval())
            except Exception:
                log.exception("Generator lỗi, thử lại sau vài giây")
                self._stop_evt.wait(1.0)    
