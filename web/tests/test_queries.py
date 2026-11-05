from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from usage_web import queries
from usage_web.migrate import apply

pytestmark = pytest.mark.integration

REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
TZ = "America/Chicago"
CHI = ZoneInfo(TZ)


def local(hour: int, minute: int = 0, day: int = 20) -> datetime:
    """Local (America/Chicago, UTC-6 in late November) wall time on 2026-11-<day>."""
    return datetime(2026, 11, day, hour, minute, tzinfo=CHI)


class Seeder:
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.n = 0

    def add(self, device, kind, start, end, app=None, domain=None, idle=False, title=None):
        self.n += 1
        self.conn.execute(
            "INSERT INTO usage.events (device, ts_start, ts_end, kind, app, title, url, domain,"
            " idle, source_bucket, source_event_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (device, start, end, kind, app, title, None, domain, idle, "bucket", self.n),
        )


@pytest.fixture
def seed(usage_schema_dsn):
    apply(usage_schema_dsn, REAL_MIGRATIONS)
    s = Seeder(usage_schema_dsn)
    yield s
    s.conn.close()


def day_filter(**kw):
    params = {"devices": [], "t_from": local(0), "t_to": local(0, day=21), "tz": TZ}
    return queries.Filters(**{**params, **kw})


def by_hour(rows):
    out: dict[int, float] = {}
    for r in rows:
        hour = r.bucket.astimezone(CHI).hour
        out[hour] = out.get(hour, 0) + r.seconds
    return out


def test_hourly_splits_long_event_across_hours(seed):
    seed.add("desk-a", "window", local(10, 30), local(13, 15), app="editor")
    rows = queries.hourly(seed.conn, day_filter())
    assert by_hour(rows) == {10: 1800, 11: 3600, 12: 3600, 13: 900}


def test_hourly_excludes_afk_and_idle(seed):
    seed.add("desk-a", "window", local(10), local(11), app="editor")
    seed.add("desk-a", "afk", local(10, 20), local(10, 40), idle=True)
    seed.add("desk-a", "afk", local(10, 45), local(10, 50), idle=False)  # active: ignored
    seed.add("desk-b", "afk", local(10, 0), local(11, 0), idle=True)  # other device: ignored
    rows = queries.hourly(seed.conn, day_filter())
    assert by_hour(rows) == {10: 2400}
    assert {r.app for r in rows} == {"editor"}


def test_hourly_clips_to_range_and_filters_app(seed):
    seed.add("desk-a", "window", local(9, 30), local(10, 30), app="editor")
    seed.add("desk-a", "window", local(10, 30), local(11), app="Browser")
    assert by_hour(queries.hourly(seed.conn, day_filter(t_from=local(10)))) == {10: 3600}
    assert by_hour(queries.hourly(seed.conn, day_filter(app="browser"))) == {10: 1800}


def test_top_apps_hour_filter(seed):
    seed.add("desk-a", "window", local(9), local(12), app="editor")
    seed.add("desk-a", "window", local(14), local(15), app="browser")
    rows = queries.top_apps(seed.conn, day_filter(hour_from=10, hour_to=11))
    assert [(r.app, r.seconds) for r in rows] == [("editor", 7200)]
    rows = queries.top_apps(seed.conn, day_filter(), limit=1)
    assert [(r.app, r.seconds) for r in rows] == [("editor", 10800)]


def test_top_domains_uses_web_events(seed):
    seed.add("desk-a", "web", local(9), local(10), domain="example.org")
    seed.add("desk-a", "window", local(9), local(10), app="browser")
    rows = queries.top_domains(seed.conn, day_filter())
    assert [(r.domain, r.seconds) for r in rows] == [("example.org", 3600)]


def test_daily_totals_per_device(seed):
    seed.add("desk-a", "window", local(9), local(10), app="editor")
    seed.add("desk-a", "window", local(23), local(1, day=21), app="editor")
    seed.add("desk-b", "window", local(9), local(9, 30), app="browser")
    rows = queries.daily(seed.conn, day_filter(t_to=local(0, day=22)))
    got = {(r.day.isoformat(), r.device): r.seconds for r in rows}
    assert got == {
        ("2026-11-20", "desk-a"): 3600 + 3600,
        ("2026-11-21", "desk-a"): 3600,
        ("2026-11-20", "desk-b"): 1800,
    }


def test_events_pagination_cursor(seed):
    for i in range(5):
        seed.add("desk-a", "window", local(9 + i), local(9 + i, 30), app="editor", title=f"t{i}")
    seed.add("desk-a", "afk", local(9), local(10), idle=True)  # not listed
    f = day_filter(devices=["desk-a"])
    pages, cursor = [], None
    while True:
        page = queries.events(seed.conn, f, cursor, limit=2)
        pages.append([r.title for r in page.rows])
        cursor = page.next_cursor
        if cursor is None:
            break
    assert pages == [["t4", "t3"], ["t2", "t1"], ["t0"]]


def test_events_filters_and_tie_break(seed):
    for _ in range(3):
        seed.add("desk-a", "window", local(9), local(10), app="Editor")
    seed.add("desk-b", "window", local(9), local(10), app="editor")
    seed.add("desk-a", "window", local(15), local(16), app="browser")
    f = day_filter(devices=["desk-a"], app="editor", hour_from=9, hour_to=10)
    first = queries.events(seed.conn, f, None, limit=2)
    second = queries.events(seed.conn, f, first.next_cursor, limit=2)
    ids = [r.id for r in first.rows + second.rows]
    assert len(set(ids)) == 3 and ids == sorted(ids, reverse=True)
    assert second.next_cursor is None


