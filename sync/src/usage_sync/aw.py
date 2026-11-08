"""Minimal stdlib client for the local ActivityWatch server (aw-server REST API)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime


class AwError(Exception):
    """Raised when the ActivityWatch server cannot be reached or returns an error."""


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AwClient:
    def __init__(self, base_url: str, timeout: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AwError(f"HTTP {exc.code} {exc.reason} for {path}") from exc
        except urllib.error.URLError as exc:
            raise AwError(f"cannot reach ActivityWatch at {self.base_url}: {exc.reason}") from exc
        except (TimeoutError, OSError, ValueError) as exc:
            raise AwError(f"request to {path} failed: {exc}") from exc

    def ping(self) -> dict:
        return self._get("/api/0/info")

    def buckets(self) -> dict[str, dict]:
        return self._get("/api/0/buckets")

    def events(
        self, bucket_id: str, start: datetime, end: datetime, limit: int = 5000
    ) -> list[dict]:
        quoted = urllib.parse.quote(bucket_id, safe="")
        return self._get(
            f"/api/0/buckets/{quoted}/events",
            {"start": _iso_utc(start), "end": _iso_utc(end), "limit": limit},
        )

    def event(self, bucket_id: str, event_id: int) -> dict:
        """One event by id; unlike a windowed query, it is not trimmed to any window."""
        quoted = urllib.parse.quote(bucket_id, safe="")
        return self._get(f"/api/0/buckets/{quoted}/events/{int(event_id)}")
