"""Xuất CSV một ngày bằng dòng lệnh, KHÔNG cần mở giao diện.

    python -m storage.export_cli --day yesterday
    CallFlow.exe export --day 29-10-2026 --out D:\\baocao       (bản đã đóng gói)

Dùng với Windows Task Scheduler để vẫn có file CSV khi ứng dụng desktop đang đóng (xem README).
Mã thoát: 0 = đã xuất, 3 = ngày không có dữ liệu (không tạo file), 1 = lỗi (CSDL / ghi file), 2 = sai tham số.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from config.runtime import RuntimeConfig
from config.settings import ConfigError, load_settings
from config.timeutils import get_timezone
from database import CallRecordRepository, Database, DatabaseUnavailableError
from database.connection import describe_error
from storage.csv_exporter import ExportError, OnExists, export_day


def parse_day(text: str, today: date) -> date:
    t = text.strip().lower()
    if t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Ngày không hợp lệ: {text!r} (dùng today, yesterday, DD-MM-YYYY hoặc YYYY-MM-DD)")


def main(argv: Optional[Sequence[str]] = None, *, database: Optional[Database] = None,
         today: Optional[date] = None) -> int:
    ap = argparse.ArgumentParser(prog="export_cli", description="Xuất CSV cuộc gọi của một ngày")
    ap.add_argument("--day", default="yesterday", help="today | yesterday | DD-MM-YYYY (mặc định: yesterday)")
    ap.add_argument("--out", help="Thư mục xuất (mặc định: thư mục đã cấu hình trong ứng dụng)")
    ap.add_argument("--allow-empty", action="store_true", help="Vẫn tạo file (chỉ có tiêu đề) nếu ngày không có dữ liệu")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    own_db = database is None
    try:
        settings = load_settings()
        tz = get_timezone(settings.timezone)
        day = parse_day(args.day, today or datetime.now(tz).date())
        out = Path(args.out) if args.out else RuntimeConfig.resolve(RuntimeConfig.load().export_dir)
    except (ConfigError, ValueError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    db = database or Database.from_settings(settings.database)
    try:
        res = export_day(CallRecordRepository(db, tz), day, out, on_exists=OnExists.OVERWRITE,
                         skip_empty=not args.allow_empty)
    except DatabaseUnavailableError as exc:
        print(f"LỖI PostgreSQL: {exc}", file=sys.stderr)
        return 1
    except ExportError as exc:
        print(f"LỖI xuất file: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"LỖI: {describe_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if own_db:
            db.close()
    if res.empty:
        print(f"Ngày {day:%d/%m/%Y} không có cuộc gọi nào: không tạo file.")
        return 3
    print(f"Đã xuất {res.rows} dòng ra {res.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
