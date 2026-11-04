from pathlib import Path

import psycopg
import pytest

from usage_web.migrate import apply

pytestmark = pytest.mark.integration

REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _versions(dsn):
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT version FROM usage.schema_migrations ORDER BY version")
        return [r[0] for r in rows.fetchall()]


def test_migrate_applies_in_order_and_skips_applied(usage_schema_dsn, tmp_path):
    (tmp_path / "0002_second.sql").write_text(
        "CREATE TABLE usage.second (n int); INSERT INTO usage.second VALUES (2);"
    )
    (tmp_path / "0001_first.sql").write_text(
        "CREATE TABLE usage.first (n int); INSERT INTO usage.first VALUES (1);"
        "INSERT INTO usage.schema_migrations (version) VALUES ('0001') ON CONFLICT DO NOTHING;"
    )
    (tmp_path / "notes.txt").write_text("ignored")

    assert apply(usage_schema_dsn, tmp_path) == ["0001", "0002"]
    assert _versions(usage_schema_dsn) == ["0001", "0002"]
    assert apply(usage_schema_dsn, tmp_path) == []


def test_migrate_rolls_back_failed_file(usage_schema_dsn, tmp_path):
    (tmp_path / "0001_bad.sql").write_text("CREATE TABLE usage.t (n int); SELECT 1/0;")
    with pytest.raises(psycopg.Error):
        apply(usage_schema_dsn, tmp_path)
    assert _versions(usage_schema_dsn) == []
    with psycopg.connect(usage_schema_dsn) as conn:
        assert conn.execute("SELECT to_regclass('usage.t')").fetchone()[0] is None


def test_migrate_real_migrations_create_hypertable(usage_schema_dsn):
    assert apply(usage_schema_dsn, REAL_MIGRATIONS) == ["0001", "0002"]
    with psycopg.connect(usage_schema_dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = 'usage' AND hypertable_name = 'events'"
        ).fetchone()
    assert row is not None
