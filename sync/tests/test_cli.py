import stat
import sys
from datetime import UTC, datetime

import pytest

from usage_sync import cli
from usage_sync.shipper import SyncReport
from usage_sync.state import State


def run(args, capsys):
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def write_cfg(tmp_path, token=True):
    cfg = tmp_path / "cfg" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('ingest_url = "https://ingest.example"\ndevice = "pc"\n', encoding="utf-8")
    if token:
        (cfg.parent / "token").write_text("sekrit-token\n", encoding="utf-8")
    return cfg


def test_init_writes_config_without_token(tmp_path, capsys):
    cfg = tmp_path / "newdir" / "config.toml"
    args = ["--config", str(cfg), "init", "--device", "pc", "--ingest-url", "https://i.example"]
    code, out, _ = run([*args, "--ca-file", "/x/ca.pem"], capsys)
    assert code == 0
    text = cfg.read_text(encoding="utf-8")
    assert 'device = "pc"' in text and 'ingest_url = "https://i.example"' in text
    assert 'ca_file = "/x/ca.pem"' in text
    assert "token" not in text.replace("token_file", "")
    assert not (cfg.parent / "token").exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700
    # refuses to overwrite without --force
    code, _, err = run(args, capsys)
    assert code != 0 and "exists" in err
    code, _, _ = run([*args, "--force"], capsys)
    assert code == 0
    from usage_sync.config import Config

    assert Config.load(cfg).device == "pc"


class FakeAw:
    def __init__(self, base_url, timeout=10):
        pass

    def ping(self):
        return {"version": "v0"}


def test_status_lists_cursors(tmp_path, capsys, monkeypatch):
    cfg = write_cfg(tmp_path)
    State(cfg.parent / "state.json").advance(
        "aw-watcher-window_pc", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    )
    monkeypatch.setattr(cli, "AwClient", FakeAw)
    code, out, _ = run(["--config", str(cfg), "status"], capsys)
    assert code == 0
    assert "pc" in out and "https://ingest.example" in out
    assert "token file: present" in out
    assert "reachable" in out
    assert "aw-watcher-window_pc" in out and "2026-01-02T03:04:05" in out
    assert "sekrit-token" not in out


def test_status_reports_unreachable_and_missing_token(tmp_path, capsys, monkeypatch):
    from usage_sync.aw import AwError

    class Down(FakeAw):
        def ping(self):
            raise AwError("down")

    cfg = write_cfg(tmp_path, token=False)
    monkeypatch.setattr(cli, "AwClient", Down)
    code, out, _ = run(["--config", str(cfg), "status"], capsys)
    assert code == 0 and "token file: missing" in out and "unreachable" in out


@pytest.mark.parametrize(("errors", "expected"), [([], 0), (["boom"], 1)])
def test_once_exit_code_on_errors(tmp_path, capsys, monkeypatch, errors, expected):
    cfg = write_cfg(tmp_path)

    class FakeShipper:
        def __init__(self, *a, **k):
            pass

        def sync_once(self):
            return SyncReport(buckets=2, shipped=5, errors=errors)

    monkeypatch.setattr(cli, "Shipper", FakeShipper)
    monkeypatch.setattr(cli, "AwClient", FakeAw)
    code, out, _ = run(["--config", str(cfg), "once"], capsys)
    assert code == expected
    assert "shipped=5" in out


def test_bad_config_exit_code(tmp_path, capsys):
    code, _, err = run(["--config", str(tmp_path / "nope.toml"), "once"], capsys)
    assert code == 2 and "cannot read config" in err


def test_run_loops_until_stop(tmp_path, capsys, monkeypatch):
    cfg = write_cfg(tmp_path)
    seen = {}

    class FakeShipper:
        def __init__(self, *a, **k):
            pass

        def run(self, stop):
            seen["stop"] = stop
            stop.set()

    monkeypatch.setattr(cli, "Shipper", FakeShipper)
    monkeypatch.setattr(cli, "AwClient", FakeAw)
    code, _, _ = run(["--config", str(cfg), "run"], capsys)
    assert code == 0 and seen["stop"].is_set()


def test_module_entry_point_runs():
    import subprocess

    res = subprocess.run(
        [sys.executable, "-m", "usage_sync", "--help"], capture_output=True, text=True, timeout=60
    )
    assert res.returncode == 0 and "usage-sync" in res.stdout


def test_logs_to_rotating_file_without_stderr(tmp_path, capsys, monkeypatch):
    import logging

    cfg = write_cfg(tmp_path)
    monkeypatch.setattr(cli, "AwClient", FakeAw)
    monkeypatch.setattr(sys, "stderr", None)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        code = cli.main(["--config", str(cfg), "status"])
        logging.getLogger("usage_sync").info("hello-from-test")
        for h in root.handlers:
            h.flush()
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
    out = capsys.readouterr().out
    assert code == 0 and "log file:" in out
    assert "hello-from-test" in (cfg.parent / "usage-sync.log").read_text(encoding="utf-8")


def test_status_shows_aw_url(tmp_path, capsys, monkeypatch):
    cfg = write_cfg(tmp_path)
    cfg.write_text(cfg.read_text(encoding="utf-8") + 'aw_url = "http://phone.example:5600"\n')
    monkeypatch.setattr(cli, "AwClient", FakeAw)
    code, out, _ = run(["--config", str(cfg), "status"], capsys)
    assert code == 0
    assert "aw url:     http://phone.example:5600" in out


def test_aw_timeout_from_config(tmp_path, capsys, monkeypatch):
    from usage_sync.config import Config

    assert Config(ingest_url="https://i.example", device="pc").aw_timeout_seconds == 10
    cfg = write_cfg(tmp_path)
    cfg.write_text(cfg.read_text(encoding="utf-8") + "aw_timeout_seconds = 25\n")
    assert Config.load(cfg).aw_timeout_seconds == 25
    seen = []

    class Spy(FakeAw):
        def __init__(self, base_url, timeout=10):
            seen.append(timeout)

    class FakeShipper:
        def __init__(self, *a, **k):
            pass

        def sync_once(self):
            return SyncReport()

    monkeypatch.setattr(cli, "AwClient", Spy)
    monkeypatch.setattr(cli, "Shipper", FakeShipper)
    run(["--config", str(cfg), "once"], capsys)
    run(["--config", str(cfg), "status"], capsys)
    assert seen == [25, 25]


def test_instance_config_dir_isolated(tmp_path, capsys, monkeypatch):
    from usage_sync.config import Config

    dirs = []
    for name in ("a", "b"):
        cfg = tmp_path / f"usage-sync-{name}" / "config.toml"
        cfg.parent.mkdir()
        cfg.write_text(f'ingest_url = "https://i.example"\ndevice = "{name}"\n', encoding="utf-8")
        dirs.append(cfg)
    assert Config.load(dirs[0]).token_file == dirs[0].parent / "token"
    assert Config.load(dirs[1]).token_file == dirs[1].parent / "token"
    State(dirs[0].parent / "state.json").advance("bkt", datetime(2026, 1, 1, tzinfo=UTC))
    monkeypatch.setattr(cli, "AwClient", FakeAw)
    _, out_a, _ = run(["--config", str(dirs[0]), "status"], capsys)
    _, out_b, _ = run(["--config", str(dirs[1]), "status"], capsys)
    assert "bkt" in out_a and "cursors: none yet" in out_b
    assert not (dirs[1].parent / "state.json").exists()


def test_install_script_bash_syntax():
    import shutil
    import subprocess
    from pathlib import Path

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    script = Path(__file__).parents[1] / "install" / "linux" / "install-sync.sh"
    result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
