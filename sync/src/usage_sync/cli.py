"""Command line entry point: usage-sync init|status|once|run."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sqlite3
import sys
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from usage_sync.aw import AwClient, AwError
from usage_sync.config import Config, ConfigError, default_config_dir
from usage_sync.importer import (
    compare_activity_logger,
    import_activity_logger,
    print_compare_report,
    print_import_report,
)
from usage_sync.shipper import Shipper, urllib_post
from usage_sync.state import State

log = logging.getLogger("usage_sync")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usage-sync", description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_dir() / "config.toml",
        help="path to config.toml (default: %(default)s)",
    )
    parser.add_argument("--log-file", type=Path, help="log to this rotating file")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a config file (no token)")
    init.add_argument("--device", required=True)
    init.add_argument("--ingest-url", required=True)
    init.add_argument("--aw-url")
    init.add_argument("--ca-file")
    init.add_argument("--force", action="store_true", help="overwrite an existing config")

    sub.add_parser("status", help="show config, ActivityWatch reachability and cursors")
    sub.add_parser("once", help="run one sync cycle")
    sub.add_parser("run", help="sync continuously until SIGINT/SIGTERM")

    imp = sub.add_parser("import-activity-logger", help="ship old activity-logger history")
    imp.add_argument("sqlite_path", type=Path)
    imp.add_argument("--device", required=True)
    imp.add_argument("--batch-size", type=int, default=1000)
    imp.add_argument("--dry-run", action="store_true", help="count rows, post nothing")

    cmp_ = sub.add_parser("compare-activity-logger", help="compare old history with ActivityWatch")
    cmp_.add_argument("sqlite_path", type=Path)
    cmp_.add_argument("--date", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    cmp_.add_argument("--tz", default=os.environ.get("USAGE_TZ") or "America/Chicago")
    return parser


def _quote(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def _cmd_init(args: argparse.Namespace) -> int:
    path: Path = args.config
    if path.exists() and not args.force:
        print(f"error: {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [f"device = {_quote(args.device)}", f"ingest_url = {_quote(args.ingest_url)}"]
    if args.aw_url:
        lines.append(f"aw_url = {_quote(args.aw_url)}")
    if args.ca_file:
        lines.append(f"ca_file = {_quote(args.ca_file)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    print(f"next: put the device token in {path.parent / 'token'} (mode 600)")
    return 0


def _log_path(args: argparse.Namespace) -> Path:
    return args.log_file or args.config.parent / "usage-sync.log"


def _setup_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if args.log_file or sys.stderr is None:  # pythonw has no stderr
        path = _log_path(args)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(fmt))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(level)
    else:
        logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


def _cmd_status(cfg: Config, path: Path, log_path: Path | None = None) -> int:
    print(f"config:     {path}")
    print(f"log file:   {log_path or path.parent / 'usage-sync.log'}")
    print(f"device:     {cfg.device}")
    print(f"ingest url: {cfg.ingest_url}")
    present = cfg.token_file.is_file()
    print(f"token file: {'present' if present else 'missing'} ({cfg.token_file})")
    try:
        AwClient(cfg.aw_url, timeout=5).ping()
        print(f"activitywatch: reachable ({cfg.aw_url})")
    except AwError as exc:
        print(f"activitywatch: unreachable ({exc})")
    state = State(path.parent / "state.json")
    cursors = state.cursors()
    if not cursors:
        print("cursors: none yet")
    else:
        print("cursors:")
        for bucket, ts in sorted(cursors.items()):
            print(f"  {bucket}: {ts.astimezone(UTC).isoformat()}")
    return 0


def _make_shipper(cfg: Config, path: Path) -> Shipper:
    return Shipper(
        cfg,
        AwClient(cfg.aw_url),
        State(path.parent / "state.json"),
        urllib_post(cfg.ca_file),
        lambda: datetime.now(UTC),
    )


def _cmd_once(cfg: Config, path: Path) -> int:
    report = _make_shipper(cfg, path).sync_once()
    print(
        f"buckets={report.buckets} shipped={report.shipped} "
        f"skipped_unsettled={report.skipped_unsettled} skipped_bad={report.skipped_bad} "
        f"errors={len(report.errors)}"
    )
    for err in report.errors:
        print(f"error: {err}", file=sys.stderr)
    return 1 if report.errors else 0


def _cmd_run(cfg: Config, path: Path) -> int:
    stop = threading.Event()

    def handler(signum, _frame) -> None:
        log.info("received signal %s, stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    log.info("starting sync loop for device %s", cfg.device)
    _make_shipper(cfg, path).run(stop)
    return 0


def _cmd_import(cfg: Config, args: argparse.Namespace) -> int:
    try:
        report = import_activity_logger(
            args.sqlite_path,
            args.device,
            cfg,
            urllib_post(cfg.ca_file),
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    except (sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_import_report(report, args.dry_run)
    for err in report.errors:
        print(f"error: {err}", file=sys.stderr)
    return 1 if report.errors else 0


def _cmd_compare(cfg: Config, args: argparse.Namespace) -> int:
    try:
        report = compare_activity_logger(args.sqlite_path, args.date, AwClient(cfg.aw_url), args.tz)
    except (ValueError, AwError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_compare_report(report, args.date)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging(args)
    if args.command == "init":
        return _cmd_init(args)
    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.command == "import-activity-logger":
        return _cmd_import(cfg, args)
    if args.command == "compare-activity-logger":
        return _cmd_compare(cfg, args)
    if args.command == "status":
        return _cmd_status(cfg, args.config, _log_path(args))
    handlers = {"once": _cmd_once, "run": _cmd_run}
    return handlers[args.command](cfg, args.config)


if __name__ == "__main__":
    sys.exit(main())
