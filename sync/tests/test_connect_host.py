import pytest

from usage_sync import cli, shipper
from usage_sync.config import Config, ConfigError, parse_connect_host


def write_cfg(tmp_path, extra=""):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'ingest_url = "https://ingest.example"\ndevice = "pc"\n' + extra, encoding="utf-8"
    )
    return cfg


def test_parse_connect_host_forms():
    assert parse_connect_host("10.1.2.3") == ("10.1.2.3", None)
    assert parse_connect_host("node.example:8443") == ("node.example", 8443)
    assert parse_connect_host("[::1]:8443") == ("::1", 8443)
    assert parse_connect_host("[::1]") == ("::1", None)


@pytest.mark.parametrize("bad", ["", "  ", "host:abc", "host:0", "host:70000", "host:", ":443"])
def test_parse_connect_host_rejects(bad):
    with pytest.raises(ConfigError):
        parse_connect_host(bad)


def test_config_loads_and_validates_connect_host(tmp_path):
    cfg = Config.load(write_cfg(tmp_path, 'ingest_connect_host = "10.1.2.3:8443"\n'))
    assert cfg.ingest_connect_host == "10.1.2.3:8443"
    assert Config.load(write_cfg(tmp_path)).ingest_connect_host is None
    with pytest.raises(ConfigError):
        Config.load(write_cfg(tmp_path, 'ingest_connect_host = ""\n'))
    with pytest.raises(ConfigError):
        Config.load(write_cfg(tmp_path, 'ingest_connect_host = "h:99999"\n'))


def test_connect_host_requires_https(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'ingest_url = "http://ingest.example"\ndevice = "pc"\ningest_connect_host = "1.2.3.4"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="https"):
        Config.load(cfg)


@pytest.mark.parametrize("value", [0, -1, True, "10", None])
def test_aw_timeout_validation(tmp_path, value):
    text = "aw_timeout_seconds = " + ("true" if value is True else repr(value)).replace(
        "None", '""'
    )
    if value == "10":
        text = 'aw_timeout_seconds = "10"'
    with pytest.raises(ConfigError, match="aw_timeout_seconds"):
        Config.load(write_cfg(tmp_path, text + "\n"))


def test_aw_timeout_accepts_int_and_float(tmp_path):
    assert Config.load(write_cfg(tmp_path, "aw_timeout_seconds = 30\n")).aw_timeout_seconds == 30
    assert Config.load(write_cfg(tmp_path, "aw_timeout_seconds = 2.5\n")).aw_timeout_seconds == 2.5


class FakeCtx:
    def __init__(self):
        self.wrapped = None

    def wrap_socket(self, sock, server_hostname=None):
        self.wrapped = (sock, server_hostname)
        return "tls-socket"


def test_connection_dials_override_but_verifies_url_host(monkeypatch):
    dialed = []

    def fake_create(addr, timeout=None, source_address=None):
        dialed.append(addr)
        return "raw-socket"

    monkeypatch.setattr(shipper.socket, "create_connection", fake_create)
    ctx = FakeCtx()
    conn = shipper._ConnectHTTPSConnection(
        "ingest.example", context=ctx, connect_host="10.1.2.3", connect_port=None
    )
    conn.connect()
    assert dialed == [("10.1.2.3", 443)]
    assert ctx.wrapped == ("raw-socket", "ingest.example")
    assert conn.sock == "tls-socket"

    conn = shipper._ConnectHTTPSConnection(
        "ingest.example", 9443, context=ctx, connect_host="10.1.2.3", connect_port=8443
    )
    conn.connect()
    assert dialed[-1] == ("10.1.2.3", 8443)


def test_urllib_post_builds_with_and_without_override():
    assert callable(shipper.urllib_post(None))
    assert callable(shipper.urllib_post(None, "10.1.2.3:8443"))


def test_init_writes_and_status_shows_connect_host(tmp_path, capsys, monkeypatch):
    path = tmp_path / "cfg" / "config.toml"
    args = ["--config", str(path), "init", "--device", "pc", "--ingest-url", "https://i.example"]
    assert cli.main([*args, "--ingest-connect-host", "10.1.2.3:8443"]) == 0
    assert 'ingest_connect_host = "10.1.2.3:8443"' in path.read_text(encoding="utf-8")
    assert cli.main([*args, "--ingest-connect-host", "h:abc", "--force"]) == 2
    capsys.readouterr()

    class FakeAw:
        def __init__(self, *a, **k):
            pass

        def ping(self):
            return {}

    monkeypatch.setattr(cli, "AwClient", FakeAw)
    assert cli.main(["--config", str(path), "status"]) == 0
    assert "connect via: 10.1.2.3:8443" in capsys.readouterr().out


def test_all_post_factories_get_connect_host(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "urllib_post", lambda ca, ch=None: seen.append((ca, ch)) or None)
    cfg = Config(ingest_url="https://i.example", device="pc", ingest_connect_host="1.2.3.4")
    cli._make_shipper(cfg, tmp_path / "config.toml")
    assert seen == [(None, "1.2.3.4")]
