"""Ship ActivityWatch events to the Lab Data Store ingest API."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from usage_sync.aw import AwClient
from usage_sync.config import Config
from usage_sync.mapping import bucket_kind, to_event
from usage_sync.redact import Redactor
from usage_sync.state import State

log = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FETCH_LIMIT = 20000
MAX_BACKOFF = 600

Post = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]


@dataclass
class SyncReport:
    buckets: int = 0
    shipped: int = 0
    skipped_unsettled: int = 0
    skipped_bad: int = 0
    errors: list[str] = field(default_factory=list)


def backoff(n: int, base: int) -> int:
    return min(base * 2**n, MAX_BACKOFF)


def urllib_post(ca_file: str | None) -> Post:
    ctx = ssl.create_default_context(cafile=ca_file)

    def post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    return post


def _wire(event: dict, device: str) -> dict:
    return {
        **event,
        "ts_start": event["ts_start"].isoformat(),
        "ts_end": event["ts_end"].isoformat(),
        "device": device,
    }


class Shipper:
    def __init__(
        self,
        cfg: Config,
        aw: AwClient,
        state: State,
        post: Post,
        now: Callable[[], datetime],
    ) -> None:
        self.cfg = cfg
        self.aw = aw
        self.state = state
        self.post = post
        self.now = now
        self.redactor = Redactor(cfg.redact_apps, cfg.redact_domains)

    def _token(self) -> str:
        return self.cfg.token_file.read_text(encoding="utf-8").strip()

    def _collect(
        self, bucket_id: str, kind: str, raw: list[dict], report: SyncReport
    ) -> list[dict]:
        by_key: dict[tuple, dict] = {}
        for item in raw:
            try:
                ev = to_event(bucket_id, kind, item)
                if ev is None:
                    continue
                if ev["ts_end"] < ev["ts_start"]:
                    raise ValueError("negative duration")
                if not isinstance(ev["source_event_id"], int):
                    raise ValueError("missing event id")
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError) as exc:
                report.skipped_bad += 1
                log.warning("%s: skipping bad event (%r)", bucket_id, exc)
                continue
            by_key[(ev["source_bucket"], ev["source_event_id"])] = ev
        events = [self.redactor.apply(e) for e in by_key.values()]
        return sorted(events, key=lambda e: e["ts_start"])

    def _ship_bucket(self, bucket_id: str, kind: str, token: str, report: SyncReport) -> None:
        now = self.now()
        cursor = self.state.cursor(bucket_id)
        start = cursor - timedelta(seconds=self.cfg.overlap_seconds) if cursor else EPOCH
        raw = self.aw.events(bucket_id, start, now, FETCH_LIMIT)
        if len(raw) >= FETCH_LIMIT:
            log.warning("%s: fetch hit the %d row limit; window truncated", bucket_id, FETCH_LIMIT)
        events = self._collect(bucket_id, kind, raw, report)
        if not events:
            return
        url = f"{self.cfg.ingest_url.rstrip('/')}/v1/ingest"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        size = self.cfg.batch_size
        for i in range(0, len(events), size):
            batch = [_wire(e, self.cfg.device) for e in events[i : i + size]]
            body = json.dumps({"source": "usage", "device": self.cfg.device, "events": batch})
            status, resp = self.post(url, body.encode("utf-8"), headers)
            if status != 200:
                text = resp.decode("utf-8", errors="replace")
                if status == 422:
                    log.warning("%s: ingest rejected a row: %s", bucket_id, text)
                report.errors.append(f"{bucket_id}: HTTP {status} {text[:200]}")
                return
            report.shipped += len(batch)
        cutoff = now - timedelta(seconds=self.cfg.settle_seconds)
        settled = [e["ts_end"] for e in events if e["ts_end"] <= cutoff]
        report.skipped_unsettled += len(events) - len(settled)
        if settled:
            self.state.advance(bucket_id, max(settled))

    def sync_once(self) -> SyncReport:
        report = SyncReport()
        try:
            token = self._token()
            buckets = self.aw.buckets()
        except Exception as exc:
            report.errors.append(f"setup: {exc}")
            return report
        for bucket_id, bucket in buckets.items():
            kind = bucket_kind(bucket)
            if kind is None:
                continue
            report.buckets += 1
            try:
                self._ship_bucket(bucket_id, kind, token, report)
            except Exception as exc:
                report.errors.append(f"{bucket_id}: {exc}")
        for err in report.errors:
            log.warning("sync error: %s", err)
        return report

    def run(self, stop: threading.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                report = self.sync_once()
            except Exception:
                log.exception("sync cycle failed")
                report = SyncReport(errors=["sync cycle crashed"])
            if report.errors:
                failures += 1
                delay = backoff(failures - 1, self.cfg.interval_seconds)
            else:
                failures = 0
                delay = self.cfg.interval_seconds
            if stop.wait(delay):
                break
