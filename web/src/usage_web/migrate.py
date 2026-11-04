import re
from pathlib import Path

import psycopg

_FILE_RE = re.compile(r"^(\d{4})_.*\.sql$")
_LOCK = "SELECT pg_advisory_lock(hashtext('usage.migrations'))"
_UNLOCK = "SELECT pg_advisory_unlock(hashtext('usage.migrations'))"
_CREATE = (
    "CREATE TABLE IF NOT EXISTS usage.schema_migrations "
    "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
)


def _discover(migrations_dir: Path) -> list[tuple[str, Path]]:
    found = []
    for path in migrations_dir.iterdir():
        match = _FILE_RE.match(path.name)
        if match:
            found.append((match.group(1), path))
    return sorted(found)


def apply(owner_dsn: str, migrations_dir: Path) -> list[str]:
    """Apply pending NNNN_*.sql files in order, each in one transaction. Returns versions."""
    applied: list[str] = []
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(_LOCK)
        try:
            conn.execute(_CREATE)
            done = {r[0] for r in conn.execute("SELECT version FROM usage.schema_migrations")}
            for version, path in _discover(migrations_dir):
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute(
                        "INSERT INTO usage.schema_migrations (version) VALUES (%s) "
                        "ON CONFLICT DO NOTHING",
                        (version,),
                    )
                applied.append(version)
        finally:
            conn.execute(_UNLOCK)
    return applied
