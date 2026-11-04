"""JSON API consumed by the frontend. Shapes follow the API contract."""

import logging
import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import queries
from .auth import require_session

log = logging.getLogger("usage_web.api")

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(422, detail=message)


def _parse_instant(value: str, name: str, tz: ZoneInfo) -> datetime:
    """Accept YYYY-MM-DD (local midnight in tz) or an ISO datetime carrying an offset."""
    try:
        if DATE_RE.match(value):
            return datetime.combine(date.fromisoformat(value), time(), tzinfo=tz)
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _unprocessable(f"{name} must be YYYY-MM-DD or an ISO datetime with offset") from None
    if parsed.tzinfo is None:
        raise _unprocessable(f"{name} datetime must include a UTC offset")
    return parsed


def _parse_hour(value: str | None, name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        hour = int(value)
    except ValueError:
        raise _unprocessable(f"{name} must be an integer 0-23") from None
    if not 0 <= hour <= 23:
        raise _unprocessable(f"{name} must be between 0 and 23")
    return hour


def _parse_limit(value: str | None, name: str, default: int, ceiling: int) -> int:
    if value is None or value == "":
        return default
    try:
        limit = int(value)
    except ValueError:
        raise _unprocessable(f"{name} must be an integer") from None
    if not 1 <= limit <= ceiling:
        raise _unprocessable(f"{name} must be between 1 and {ceiling}")
    return limit


def parse_filters(
    *,
    devices: list[str] | None,
    t_from: str | None,
    t_to: str | None,
    hour_from: str | None,
    hour_to: str | None,
    app: str | None,
    domain: str | None,
    tz: str,
) -> queries.Filters:
    zone = ZoneInfo(tz)
    if t_from:
        start = _parse_instant(t_from, "from", zone)
    else:
        today = datetime.now(zone).date()
        start = datetime.combine(today, time(), tzinfo=zone)
    if t_to:
        end = _parse_instant(t_to, "to", zone)
    else:
        end = datetime.combine(
            start.astimezone(zone).date() + timedelta(days=1), time(), tzinfo=zone
        )
        if t_from and not DATE_RE.match(t_from):
            end = start + timedelta(days=1)
    if start >= end:
        raise _unprocessable("from must be earlier than to")
    return queries.Filters(
        devices=[d for d in (devices or []) if d],
        t_from=start,
        t_to=end,
        hour_from=_parse_hour(hour_from, "hour_from", 0),
        hour_to=_parse_hour(hour_to, "hour_to", 23),
        app=app or None,
        domain=domain or None,
        tz=tz,
    )


def _filters(
    request: Request,
    device: Annotated[list[str] | None, Query()] = None,
    t_from: Annotated[str | None, Query(alias="from")] = None,
    t_to: Annotated[str | None, Query(alias="to")] = None,
    hour_from: str | None = None,
    hour_to: str | None = None,
    app: str | None = None,
    domain: str | None = None,
) -> queries.Filters:
    return parse_filters(
        devices=device,
        t_from=t_from,
        t_to=t_to,
        hour_from=hour_from,
        hour_to=hour_to,
        app=app,
        domain=domain,
        tz=request.app.state.settings.USAGE_TZ,
    )


Flt = Annotated[queries.Filters, Depends(_filters)]


def _run[T](request: Request, fn: Callable[[psycopg.Connection], T]) -> T:
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(503, detail="database unavailable")
    try:
        with pool.connection() as conn:
            return fn(conn)
    except psycopg.Error:
        log.exception("database error serving %s", request.url.path)
        raise HTTPException(503, detail="database unavailable") from None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/meta")
def meta(request: Request) -> dict:
    rows = _run(request, queries.devices)
    return {
        "tz": request.app.state.settings.USAGE_TZ,
        "devices": [
            {
                "device": r.device,
                "first_seen": _iso(r.first_seen),
                "last_seen": _iso(r.last_seen),
                "events": r.events,
            }
            for r in rows
        ],
    }


@router.get("/hourly")
def hourly(request: Request, f: Flt) -> dict:
    zone = ZoneInfo(f.tz)
    rows = _run(request, lambda conn: queries.hourly(conn, f))
    kept = [r for r in rows if f.hour_from <= r.bucket.astimezone(zone).hour <= f.hour_to]
    return {
        "from": f.t_from.isoformat(),
        "to": f.t_to.isoformat(),
        "tz": f.tz,
        "rows": [
            {
                "bucket": r.bucket.isoformat(),
                "device": r.device,
                "app": r.app,
                "seconds": float(r.seconds),
            }
            for r in kept
        ],
    }


@router.get("/top/apps")
def top_apps(request: Request, f: Flt, limit: str | None = None) -> dict:
    n = _parse_limit(limit, "limit", 20, queries.MAX_TOP_LIMIT)
    rows = _run(request, lambda conn: queries.top_apps(conn, f, n))
    return {"rows": [{"app": r.app, "seconds": float(r.seconds)} for r in rows]}


@router.get("/top/domains")
def top_domains(request: Request, f: Flt, limit: str | None = None) -> dict:
    n = _parse_limit(limit, "limit", 20, queries.MAX_TOP_LIMIT)
    rows = _run(request, lambda conn: queries.top_domains(conn, f, n))
    return {"rows": [{"domain": r.domain, "seconds": float(r.seconds)} for r in rows]}


@router.get("/daily")
def daily(request: Request, f: Flt) -> dict:
    rows = _run(request, lambda conn: queries.daily(conn, f))
    return {
        "rows": [
            {"day": r.day.isoformat(), "device": r.device, "seconds": float(r.seconds)}
            for r in rows
        ]
    }


@router.get("/events")
def events(
    request: Request,
    f: Flt,
    cursor: str | None = None,
    limit: str | None = None,
) -> dict:
    n = _parse_limit(limit, "limit", 200, queries.MAX_EVENTS_LIMIT)
    try:
        page = _run(request, lambda conn: queries.events(conn, f, cursor or None, n))
    except ValueError:
        raise _unprocessable("invalid cursor") from None
    return {
        "rows": [
            {
                "id": r.id,
                "device": r.device,
                "ts_start": _iso(r.ts_start),
                "ts_end": _iso(r.ts_end),
                "kind": r.kind,
                "app": r.app,
                "title": r.title,
                "url": r.url,
                "domain": r.domain,
            }
            for r in page.rows
        ],
        "next_cursor": page.next_cursor,
    }
