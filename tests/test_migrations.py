"""Test migration + lược đồ (cần PostgreSQL: TEST_DATABASE_URL)."""
import shutil
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from database.migrate import MIGRATIONS_DIR, MigrationError, apply_migrations, discover
from database.models import Base


def test_discover_finds_initial_migration():
    ms = discover()
    assert [m.version for m in ms][:1] == ["0001"] and len(ms[0].checksum) == 64


def test_apply_creates_schema_and_is_idempotent(fresh_schema_engine):
    assert apply_migrations(fresh_schema_engine) == ["0001"]
    assert apply_migrations(fresh_schema_engine) == []
    tables = set(inspect(fresh_schema_engine).get_table_names())
    assert {"employees", "call_records", "employee_monthly_stats", "schema_migrations"} <= tables


def test_failed_migration_rolls_back_everything(fresh_schema_engine, tmp_path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE a (id int);", encoding="utf-8")
    (tmp_path / "0002_bad.sql").write_text("CREATE TABLE b (id int);\nTHIS IS NOT SQL;", encoding="utf-8")
    with pytest.raises(Exception):
        apply_migrations(fresh_schema_engine, tmp_path)
    names = set(inspect(fresh_schema_engine).get_table_names())
    assert "a" not in names and "b" not in names and "schema_migrations" not in names


def test_modified_applied_migration_is_detected(fresh_schema_engine, tmp_path):
    f = tmp_path / "0001_x.sql"
    f.write_text("CREATE TABLE a (id int);", encoding="utf-8")
    apply_migrations(fresh_schema_engine, tmp_path)
    f.write_text("CREATE TABLE a (id bigint);", encoding="utf-8")
    with pytest.raises(MigrationError, match="bị sửa"):
        apply_migrations(fresh_schema_engine, tmp_path)


def test_database_newer_than_code_is_detected(fresh_schema_engine, tmp_path):
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (id int);", encoding="utf-8")
    (tmp_path / "0002_b.sql").write_text("CREATE TABLE b (id int);", encoding="utf-8")
    apply_migrations(fresh_schema_engine, tmp_path)
    (tmp_path / "0002_b.sql").unlink()
    with pytest.raises(MigrationError, match="không có trong mã nguồn"):
        apply_migrations(fresh_schema_engine, tmp_path)


def test_new_migration_is_applied_incrementally(fresh_schema_engine, tmp_path):
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (id int);", encoding="utf-8")
    apply_migrations(fresh_schema_engine, tmp_path)
    (tmp_path / "0002_b.sql").write_text("CREATE TABLE b (id int);", encoding="utf-8")
    assert apply_migrations(fresh_schema_engine, tmp_path) == ["0002"]


def test_bad_filename_and_duplicate_version(tmp_path):
    (tmp_path / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover(tmp_path)
    (tmp_path / "init.sql").unlink()
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    assert len(discover(tmp_path)) == 1


# ------------------------------------------------------ ORM khớp với SQL migration
def test_orm_models_match_migrated_schema(migrated_engine):
    insp = inspect(migrated_engine)
    for table in Base.metadata.sorted_tables:
        real_cols = {c["name"]: c for c in insp.get_columns(table.name)}
        assert set(real_cols) == {c.name for c in table.columns}, table.name
        for col in table.columns:
            assert real_cols[col.name]["nullable"] == (col.nullable and not col.primary_key), (table.name, col.name)
        assert set(insp.get_pk_constraint(table.name)["constrained_columns"]) == {c.name for c in table.primary_key}
        fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys(table.name)}
        orm_fks = {((fk.parent.name,), fk.column.table.name) for fk in table.foreign_keys}
        assert fks == orm_fks, table.name
        real_checks = {c["name"] for c in insp.get_check_constraints(table.name)}
        orm_checks = {c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
        assert real_checks == orm_checks, table.name
        assert {i["name"] for i in insp.get_indexes(table.name)} == {i.name for i in table.indexes}, table.name


# --------------------------------------------------------- ràng buộc ở mức CSDL
@pytest.fixture
def conn(db):
    with db.engine.begin() as c:
        c.execute(text("INSERT INTO employees (employee_id, employee_name) VALUES ('E1', 'x')"))
        yield c
        c.rollback() if False else None


def _insert(c, **over):
    row = dict(call_id="c1", phone="0912345678", emp="E1", rec="2026-10-29 10:00+07", st="2026-10-29 10:00:01+07",
               en="2026-10-29 10:00:03+07", dur=2, tag="Success")
    row.update(over)
    c.execute(text("INSERT INTO call_records (call_id, phone_number, employee_id, received_at, started_at, "
                   "ended_at, duration_seconds, tag) VALUES (:call_id, :phone, :emp, :rec, :st, :en, :dur, :tag)"), row)


@pytest.mark.parametrize("bad", [
    {"tag": "success"}, {"tag": "Bogus"}, {"phone": "0412345678"}, {"phone": "091234567"},
    {"dur": -1}, {"en": "2026-10-29 09:59:00+07"}, {"emp": None}, {"emp": "NOPE"},
])
def test_database_rejects_invalid_rows(db, bad):
    with db.engine.connect() as c:
        c.execute(text("INSERT INTO employees (employee_id, employee_name) VALUES ('E1', 'x') ON CONFLICT DO NOTHING"))
        c.commit()
        with pytest.raises(IntegrityError):
            _insert(c, **bad)
        c.rollback()


def test_rejected_without_employee_is_allowed(db):
    with db.engine.begin() as c:
        _insert(c, tag="Rejected", emp=None, st=None, en="2026-10-29 10:00:00+07", dur=0)
