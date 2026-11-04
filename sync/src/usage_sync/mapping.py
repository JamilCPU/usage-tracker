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


def to_event(bucket_id: str, kind: str, aw_event: dict) -> dict | None:
    data = aw_event.get("data") or {}
    ts_start = _parse_ts(aw_event["timestamp"])
    ts_end = ts_start + timedelta(seconds=float(aw_event.get("duration") or 0))

    app = title = url = domain = None
    idle = False
    if kind == "window":
        app = data.get("package") or data.get("app") or None
        title = data.get("title")
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
