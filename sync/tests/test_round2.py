import http.server
import logging
import os
import ssl
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_sync import cli, shipper
from usage_sync.config import Config, ConfigError, parse_connect_host
from usage_sync.importer import rows_to_events
from usage_sync.mapping import to_event


@pytest.mark.parametrize("ca_file", [None, "ca.pem"])
def test_ssl_context_verifies_certificate_and_hostname(monkeypatch, ca_file):
    real = ssl.create_default_context
    monkeypatch.setattr(shipper.ssl, "create_default_context", lambda cafile=None: real())
    ctx = shipper._ssl_context(ca_file)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED


def test_override_opener_has_no_proxy_and_no_redirects():
    opener = shipper._build_opener(ssl.create_default_context(), ("192.0.2.10", None))
    kinds = [type(h) for h in opener.handlers]
    assert shipper._NoRedirect in kinds
    # an explicit empty ProxyHandler replaces the default one, which reads proxy env vars
    proxy = [h for h in opener.handlers if isinstance(h, shipper.urllib.request.ProxyHandler)]
    assert proxy == []
    assert shipper._ConnectHTTPSHandler in kinds


def test_redirect_surfaces_as_status():
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", "http://192.0.2.99/elsewhere")
            self.end_headers()
            self.wfile.write(b"moved")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        post = shipper.urllib_post(None)
        status, body = post(f"http://127.0.0.1:{srv.server_port}/v1/ingest", b"{}", {})
    finally:
        srv.shutdown()
        srv.server_close()
    assert status == 302 and body == b"moved"


def test_config_error_is_logged_without_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    root = logging.getLogger()
    before = list(root.handlers)
    cfg = tmp_path / "config.toml"
    cfg.write_text('device = "pc"\n', encoding="utf-8")
    try:
        code = cli.main(["--config", str(cfg), "status"])
        for h in root.handlers:
            h.flush()
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
    assert code == 2
    assert "missing required config keys" in (tmp_path / "usage-sync.log").read_text("utf-8")


def test_module_entry_point_with_pythonpath():
    import subprocess

    src = str(Path(cli.__file__).parent.parent)
    env = {**os.environ, "PYTHONPATH": src}
    res = subprocess.run(
        [sys.executable, "-m", "usage_sync", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert res.returncode == 0


@pytest.mark.parametrize("bad", ["[::1", "host:٣٣", "[]:443", "[::1]x"])
def test_connect_host_parser_rejects_more(bad):
    with pytest.raises(ConfigError):
        parse_connect_host(bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_aw_timeout_rejects_nan_inf(bad):
    with pytest.raises(ConfigError):
        Config(ingest_url="https://i.example", device="pc", aw_timeout_seconds=bad)


def test_init_rejects_connect_host_with_http(tmp_path, capsys):
    args = ["--config", str(tmp_path / "c.toml"), "init", "--device", "pc"]
    code = cli.main(
        [*args, "--ingest-url", "http://i.example", "--ingest-connect-host", "192.0.2.10"]
    )
    assert code == 2 and "https" in capsys.readouterr().err


def test_nul_bytes_stripped_in_mapping_and_importer():
    raw = {
        "id": 1,
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "duration": 1,
        "data": {"app": "a\x00b", "title": "t\x00"},
    }
    ev = to_event("b", "window", raw)
    assert ev["app"] == "ab" and ev["title"] == "t"
    web = {**raw, "data": {"url": "https://x.example/\x00p", "title": "x\x00", "app": "c\x00"}}
    ev = to_event("b", "web", web)
    assert "\x00" not in ev["url"] and "\x00" not in ev["domain"] and ev["app"] == "c"
    row = {
        "id": 1,
        "start_time": 1.0,
        "end_time": 2.0,
        "duration_seconds": 1.0,
        "is_idle": 0,
        "process_name": "p\x00.exe",
        "app_name": "x",
        "window_title": "w\x00",
    }
    out = list(rows_to_events([row], "pc"))[0]
    assert out["app"] == "p.exe" and out["title"] == "w"
