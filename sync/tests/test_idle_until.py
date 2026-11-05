import json
import sqlite3
from datetime import UTC, datetime

import pytest

from usage_sync import cli
from usage_sync.config import Config, ConfigError
from usage_sync.importer import import_activity_logger, rows_to_events
from usage_sync.mapping import IDLE_APPS, is_idle_app, to_event

SCHEMA = """CREATE TABLE sessions (
    id INTEGER PRIMARY KEY, app_name TEXT NOT NULL, display_name TEXT, window_title TEXT,
    process_name TEXT, exe_path TEXT, category TEXT, start_time REAL NOT NULL, end_time REAL,
    duration_seconds REAL, is_idle INTEGER NOT NULL DEFAULT 0, device TEXT NOT NULL DEFAULT 'pc')"""


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


def row(i, start, end, proc="fakeapp.exe", title="t"):
    return (i, "Fake App", title, proc, start, end, None, 0)


class FakePost:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append((url, json.loads(body), headers))
        return 200, b"{}"


def make_cfg(tmp_path):
    (tmp_path / "token").write_text("sekrit", encoding="utf-8")
    return Config(ingest_url="https://i.example", device="pc", token_file=tmp_path / "token")


T0 = datetime(2026, 3, 30, 12, 0, tzinfo=UTC).timestamp()


def aw(app, title="Locked"):
    return {
        "id": 1,
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "duration": 5,
        "data": {"app": app, "title": title},
    }


def test_idle_app_matching_normalised():
    assert "lockapp.exe" in IDLE_APPS
    assert is_idle_app("LockApp.exe", IDLE_APPS) and is_idle_app("LOGONUI", IDLE_APPS)
    assert is_idle_app("lockapp", ["LockApp.exe"])
    assert not is_idle_app("lockapp-pro.exe", IDLE_APPS) and not is_idle_app(None, IDLE_APPS)


def test_live_mapping_marks_lock_screen_idle():
    ev = to_event("b", "window", aw("LockApp.exe"))
    assert ev["kind"] == "window" and ev["idle"] is True
    assert ev["app"] == "LockApp.exe" and ev["title"] is None
    other = to_event("b", "window", aw("code.exe", "file.py"))
    assert other["idle"] is False and other["title"] == "file.py"
    custom = to_event("b", "window", aw("myapp.exe"), idle_apps=["MyApp"])
    assert custom["idle"] is True


def test_importer_marks_lock_screen_idle(tmp_path):
    db = tmp_path / "i.db"
    make_db(db, [row(1, T0, T0 + 5, proc="LockApp.exe", title="x"), row(2, T0, T0 + 5)])
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM sessions ORDER BY id").fetchall()
    con.close()
    a, b = rows_to_events(rows, "pc")
    assert a["idle"] is True and a["kind"] == "window" and a["title"] is None
    assert b["idle"] is False and b["title"] == "t"
    c, _ = rows_to_events(rows, "pc", idle_apps=["fakeapp"])
    assert c["idle"] is False
    _, d = rows_to_events(rows, "pc", idle_apps=["fakeapp"])
    assert d["idle"] is True


def test_config_idle_apps(tmp_path):
    cfg = tmp_path / "config.toml"
    base = 'ingest_url = "https://i.example"\ndevice = "pc"\n'
    cfg.write_text(base, encoding="utf-8")
    assert Config.load(cfg).idle_apps == list(IDLE_APPS)
    cfg.write_text(base + 'idle_apps = ["Foo.exe"]\n', encoding="utf-8")
    extended = Config.load(cfg).idle_apps
    assert extended[: len(IDLE_APPS)] == list(IDLE_APPS) and extended[-1] == "Foo.exe"
    cfg.write_text(base + 'idle_apps = ["LockApp", "Foo.exe"]\n', encoding="utf-8")
    assert Config.load(cfg).idle_apps.count("lockapp.exe") == 1  # no duplicate of a default
    assert "LockApp" not in Config.load(cfg).idle_apps
    cfg.write_text(base + 'idle_apps = ["Foo.exe"]\nidle_apps_replace = true\n', encoding="utf-8")
    assert Config.load(cfg).idle_apps == ["Foo.exe"]
    cfg.write_text(base + "idle_apps = [1]\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(cfg)


def test_import_until_cutoff(tmp_path):
    db, cfg, post = tmp_path / "u.db", make_cfg(tmp_path), FakePost()
    make_db(db, [row(i, T0 + i * 100, T0 + i * 100 + 5) for i in range(1, 6)])
    until = datetime.fromtimestamp(T0 + 350, UTC)
    rep = import_activity_logger(db, "pc", cfg, post, until=until)
    ids = [e["source_event_id"] for c in post.calls for e in c[1]["events"]]
    assert ids == [1, 2, 3] and rep.read == 3


def test_cli_until_naive_rejected_and_dry_run_shows_cutoff(tmp_path, capsys):
    cfg_path = tmp_path / "cfg" / "config.toml"
    cfg_path.parent.mkdir()
    cfg_path.write_text('ingest_url = "https://i.example"\ndevice = "pc"\n', encoding="utf-8")
    db = tmp_path / "c.db"
    make_db(db, [row(1, T0, T0 + 5)])
    common = ["--config", str(cfg_path), "import-activity-logger", str(db), "--device", "g"]
    with pytest.raises(SystemExit):
        cli.main([*common, "--until", "2026-03-30T12:00:00"])
    capsys.readouterr()
    code = cli.main([*common, "--dry-run", "--until", "2026-03-30T13:00:00+00:00"])
    out = capsys.readouterr().out
    assert code == 0 and "until=2026-03-30T13:00:00+00:00" in out and "read=1" in out
