from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from test_auth import make_settings
from test_queries import CHI, REAL_MIGRATIONS, TZ, Seeder, local
from usage_web.app import create_app
from usage_web.migrate import apply

integration = pytest.mark.integration

RANGE = {"from": "2026-11-20", "to": "2026-11-21"}
EVENT_KEYS = {"id", "device", "ts_start", "ts_end", "kind", "app", "title", "url", "domain"}


@pytest.fixture
def seed(usage_schema_dsn):
    apply(usage_schema_dsn, REAL_MIGRATIONS)
    s = Seeder(usage_schema_dsn)
    s.dsn = usage_schema_dsn
    yield s
    s.conn.close()


@pytest.fixture
def client(seed):
    settings = make_settings(AUTH_MODE="none", USAGE_READER_DSN=seed.dsn, USAGE_TZ=TZ)
    with TestClient(create_app(settings)) as c:
        yield c


@integration
def test_hourly_defaults_to_today(client):
    body = client.get("/api/hourly").json()
    start = datetime.now(CHI).replace(hour=0, minute=0, second=0, microsecond=0)
    assert body["tz"] == TZ
    assert datetime.fromisoformat(body["from"]) == start
    assert datetime.fromisoformat(body["to"]) == start + timedelta(days=1)
    assert body["from"].endswith(("-05:00", "-06:00"))
    assert body["rows"] == []


@integration
def test_hour_range_validated(client):
    assert client.get("/api/top/apps", params={"hour_from": 24}).status_code == 422
    assert client.get("/api/top/apps", params={"hour_to": -1}).status_code == 422
    assert client.get("/api/top/apps", params={"hour_from": "x"}).status_code == 422
    resp = client.get("/api/top/apps", params={"hour_from": 5, "hour_to": 3})
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


@integration
def test_devices_lists_seeded(client, seed):
    seed.add("desk-a", "window", local(10), local(11), app="editor")
    body = client.get("/api/meta").json()
    assert body["tz"] == TZ
    [dev] = body["devices"]
    assert dev["device"] == "desk-a"
    assert dev["events"] == 1
    assert datetime.fromisoformat(dev["first_seen"]) == local(10)
    assert datetime.fromisoformat(dev["last_seen"]) == local(11)


