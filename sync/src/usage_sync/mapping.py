"""Map ActivityWatch buckets/events to the Lab Data Store event shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

Kind = Literal["window", "afk", "web"]

_TYPE_TO_KIND: dict[str, Kind] = {
    "currentwindow": "window",
    "afkstatus": "afk",
    "web.tab.current": "web",
}


def bucket_kind(bucket: dict) -> Kind | None:
    return _TYPE_TO_KIND.get(bucket.get("type", ""))


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _clean(value):
    """Strip NUL bytes: Postgres text cannot store them and the ingest API rejects them."""
    return value.replace("\x00", "") if isinstance(value, str) else value


# Lock-screen / logon processes: the focused "app" while the machine is locked, not usage.
IDLE_APPS = (
    "lockapp.exe",
    "logonui.exe",
    "loginwindow",
    "gnome-screensaver",
    "gdm-x-session",
    "kscreenlocker_greet",
)


def _norm_app(app: str) -> str:
    low = app.strip().lower()
    return low[:-4] if low.endswith(".exe") else low


def is_idle_app(app: str | None, idle_apps) -> bool:
    """Case-insensitive exact match on the app name, with or without a trailing .exe."""
    if not app:
        return False
    wanted = {_norm_app(a) for a in idle_apps}
    return _norm_app(app) in wanted


def to_event(bucket_id: str, kind: str, aw_event: dict, idle_apps=IDLE_APPS) -> dict | None:
    data = aw_event.get("data") or {}
    ts_start = _parse_ts(aw_event["timestamp"])
    ts_end = ts_start + timedelta(seconds=float(aw_event.get("duration") or 0))

    app = title = url = domain = None
    idle = False
    if kind == "window":
        app = data.get("package") or data.get("app") or None
        title = data.get("title")
        if is_idle_app(app, idle_apps):
            idle, title = True, None
    elif kind == "afk":
        idle = data.get("status") == "afk"
    elif kind == "web":
        if data.get("incognito"):
            return None
        url = data.get("url")
        title = data.get("title")
        domain = urlsplit(url).hostname if url else None
        app = data.get("app") or None
    else:
        return None

    app, title, url, domain = (_clean(v) for v in (app, title, url, domain))
    return {
        "ts_start": ts_start,
        "ts_end": ts_end,
        "kind": kind,
        "app": app,
        "title": title,
        "url": url,
        "domain": domain,
        "idle": idle,
        "source_bucket": bucket_id,
        "source_event_id": aw_event.get("id"),
    }
