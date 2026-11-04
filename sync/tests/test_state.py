import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from usage_sync import config as config_mod
from usage_sync.config import Config, ConfigError, default_config_dir
from usage_sync.redact import DEFAULT_APPS, DEFAULT_DOMAINS
from usage_sync.state import State

T = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_state_round_trip_and_atomic_write(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    st = State(path)
    assert st.cursor("b1") is None
    st.advance("b1", T)
    st.advance("b1", T - timedelta(hours=1))  # older: ignored
    assert st.cursor("b1") == T
    assert State(path).cursor("b1") == T
    assert json.loads(path.read_text()) == {"b1": T.isoformat()}

    replaced = []
    real = os.replace

    def spy(src, dst):
        replaced.append((src, dst))
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    st.advance("b1", T + timedelta(minutes=1))
    assert len(replaced) == 1
    src, dst = map(Path, replaced[0])
    assert dst == path and src.parent == path.parent
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_state_tolerates_corrupt_and_missing_file(tmp_path, caplog):
    path = tmp_path / "state.json"
    assert State(path).cursor("x") is None
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        st = State(path)
    assert st.cursor("x") is None
    assert "corrupt" in caplog.text.lower()
    st.advance("x", T)
    assert State(path).cursor("x") == T


def test_config_defaults_and_token_file_location(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('ingest_url = "https://ingest.example"\ndevice = "pc"\n', encoding="utf-8")
    cfg = Config.load(p)
    assert cfg.aw_url == "http://127.0.0.1:5600"
    assert (cfg.interval_seconds, cfg.batch_size) == (60, 1000)
    assert (cfg.settle_seconds, cfg.overlap_seconds) == (120, 3600)
    assert cfg.redact_apps == DEFAULT_APPS and cfg.redact_domains == DEFAULT_DOMAINS
    assert cfg.token_file == tmp_path / "token"
    assert cfg.ca_file is None


def test_config_overrides_and_errors(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        'ingest_url = "u"\ndevice = "d"\nbatch_size = 5\nca_file = "/x/ca.pem"\n'
        'token_file = "/t/tok"\n',
        encoding="utf-8",
    )
    cfg = Config.load(p)
    assert cfg.batch_size == 5 and cfg.ca_file == "/x/ca.pem" and cfg.token_file == Path("/t/tok")
    p.write_text('ingest_url = "u"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(p)
    p.write_text('ingest_url = "u"\ndevice = "d"\nbogus = 1\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="bogus"):
        Config.load(p)


def test_default_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "os", SimpleNamespace(name="posix", environ=os.environ))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_config_dir() == tmp_path / "usage-sync"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert default_config_dir() == Path.home() / ".config" / "usage-sync"
    monkeypatch.setattr(config_mod, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_config_dir() == tmp_path / "usage-sync"