@integration
def test_healthz_public_readyz_checks_db(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_readyz_503_without_pool():
    app = create_app(make_settings(AUTH_MODE="none"), with_pool=False)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").status_code == 503


@integration
def test_from_to_accept_dates_and_datetimes(client, seed):
    seed.add("desk-a", "window", local(10), local(11), app="editor")
    by_date = client.get("/api/hourly", params=RANGE).json()
    assert by_date["from"] == local(0).isoformat()
    assert by_date["to"] == local(0, day=21).isoformat()
    assert sum(r["seconds"] for r in by_date["rows"]) == 3600
    by_dt = client.get(
        "/api/hourly",
        params={"from": "2026-11-20T16:00:00+00:00", "to": "2026-11-20T17:00:00+00:00"},
    ).json()
    assert sum(r["seconds"] for r in by_dt["rows"]) == 3600
    assert datetime.fromisoformat(by_dt["from"]) == datetime.fromisoformat(
        "2026-11-20T16:00:00+00:00"
    )


@integration
def test_bad_date_is_422(client):
    for params in (
        {"from": "nope"},
        {"to": "2026-13-45"},
        {"from": "2026-11-21", "to": "2026-11-20"},
        {"from": "2026-11-20", "to": "2026-11-20"},
        {"from": "2026-11-20T10:00:00"},
    ):
        resp = client.get("/api/hourly", params=params)
        assert resp.status_code == 422, params
        assert isinstance(resp.json()["detail"], str)


@integration
def test_events_bad_cursor_is_422(client):
    resp = client.get("/api/events", params={**RANGE, "cursor": "garbage"})
    assert resp.status_code == 422
    assert resp.json() == {"detail": "invalid cursor"}


@integration
def test_events_shape_and_paging(client, seed):
    for h in (9, 10, 11):
        seed.add("desk-a", "window", local(h), local(h, 30), app="editor", title="t")
    page = client.get("/api/events", params={**RANGE, "limit": 2}).json()
    assert set(page["rows"][0]) == EVENT_KEYS
    assert len(page["rows"]) == 2 and page["next_cursor"]
    rest = client.get(
        "/api/events", params={**RANGE, "limit": 2, "cursor": page["next_cursor"]}
    ).json()
    assert len(rest["rows"]) == 1 and rest["next_cursor"] is None


@integration
def test_top_apps_shape(client, seed):
    seed.add("desk-a", "window", local(10), local(10, 10), app="small")
    seed.add("desk-a", "window", local(11), local(12), app="big")
    seed.add("desk-a", "web", local(11), local(12), domain="example.org")
    rows = client.get("/api/top/apps", params={**RANGE, "limit": 5}).json()["rows"]
    assert [r["app"] for r in rows] == ["big", "small"]
    assert rows[0]["seconds"] == 3600.0 and isinstance(rows[0]["seconds"], float)
    doms = client.get("/api/top/domains", params=RANGE).json()["rows"]
    assert doms == [{"domain": "example.org", "seconds": 3600.0}]
    assert client.get("/api/top/apps", params={"limit": 0}).status_code == 422
    assert client.get("/api/top/apps", params={"limit": 101}).status_code == 422


@integration
def test_hourly_hour_filter_drops_buckets(client, seed):
    seed.add("desk-a", "window", local(10), local(12), app="editor")
    params = {**RANGE, "hour_from": 11, "hour_to": 11}
    rows = client.get("/api/hourly", params=params).json()["rows"]
    assert [datetime.fromisoformat(r["bucket"]).astimezone(CHI).hour for r in rows] == [11]
    assert set(rows[0]) == {"bucket", "device", "app", "seconds"}


@integration
def test_daily_shape(client, seed):
    seed.add("desk-a", "window", local(10), local(11), app="editor")
    seed.add("desk-b", "window", local(10), local(10, 30), app="editor")
    rows = client.get("/api/daily", params=RANGE).json()["rows"]
    assert rows == [
        {"day": "2026-11-20", "device": "desk-a", "seconds": 3600.0},
        {"day": "2026-11-20", "device": "desk-b", "seconds": 1800.0},
    ]
    only_b = client.get("/api/daily", params={**RANGE, "device": "desk-b"}).json()["rows"]
    assert [r["device"] for r in only_b] == ["desk-b"]


def test_index_route_without_build_is_200(monkeypatch, tmp_path):
    monkeypatch.setattr("usage_web.app.STATIC_DIR", tmp_path / "missing")
    app = create_app(make_settings(AUTH_MODE="none"), with_pool=False)
    with TestClient(app) as c:
        resp = c.get("/")
        assert resp.status_code == 200
        assert "not built" in resp.text.lower()
        assert c.get("/assets/x.js").status_code == 404


def test_index_serves_built_frontend(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>built</html>")
    (tmp_path / "assets" / "a.js").write_text("1")
    monkeypatch.setattr("usage_web.app.STATIC_DIR", tmp_path)
    app = create_app(make_settings(AUTH_MODE="none"), with_pool=False)
    with TestClient(app) as c:
        assert "built" in c.get("/").text
        assert c.get("/assets/a.js").status_code == 200


def test_api_requires_session_when_oauth():
    app = create_app(make_settings(AUTH_MODE="oauth"), with_pool=False)
    with TestClient(app, follow_redirects=False) as c:
        resp = c.get("/api/meta")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "login required"}
        page = c.get("/")
        assert page.status_code == 302
        assert page.headers["location"].startswith("/auth/login")
        assert c.get("/healthz").status_code == 200
