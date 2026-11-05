import json
import random
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from usage_sync import cli
from usage_sync.config import Config
from usage_sync.importer import compare_activity_logger, import_activity_logger, rows_to_events

SCHEMA = """CREATE TABLE sessions (
    id INTEGER PRIMARY KEY, app_name TEXT NOT NULL, display_name TEXT, window_title TEXT,
    process_name TEXT, exe_path TEXT, category TEXT, start_time REAL NOT NULL, end_time REAL,
    duration_seconds REAL, is_idle INTEGER NOT NULL DEFAULT 0, device TEXT NOT NULL DEFAULT 'pc')"""
T0 = datetime(2026, 3, 30, 12, 0, tzinfo=UTC).timestamp()


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO sessions (id, app_name, window_title, process_name, start_time, end_time,"
        " duration_seconds, is_idle) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def row(i, start, end, dur=None, app="Fake App", proc="fakeapp.exe", title="t", idle=0):
    return (i, app, title, proc, start, end, dur, idle)


def as_rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM sessions ORDER BY id").fetchall()
    con.close()
    return rows


def test_rows_to_events_window_and_idle(tmp_path):
    db = tmp_path / "a.db"
    make_db(
        db,
        [
            row(1, T0, T0 + 60),
            row(2, T0 + 60, T0 + 90, idle=1, proc=None),
            row(3, T0 + 90, T0 + 100, proc=None, app="Fallback"),
            row(4, T0 + 200, T0 + 100),  # negative duration
        ],
    )
    evs = list(rows_to_events(as_rows(db), "pc"))
    assert [e["source_event_id"] for e in evs] == [1, 2, 3]
    win, afk, fb = evs
    assert win["kind"] == "window" and win["app"] == "fakeapp.exe" and win["title"] == "t"
    assert win["idle"] is False and win["source_bucket"] == "activity-logger"
    assert win["ts_start"] == datetime.fromtimestamp(T0, UTC)
    assert win["ts_end"] - win["ts_start"] == timedelta(seconds=60)
    assert win["url"] is None and win["domain"] is None
    assert afk["kind"] == "afk" and afk["idle"] is True
    assert afk["app"] is None and afk["title"] is None
    assert fb["app"] == "Fallback"


def test_rows_skip_open_sessions(tmp_path):
    db = tmp_path / "a.db"
    make_db(db, [row(1, T0, None, None), row(2, T0, None, 42.5)])
    evs = list(rows_to_events(as_rows(db), "pc"))
    assert [e["source_event_id"] for e in evs] == [2]
    assert (evs[0]["ts_end"] - evs[0]["ts_start"]).total_seconds() == 42.5


class FakePost:
    def __init__(self, statuses=None):
        self.statuses, self.calls = list(statuses or []), []

    def __call__(self, url, body, headers):
        self.calls.append((url, json.loads(body), headers))
        status = self.statuses.pop(0) if self.statuses else 200
        return status, b"{}"


def make_cfg(tmp_path):
    (tmp_path / "token").write_text("sekrit\n", encoding="utf-8")
    return Config(ingest_url="https://ingest.example", device="pc", token_file=tmp_path / "token")


def big_db(tmp_path, n=2500):
    ids = list(range(1, n + 1))
    random.Random(1).shuffle(ids)
    db = tmp_path / "big.db"
    make_db(db, [row(i, T0 + i * 10, T0 + i * 10 + 5) for i in ids])
    return db


def test_import_batches_sorted(tmp_path):
    db, cfg, post = big_db(tmp_path), make_cfg(tmp_path), FakePost()
    rep = import_activity_logger(db, "pc", cfg, post)
    assert rep.read == 2500 and rep.shipped == 2500 and rep.batches == 3 and rep.errors == []
    assert [len(c[1]["events"]) for c in post.calls] == [1000, 1000, 500]
    url, body, headers = post.calls[0]
    assert url == "https://ingest.example/v1/ingest"
    assert headers["Authorization"] == "Bearer sekrit"
    assert body["source"] == "usage" and body["device"] == "pc"
    sent = [e for c in post.calls for e in c[1]["events"]]
    starts = [e["ts_start"] for e in sent]
    assert starts == sorted(starts)
    assert len({e["source_event_id"] for e in sent}) == 2500
    assert not (tmp_path / "state.json").exists()


def test_import_stops_on_first_failure(tmp_path):
    db, cfg, post = big_db(tmp_path), make_cfg(tmp_path), FakePost([200, 503, 200])
    rep = import_activity_logger(db, "pc", cfg, post)
    assert len(post.calls) == 2 and rep.shipped == 1000 and rep.batches == 1
    assert len(rep.errors) == 1 and "503" in rep.errors[0]


def test_import_redacts(tmp_path):
    db, cfg = tmp_path / "r.db", make_cfg(tmp_path)
    make_db(db, [row(1, T0, T0 + 5, proc="KeePassXC.exe", title="secret"), row(2, T0, T0 + 5)])
    post = FakePost()
    import_activity_logger(db, "pc", cfg, post)
    assert [e["title"] for e in post.calls[0][1]["events"]] == [None, "t"]


def test_import_dry_run_posts_nothing(tmp_path):
    db, cfg = tmp_path / "r.db", make_cfg(tmp_path)
    make_db(db, [row(1, T0, T0 + 5), row(2, T0, T0 + 5)])
    never = FakePost()
    rep = import_activity_logger(db, "pc", cfg, never, dry_run=True)
    assert never.calls == [] and rep.read == 2 and rep.shipped == 0 and rep.batches == 0


class FakeAw:
    def __init__(self, events):
        self._events = events

    def buckets(self):
        return {
            "aw-watcher-window_x": {"type": "currentwindow"},
            "aw-watcher-afk_x": {"type": "afkstatus"},
        }

    def events(self, bucket_id, start, end, limit=5000):
        assert bucket_id == "aw-watcher-window_x"
        return self._events


def aw_ev(i, start, dur, app):
    return {
        "id": i,
        "timestamp": start.isoformat(),
        "duration": dur,
        "data": {"app": app, "title": "x"},
    }


def test_compare_ratio_and_exe_normalisation(tmp_path):
    day = date(2026, 4, 1)
    base = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    b = base.timestamp()
    db = tmp_path / "c.db"
    make_db(
        db,
        [
            row(1, b, b + 1000, proc="Alpha.EXE"),
            row(2, b + 2000, b + 2500, proc="beta.exe"),
            row(3, b, b + 900, proc="alpha.exe", idle=1),
            row(4, b + 5000, b + 5100, proc="outside"),
        ],
    )
    aw = FakeAw(
        [
            aw_ev(1, base, 1050, "alpha.exe"),
            aw_ev(2, base + timedelta(seconds=2000), 500, "BETA"),
        ]
    )
    rep = compare_activity_logger(db, day, aw, "UTC")
    table = {app: (old, new) for app, old, new in rep.rows}
    assert table["alpha"] == (1000, 1050)
    assert table["beta"] == (500, 500)
    assert table["outside"] == (100, 0)
    assert rep.rows[0][0] == "alpha"
    assert rep.ratio == pytest.approx(1550 / 1600)


def test_compare_zero_old_total(tmp_path):
    db = tmp_path / "e.db"
    make_db(db, [])
    rep = compare_activity_logger(db, date(2026, 4, 1), FakeAw([]), "UTC")
    assert rep.rows == [] and rep.ratio == 0


def test_cli_import_and_compare_wiring(tmp_path, capsys, monkeypatch):
    cfg_path = tmp_path / "cfg" / "config.toml"
    cfg_path.parent.mkdir()
    cfg_path.write_text('ingest_url = "https://ingest.example"\ndevice = "pc"\n', encoding="utf-8")
    (cfg_path.parent / "token").write_text("sekrit\n", encoding="utf-8")
    db = tmp_path / "w.db"
    base = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    make_db(db, [row(1, base.timestamp(), base.timestamp() + 600, proc="alpha.exe")])
    post = FakePost()
    monkeypatch.setattr(cli, "urllib_post", lambda ca: post)
    common = ["--config", str(cfg_path)]

    code = cli.main([*common, "import-activity-logger", str(db), "--device", "gpu", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "dry run" in out and "read=1" in out and post.calls == []

    code = cli.main([*common, "import-activity-logger", str(db), "--device", "gpu"])
    out = capsys.readouterr().out
    assert code == 0 and "shipped=1" in out
    assert post.calls[0][1]["device"] == "gpu"
    assert post.calls[0][1]["events"][0]["device"] == "gpu"

    monkeypatch.setattr(
        cli, "AwClient", lambda url, timeout=10: FakeAw([aw_ev(1, base, 600, "alpha")])
    )
    argv = [*common, "compare-activity-logger", str(db), "--date", "2026-04-01", "--tz", "UTC"]
    code = cli.main(argv)
    out = capsys.readouterr().out
    assert code == 0 and "alpha" in out and "PASS" in out

    monkeypatch.setattr(cli, "AwClient", lambda url, timeout=10: FakeAw([]))
    code = cli.main(argv)
    assert code == 0 and "WARN" in capsys.readouterr().out


def test_bad_start_time_is_skipped_not_fatal(tmp_path):
    db, cfg, post = tmp_path / "b.db", make_cfg(tmp_path), FakePost()
    make_db(db, [row(1, 1e30, 1e30 + 5), row(2, T0, T0 + 5)])
    rep = import_activity_logger(db, "pc", cfg, post)
    assert rep.read == 2 and rep.shipped == 1 and rep.skipped == 1


def test_compare_pass_ignores_zero_old_apps():
    from usage_sync.importer import CompareReport

    ok = CompareReport(rows=[("a", 100, 105), ("only_new", 0, 50)], ratio=1)
    assert ok.passed


def test_cli_import_errors_exit_1(tmp_path, capsys):
    cfg_path = tmp_path / "cfg" / "config.toml"
    cfg_path.parent.mkdir()
    cfg_path.write_text('ingest_url = "https://ingest.example"\ndevice = "pc"\n', encoding="utf-8")
    db = tmp_path / "w.db"
    make_db(db, [row(1, T0, T0 + 5)])
    common = ["--config", str(cfg_path)]
    assert cli.main([*common, "import-activity-logger", str(db), "--device", "g"]) == 1
    assert "error:" in capsys.readouterr().err  # missing token file
    missing = str(tmp_path / "nope.db")
    assert cli.main([*common, "import-activity-logger", missing, "--device", "g", "--dry-run"]) == 1
    assert "error:" in capsys.readouterr().err
    argv = [*common, "compare-activity-logger", missing, "--date", "2026-04-01", "--tz", "UTC"]
    assert cli.main(argv) == 1
    assert "error:" in capsys.readouterr().err
