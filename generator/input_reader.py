from __future__ import annotations

import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core.models import CallEvent, IncomingCall

log = logging.getLogger(__name__)
Row = Tuple[str, CallEvent, float]

def parse_line(line: str) -> Optional[Row]:
    line = line.strip().lstrip("\ufeff")
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 3:
        return None
    try:
        inc = IncomingCall(parts[0], parts[1], float(parts[2].rstrip("sS")), datetime.min)
    except ValueError:
        return None
    return inc.phone_number, inc.event, inc.time_call

def format_row(phone: str, event: CallEvent, time_call: float) -> str:
    return f"{phone}|{event.value}|{time_call:.2f}"

def load_rows(path: Path) -> Tuple[List[Row], List[str]]:
    errors: List[str] = []
    rows: List[Row] = []
    for n, raw in enumerate(Path(path).read_text(encoding = "utf-8").splitlines(), 1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        row = parse_line(raw)
        if row is None:
            errors.append(f"Dòng {n}: sai định dạng phone_number|event|time_call ({raw!r})")
        else:
            rows.append(row)
    return rows, errors


class ReplaySource:
    
    def __init__(self, path: Path, config_provider, rng: random.Random, 
                 clock: Callable[[], datetime] = datetime.now
    ) -> None:
        self.rows, self.errors = load_rows(path)
        for e in self.errors:
            log.warning("%s: %s", Path(path).name, e)
        self._pos = 0
        self._cfg = config_provider
        self._rng = rng
        self._clock = clock
        
    @property
    def exhausted(self) -> bool:
        return self._pos >= len(self.rows)
    
    def next_interval(self) -> float:
        c = self._cfg()
        lo, hi = sorted((c.min_interval, c.max_interval))
        return self._rng.uniform(lo, hi)
    
    def make_batch(self, waiting: int = 0) -> List[IncomingCall]:
        c = self._cfg()
        n = self._rng.randint(1, max(1, c.effective_max_call(waiting)))
        chunk = self.rows[self._pos:self._pos + n]
        self._pos += len(chunk)
        now = self._clock()
        return [IncomingCall(p, e, t, now) for p, e, t in chunk]