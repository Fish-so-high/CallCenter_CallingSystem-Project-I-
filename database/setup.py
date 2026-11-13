from __future__ import annotations

import logging
import re
import sys

from typing import Optional, Sequence

from sqlalchemy import create_engine, text

from config.settings import ConfigError, DatabaseSettings, load_settings
from database.connection import Database, DatabaseUnavailableError, describe_error
from database.migrate import MigrationError, apply_migrations

log = logging.getLogger(__name__)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# ----- Create Database ------ #
def ensure_database(settings: DatabaseSettings) -> bool:
    if not _IDENT.match(settings.name):
        raise ConfigError(f"Tên Database không hợp lệ: {settings.name!r} (tên databse chỉ được sử dụng các ký tự chữ, số và dấu '_')")
    admin_url = settings.sqlalchemy_url().set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT",
                           connect_args={"connect_timeout": settings.connect_timeout})
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                  {"n": settings.name}
            ).scalar()
            if exists:
                return False
            conn.execute(text(f'CREATE DATABASE "{settings.name}"'))
            log.info("Đã tạo database %s", settings.name)
            return True
    finally:
        engine.dispose()
        
def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = load_settings().database
        created = ensure_database(settings)
        print(f"Database '{settings.name}': {'đã tạo mới' if created else 'đã tồn tại'}")
        db = Database.from_settings(settings)
        try:
            applied = apply_migrations(db.engine)
        finally:
            db.close()
        print("Migration đã áp dụng:", ", ".join(applied) if applied  else "(không có gì mới)")
        return 0
    except (ConfigError, MigrationError) as exc:
        print(f"Lỗi cấu hình/migration: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Lỗi PostgreSQL: {describe_error(exc)}", file=sys.stderr)
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
        
        
        