from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional

from core.engine import EngineConfig, ReleasePolicy
from generator.test_case_generator import DEFAULT_EVENT_WEIGHTS, GeneratorConfig

from .settings import BASE_DIR, DEFAULT_EXPORT_DIR

SETTING_PATH = BASE_DIR / "data" / "settings.json"

def parse_hhmm(text: str):
    import re
    from datetime import time
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(text).strip())
    return time(int(m.group(1)), int(m.group(2))) if m else None


@dataclass
class RuntimeConfig:
    
    # ------- Egine ------- #
    max_queue_size: int = 100
    miss_release_policy: str = ReleasePolicy.REQUEUE.value
    break_release_policy: str = ReleasePolicy.REQUEUE.value
    
    # ------- Generator ------- #
    max_call: int = 20
    min_interval: float = 1.0
    max_interval: float = 3.0
    heavy_threshold: int = 50
    event_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EVENT_WEIGHTS))
    seed: Optional[int] = None
    source_code: str = "random"
    replay_file: str = "data/input_testcase.txt"
    record_generated: bool = False
    
    # ------- Export ------- #
    export_dir: str = str(DEFAULT_EXPORT_DIR)
    
    # ------- Automatic ------- #
    auto_schedule_enabled: bool = True
    auto_export_time: str = "19:00"
    
    def validate(self) -> List[str]:
        errs = list(self.generator_config().validate())
        if self.max_queue_size < 1:
            errs.append("Sức chứa Calling_queue phải >= 1")
        for name in (self.miss_release_policy, self.break_release_policy):
            if name not in {p.value for p in ReleasePolicy}:
                errs.append(f"Chính sách giải phóng nhân viên không hợp lệ: {name}")
        if self.source_mode not in ("random", "replay"):
            errs.append("Nguồn test case phải là random hoặc replay")
        if self.source_mode == "replay" and not Path(self.resolve(self.replay_file)).is_file():
            errs.append(f"Không tìm thấy file phát lại: {self.replay_file}")
        if self.record_generated and self.source_mode == "replay":
            errs.append("Không thể vừa phát lại vừa ghi lại test case vào cùng một file")
        if parse_hhmm(self.auto_export_time) is None:
            errs.append("Giờ xuất tự động phải có dạng HH:MM (00:00 - 23:59)")
        if not self.export_dir.strip():
            errs.append("Thư mục xuất CSV không được để trống")
        return errs
        
    @staticmethod
    def resolve(path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else BASE_DIR / p
    
    def generator_config(self) -> GeneratorConfig:
        return GeneratorConfig(self.max_call, self.min_interval, self.max_interval, 
                               self.heavy_threshold, dict(self.event_weights), self.seed)
        
    def engine_config(self) -> EngineConfig:
        return EngineConfig(max_queue_size=self.max_queue_size, miss_release_policy=self.miss_release_policy,
                            break_release_policy=self.break_release_policy)

    
    @classmethod
    def load(cls, path: Path = SETTING_PATH) -> "RuntimeConfig":
        try:
            raw =  json.load(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("Không đọc được %s (%s), dùng cấu hình mặc định", path, exc)
            return cls()
        
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        return cfg if not cfg.validate_loose() else cls()
    
    def validate_loose(self) -> List[str]:
        saved, self.source_code = self.source_code, "random"
        try: 
            return self.validate()
        finally:
            self.source_code = saved
            
    def save(self, path: Path = SETTING_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        


