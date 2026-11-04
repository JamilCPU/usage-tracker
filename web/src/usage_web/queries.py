"""Read-only query layer over usage.events and the usage.* SQL functions."""

from dataclasses import dataclass
from datetime import date, datetime

import psycopg

MAX_EVENTS_LIMIT = 500
MAX_TOP_LIMIT = 100


def _clamp(limit: int, ceiling: int) -> int:
    return max(1, min(limit, ceiling))


@dataclass(frozen=True)
class Filters:
    devices: list[str]
    t_from: datetime
    t_to: datetime
    hour_from: int = 0
    hour_to: int = 23
    app: str | None = None
    domain: str | None = None
    tz: str = "America/Chicago"

    @property
    def device_arg(self) -> list[str] | None:
        """Empty selection means all devices (passed to SQL as NULL)."""
        return list(self.devices) if self.devices else None


@dataclass(frozen=True)
class DeviceRow:
    device: str
    first_seen: datetime
    last_seen: datetime
    events: int


@dataclass(frozen=True)
class HourlyRow:
    bucket: datetime
    device: str
    app: str | None
    seconds: float


@dataclass(frozen=True)
class AppRow:
    app: str | None
    seconds: float


@dataclass(frozen=True)
class DomainRow:
    domain: str | None
    seconds: float


@dataclass(frozen=True)
class DailyRow:
    day: date
    device: str
    seconds: float


@dataclass(frozen=True)
class EventRow:
    id: int
    device: str
    ts_start: datetime
    ts_end: datetime
    kind: str
    app: str | None
    title: str | None
    url: str | None
    domain: str | None


@dataclass(frozen=True)
class Page:
    rows: list[EventRow]
    next_cursor: str | None


def devices(conn: psycopg.Connection) -> list[DeviceRow]:
    cur = conn.execute(
        "SELECT device, min(ts_start), max(ts_end), count(*) FROM usage.events "
        "GROUP BY device ORDER BY device"
    )
    return [DeviceRow(*r) for r in cur.fetchall()]


def hourly(conn: psycopg.Connection, f: Filters) -> list[HourlyRow]:
    cur = conn.execute(
        "SELECT bucket, device, app, seconds FROM usage.hourly(%s, %s, %s, %s, %s)",
        (f.device_arg, f.t_from, f.t_to, f.tz, f.app),
    )
    return [HourlyRow(*r) for r in cur.fetchall()]


def top_apps(conn: psycopg.Connection, f: Filters, limit: int = 20) -> list[AppRow]:
    cur = conn.execute(
        "SELECT app, seconds FROM usage.top_apps(%s, %s, %s, %s, %s, %s, %s)",
        (
            f.device_arg,
            f.t_from,
            f.t_to,
            f.hour_from,
            f.hour_to,
            f.tz,
            _clamp(limit, MAX_TOP_LIMIT),
        ),
    )
    return [AppRow(*r) for r in cur.fetchall()]


def top_domains(conn: psycopg.Connection, f: Filters, limit: int = 20) -> list[DomainRow]:
    cur = conn.execute(
        "SELECT domain, seconds FROM usage.top_domains(%s, %s, %s, %s, %s, %s, %s)",
        (
            f.device_arg,
            f.t_from,
            f.t_to,
            f.hour_from,
            f.hour_to,
            f.tz,
            _clamp(limit, MAX_TOP_LIMIT),
        ),
    )
    return [DomainRow(*r) for r in cur.fetchall()]


def daily(conn: psycopg.Connection, f: Filters) -> list[DailyRow]:
    cur = conn.execute(
        "SELECT day, device, seconds FROM usage.daily(%s, %s, %s, %s)",
        (f.device_arg, f.t_from, f.t_to, f.tz),
    )
    return [DailyRow(*r) for r in cur.fetchall()]


def _encode_cursor(row: EventRow) -> str:
    return f"{row.ts_start.isoformat()}|{row.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        ts, _, ident = cursor.rpartition("|")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must carry a UTC offset")
        row_id = int(ident)
        if not 0 <= row_id <= 2**63 - 1:
            raise ValueError("cursor id out of range")
        return parsed, row_id
    except ValueError as exc:
        raise ValueError(f"invalid cursor: {cursor!r}") from exc


_EVENTS_SQL = """
SELECT id, device, ts_start, ts_end, kind, app, title, url, domain
FROM usage.events
WHERE kind IN ('window', 'web')
  AND (%(devices)s::text[] IS NULL OR device = ANY(%(devices)s))
  AND ts_end > %(t_from)s AND ts_start < %(t_to)s
  AND extract(hour FROM ts_start AT TIME ZONE %(tz)s) BETWEEN %(hour_from)s AND %(hour_to)s
  AND (%(app)s::text IS NULL OR lower(app) = lower(%(app)s))
  AND (%(domain)s::text IS NULL OR lower(domain) = lower(%(domain)s))
  AND (%(cur_ts)s::timestamptz IS NULL OR (ts_start, id) < (%(cur_ts)s, %(cur_id)s::bigint))
ORDER BY ts_start DESC, id DESC
LIMIT %(lim)s
"""


def events(conn: psycopg.Connection, f: Filters, cursor: str | None, limit: int = 200) -> Page:
    limit = _clamp(limit, MAX_EVENTS_LIMIT)
    cur_ts, cur_id = _decode_cursor(cursor) if cursor else (None, None)
    cur = conn.execute(
        _EVENTS_SQL,
        {
            "devices": f.device_arg,
            "t_from": f.t_from,
            "t_to": f.t_to,
            "tz": f.tz,
            "hour_from": f.hour_from,
            "hour_to": f.hour_to,
            "app": f.app,
            "domain": f.domain,
            "cur_ts": cur_ts,
            "cur_id": cur_id,
            "lim": limit + 1,
        },
    )
    rows = [EventRow(*r) for r in cur.fetchall()]
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page(rows, _encode_cursor(rows[-1]) if has_more else None)
