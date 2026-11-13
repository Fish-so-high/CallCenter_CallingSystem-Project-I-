from __future__ import annotations
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.engine import Engine


log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_ADVISORY_LOCK_KEY = 7_262_020_001


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str
    
    
def discover(directory: Path = MIGRATIONS_DIR) -> List[Migration]:
    found: Dict[str, Migration] = {}
    for path in sorted(Path(directory).glob("*.sql")):
        m = _FILE_RE.match(path.name)
        if not m:
            raise MigrationError(f"Tên file migration sai định dạng NNNN_ten.sql: {path.name}")
        if m.group(1) in found:
            raise MigrationError(f"Trùng số phiên bản migration: {m.group(1)}")
        sql = path.read_text(encoding="utf-8")
        found[m.group(1)] = Migration(m.group(1), m.group(2), sql, hashlib.sha256(sql.encode()).hexdigest())
    return [found[v] for v in sorted(found)]

def apply_migrations(engine: Engine, directory: Path = MIGRATIONS_DIR) -> List[str]:
    migrations = discover(directory)
    applied_now: List[str] = []
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version VARCHAR(16) PRIMARY KEY, name TEXT NOT NULL, checksum CHAR(64) NOT NULL,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
        done = {r[0]: r[1] for r in conn.execute(text("SELECT version, checksum FROM schema_migrations"))}
        known = {m.version for m in migrations}
        unknown = sorted(set(done) - known)
        if unknown:
            raise MigrationError(f"CSDL đã áp dụng migration không có trong mã nguồn: {unknown} "
                                 "(phiên bản ứng dụng cũ hơn CSDL?)")
        for m in migrations:
            if m.version in done:
                if done[m.version].strip() != m.checksum:
                    raise MigrationError(f"Migration {m.version}_{m.name}.sql đã bị sửa sau khi áp dụng")
                continue
            log.info("Áp dụng migration %s_%s", m.version, m.name)
            for statement in _split_statements(m.sql):
                conn.exec_driver_sql(statement)
            conn.execute(text("INSERT INTO schema_migrations (version, name, checksum) VALUES (:v, :n, :c)"),
                         {"v": m.version, "n": m.name, "c": m.checksum})
            applied_now.append(m.version)
    return applied_now


def _split_statements(sql: str) -> List[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
    



