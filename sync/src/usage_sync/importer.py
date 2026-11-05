"""Import the legacy activity-logger SQLite history and compare it with ActivityWatch."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from usage_sync.aw import AwClient
from usage_sync.config import Config
from usage_sync.mapping import bucket_kind, to_event
from usage_sync.redact import Redactor
from usage_sync.shipper import Post, post_batch

SOURCE_BUCKET = "activity-logger"
TOP_N = 5
TOLERANCE = 0.15
FETCH_LIMIT = 100000


@dataclass
class ImportReport:
    read: int = 0
    shipped: int = 0
    skipped: int = 0
    batches: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CompareReport:
    rows: list[tuple[str, float, float]]
    ratio: float

    @property
    def passed(self) -> bool:
        top = [r for r in self.rows if r[1] > 0][:TOP_N]
        return bool(top) and all(abs(new - old) / old <= TOLERANCE for _, old, new in top)


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _row_event(row: Mapping | sqlite3.Row) -> dict | None:
    try:
        return _build_event(row)
    except (ValueError, OverflowError, OSError, TypeError):
        return None  # NaN / out-of-range timestamps: skipped and counted


def _build_event(row: Mapping | sqlite3.Row) -> dict | None:
    start = row["start_time"]
    end = row["end_time"]
    if end is None:
        if row["duration_seconds"] is None:
            return None
        end = start + row["duration_seconds"]
    if end < start:
        return None
    idle = bool(row["is_idle"])
    return {
        "ts_start": _utc(start),
        "ts_end": _utc(end),
        "kind": "afk" if idle else "window",
        "app": None if idle else (row["process_name"] or row["app_name"]),
        "title": None if idle else row["window_title"],
        "url": None,
        "domain": None,
        "idle": idle,
        "source_bucket": SOURCE_BUCKET,
        "source_event_id": row["id"],
    }


def rows_to_events(rows: Iterable[sqlite3.Row | Mapping], device: str) -> Iterator[dict]:
    """Yield shim-shaped events; open sessions without a duration and bad rows are skipped."""
    for row in rows:
        event = _row_event(row)
        if event is not None:
            yield event


def _open_ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def import_activity_logger(
    db_path: Path,
    device: str,
    cfg: Config,
    post: Post,
    batch_size: int = 1000,
    redactor: Redactor | None = None,
    dry_run: bool = False,
) -> ImportReport:
    redactor = redactor or Redactor(cfg.redact_apps, cfg.redact_domains)
    report = ImportReport()
    token = "" if dry_run else cfg.token_file.read_text(encoding="utf-8").strip()
    batch_cfg = replace(cfg, device=device)
    kept = 0
    batch: list[dict] = []
    con = _open_ro(db_path)
    try:
        for row in con.execute("SELECT * FROM sessions ORDER BY start_time, id"):
            report.read += 1
            event = _row_event(row)
            if event is None:
                continue
            kept += 1
            batch.append(redactor.apply(event))
            if len(batch) >= batch_size:
                if not _flush(batch, batch_cfg, post, token, report, dry_run):
                    return report
                batch = []
        if batch:
            _flush(batch, batch_cfg, post, token, report, dry_run)
    finally:
        con.close()
    report.skipped = report.read - kept
    return report


def _flush(
    batch: list[dict], cfg: Config, post: Post, token: str, report: ImportReport, dry_run: bool
) -> bool:
    if dry_run:
        return True
    status, resp = post_batch(cfg, post, token, batch)
    if status != 200:
        text = resp.decode("utf-8", errors="replace")
        report.errors.append(f"HTTP {status} {text[:200]}")
        return False
    report.shipped += len(batch)
    report.batches += 1
    return True


def _norm_app(app: str | None) -> str:
    name = (app or "(unknown)").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def _zone(tz: str) -> tzinfo:
    if tz.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown time zone {tz!r} (on Windows: pip install tzdata)") from exc


def _clip(ev: dict, lo: datetime, hi: datetime) -> float:
    return max(0.0, (min(ev["ts_end"], hi) - max(ev["ts_start"], lo)).total_seconds())


def compare_activity_logger(db_path: Path, day: date, aw: AwClient, tz: str) -> CompareReport:
    zone = _zone(tz)
    lo = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)
    nxt = day + timedelta(days=1)
    hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone).astimezone(UTC)
    old: dict[str, float] = defaultdict(float)
    new: dict[str, float] = defaultdict(float)
    con = _open_ro(db_path)
    try:
        query = (
            "SELECT * FROM sessions WHERE is_idle = 0 AND start_time < ? AND "
            "COALESCE(end_time, start_time + COALESCE(duration_seconds, 0)) > ?"
        )
        for row in con.execute(query, (hi.timestamp(), lo.timestamp())):
            event = _row_event(row)
            if event is not None:
                old[_norm_app(event["app"])] += _clip(event, lo, hi)
    finally:
        con.close()
    for bucket_id, bucket in aw.buckets().items():
        if bucket_kind(bucket) != "window":
            continue
        for raw in aw.events(bucket_id, lo, hi, FETCH_LIMIT):
            event = to_event(bucket_id, "window", raw)
            if event is not None:
                new[_norm_app(event["app"])] += _clip(event, lo, hi)
    apps = sorted(set(old) | set(new), key=lambda a: (-old.get(a, 0.0), -new.get(a, 0.0), a))
    rows = [(a, old.get(a, 0.0), new.get(a, 0.0)) for a in apps]
    old_total = sum(old.values())
    ratio = sum(new.values()) / old_total if old_total else 0
    return CompareReport(rows=rows, ratio=ratio)


def print_import_report(report: ImportReport, dry_run: bool) -> None:
    prefix = "dry run: " if dry_run else ""
    print(
        f"{prefix}read={report.read} shipped={report.shipped} skipped={report.skipped} "
        f"batches={report.batches} errors={len(report.errors)}"
    )


def print_compare_report(report: CompareReport, day: date) -> None:
    print(f"{'app':<32}{'old_s':>12}{'new_s':>12}{'new/old':>10}")
    for app, o, n in report.rows[:25]:
        pct = f"{n / o:.2f}" if o else "-"
        print(f"{app[:31]:<32}{o:>12.0f}{n:>12.0f}{pct:>10}")
    print(f"total ratio (new/old) for {day}: {report.ratio:.3f}")
    verdict = "PASS" if report.passed else "WARN"
    print(f"{verdict}: top-{TOP_N} apps within {int(TOLERANCE * 100)}% of the old history")