def test_devices_lists_seeded(seed):
    seed.add("desk-b", "window", local(9), local(10), app="editor")
    seed.add("desk-a", "window", local(9), local(10), app="editor")
    seed.add("desk-a", "afk", local(10), local(12))
    rows = queries.devices(seed.conn)
    assert [(r.device, r.events) for r in rows] == [("desk-a", 2), ("desk-b", 1)]
    assert rows[0].first_seen == local(9) and rows[0].last_seen == local(12)


def test_events_limit_is_clamped(seed):
    for i in range(queries.MAX_EVENTS_LIMIT + 3):
        seed.add("desk-a", "window", local(9), local(9, 1), app="editor", title=str(i))
    f = day_filter()
    assert len(queries.events(seed.conn, f, None, limit=0).rows) == 1
    assert len(queries.events(seed.conn, f, None, limit=-5).rows) == 1
    page = queries.events(seed.conn, f, None, limit=9999)
    assert len(page.rows) == queries.MAX_EVENTS_LIMIT and page.next_cursor is not None


def test_events_includes_overlapping_and_rejects_bad_cursor(seed):
    seed.add("desk-a", "window", local(9), local(11), app="editor")
    rows = queries.events(seed.conn, day_filter(t_from=local(10)), None).rows
    assert len(rows) == 1
    with pytest.raises(ValueError):
        queries.events(seed.conn, day_filter(), f"{local(9).isoformat()}|{2**63}")


def test_top_limit_is_clamped(seed):
    for i in range(queries.MAX_TOP_LIMIT + 5):
        seed.add("desk-a", "window", local(9), local(9, 1), app=f"app{i:03d}")
    f = day_filter()
    assert len(queries.top_apps(seed.conn, f, limit=0)) == 1
    assert len(queries.top_apps(seed.conn, f, limit=9999)) == queries.MAX_TOP_LIMIT


def _old_event_slices_sql() -> str:
    """The 0002 definition of usage.event_slices, renamed so both can run side by side."""
    text = (REAL_MIGRATIONS / "0002_usage_functions.sql").read_text(encoding="utf-8")
    start = text.index("CREATE OR REPLACE FUNCTION usage.event_slices(")
    end = text.index("$$;", text.index("LANGUAGE sql", start)) + 3
    return text[start:end].replace("usage.event_slices(", "usage.event_slices_0002(", 1)


def test_idle_cte_matches_0002_semantics(seed):
    seed.conn.execute(_old_event_slices_sql())
    for dev in ("desk-a", "desk-b"):
        seed.add(dev, "window", local(8, 10), local(13, 40), app="editor")
        seed.add(dev, "window", local(12), local(15), app="Browser")
        seed.add(dev, "window", local(9), local(9, 30), app="LockApp.exe", idle=True)
    seed.add("desk-a", "web", local(10), local(11, 30), domain="x.example")
    # overlapping idle intervals (sum over-counts, clamped at 0), plus ones partly/fully
    # outside the query window and an active (non-idle) afk event
    seed.add("desk-a", "afk", local(9), local(10, 30), idle=True)
    seed.add("desk-a", "afk", local(10), local(11), idle=True)
    seed.add("desk-a", "afk", local(10, 10), local(10, 20), idle=True)
    seed.add("desk-a", "afk", local(11, 45), local(12, 15), idle=True)
    seed.add("desk-a", "afk", local(14), local(16), idle=True)
    seed.add("desk-a", "afk", local(5), local(6), idle=True)
    seed.add("desk-a", "afk", local(13), local(14), idle=False)
    seed.add("desk-b", "afk", local(12, 30), local(14), idle=True)

    windows = [
        (local(0), local(0, day=21)),
        (local(10, 15), local(13, 5)),
        (local(14, 30), local(15, 30)),
    ]
    for kind in ("window", "web"):
        for devices in (None, [], ["desk-a"], ["desk-b"], ["desk-a", "desk-b"]):
            for app in (None, "editor"):
                for t_from, t_to in windows:
                    args = (kind, devices, t_from, t_to, TZ, app)
                    query = (
                        "SELECT bucket, device, app, domain, seconds "
                        "FROM usage.{}(%s,%s,%s,%s,%s,%s)"
                    )
                    new = seed.conn.execute(query.format("event_slices"), args).fetchall()
                    old = seed.conn.execute(query.format("event_slices_0002"), args).fetchall()
                    assert sorted(new, key=repr) == sorted(old, key=repr), args
    assert (
        seed.conn.execute(
            "SELECT count(*) FROM usage.event_slices('window', NULL, %s, %s, %s)",
            (local(0), local(0, day=21), TZ),
        ).fetchone()[0]
        > 0
    )


def test_migration_0003_recorded_and_idle_index_exists(seed, test_dsn):
    versions = [
        r[0] for r in seed.conn.execute("SELECT version FROM usage.schema_migrations ORDER BY 1")
    ]
    assert versions == ["0001", "0002", "0003"]
    row = seed.conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'events_afk_idle'"
    ).fetchone()
    assert row and "WHERE" in row[0]
    assert apply(test_dsn, REAL_MIGRATIONS) == []  # idempotent re-apply
