from __future__ import annotations

import csv
import itertools
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Protocol

from database.repositories import StoredCall

log = logging.getLogger(__name__)

HEADERS = (
    "Số điện thoại", "Nhân viên trả lời", "Thời gian nhận cuộc gọi",
    "Thời gian cuộc gọi kết thúc", "Tổng thời gian xử lý (giây)", "Tag",
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ExportError(RuntimeError):
    pass


class OnExists(str, Enum):
    OVERWRITE = "overwrite"   
    SKIP = "skip"             
    ERROR = "error"           


class CallSource(Protocol):
    def iter_by_day(self, day: date) -> Iterable[StoredCall]: ...


@dataclass(frozen=True)
class ExportResult:
    path: Path
    day: date
    rows: int
    existed: bool     
    written: bool      
    empty: bool = False  


def csv_filename(day: date) -> str:
    return f"calls-{day:%d-%m-%Y}.csv"


def _fmt_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _safe(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _open_temp(directory: Path, stem: str):
    path = Path(directory) / f".{stem}.{uuid.uuid4().hex[:8]}.tmp"
    return str(path), open(path, "x", newline="", encoding="utf-8-sig")


def export_day(source: CallSource, day: date, export_dir: Path, *,
               on_exists: OnExists = OnExists.OVERWRITE, skip_empty: bool = False) -> ExportResult:
    export_dir = Path(export_dir)
    target = export_dir / csv_filename(day)
    existed = target.exists()
    if existed:
        if on_exists == OnExists.SKIP:
            log.info("Bỏ qua xuất %s: file đã tồn tại", target.name)
            return ExportResult(target, day, 0, True, False)
        if on_exists == OnExists.ERROR:
            raise ExportError(f"File đã tồn tại: {target}")
        if target.is_dir():
            raise ExportError(f"Đường dẫn xuất là một thư mục: {target}")

    tmp_path: Optional[str] = None
    rows = 0
    try:
        rows_iter = iter(source.iter_by_day(day))
        first = next(rows_iter, None)
        if first is None and skip_empty:
            log.info("Ngày %s không có cuộc gọi: không tạo %s", day.strftime("%d-%m-%Y"), target.name)
            return ExportResult(target, day, 0, existed, False, True)
        export_dir.mkdir(parents=True, exist_ok=True)
        tmp_path, f = _open_temp(export_dir, target.stem)
        with f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)
            for rec in itertools.chain([] if first is None else [first], rows_iter):
                writer.writerow([
                    _safe(rec.phone_number), _safe(rec.employee_id or ""), _fmt_dt(rec.received_at),
                    _fmt_dt(rec.ended_at), f"{rec.duration_seconds:.2f}", rec.tag.value,
                ])
                rows += 1
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)   
        tmp_path = None
    except Exception as exc:
        if isinstance(exc, PermissionError):
            msg = f"Không ghi được {target.name}: file đang được mở bởi chương trình khác hoặc thiếu quyền ghi"
        elif isinstance(exc, OSError):
            msg = f"Lỗi ghi file {target}: {exc.strerror or exc}"
        else:
            msg = f"Xuất CSV ngày {day:%d-%m-%Y} thất bại: {exc}"
        log.error(msg)
        raise ExportError(msg) from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    log.info("Đã xuất %d dòng ra %s%s", rows, target, " (ghi đè)" if existed else "")
    return ExportResult(target, day, rows, existed, True)
