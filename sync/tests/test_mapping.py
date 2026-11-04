import io
import json
from datetime import UTC, datetime

from usage_sync import aw
from usage_sync.mapping import bucket_kind, to_event


def test_window_event_maps_app_title_and_end(fixture):
    ev = to_event("aw-watcher-window_HOST", "window", fixture("window_events.json")[0])
    assert ev["app"] == "Code.exe"
    assert ev["title"] == "main.py - project"
    assert ev["kind"] == "window"
    assert ev["ts_start"] == datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    assert ev["ts_end"] == datetime(2026, 11, 4, 15, 0, 12, 500000, tzinfo=UTC)
    assert ev["source_bucket"] == "aw-watcher-window_HOST"
    assert ev["source_event_id"] == 123
    assert ev["idle"] is False
    assert ev["url"] is None and ev["domain"] is None


def test_zero_duration_and_offset_timestamp(fixture):
    ev = to_event("b", "window", fixture("window_events.json")[1])
    assert ev["ts_end"] == ev["ts_start"]
    assert ev["ts_start"] == datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    assert ev["ts_start"].tzinfo is not None


def test_afk_event_sets_idle(fixture):
    afk, active = fixture("afk_events.json")
    e1 = to_event("aw-watcher-afk_HOST", "afk", afk)
    e2 = to_event("aw-watcher-afk_HOST", "afk", active)
    assert e1["idle"] is True and e1["app"] is None
    assert e2["idle"] is False


def test_web_event_domain_and_incognito_skipped(fixture):
    normal, incognito = fixture("web_events.json")
    ev = to_event("aw-watcher-web-chrome", "web", normal)
    assert ev["domain"] == "docs.example.com"
    assert ev["url"] == "https://docs.example.com/page?q=1"
    assert ev["title"] == "Docs"
    assert ev["app"] is None
    assert to_event("aw-watcher-web-chrome", "web", incognito) is None


def test_android_bucket_is_window_kind(fixture):
    bucket = fixture("buckets.json")["aw-watcher-android-test"]
    assert bucket_kind(bucket) == "window"
    ev = to_event(bucket["id"], "window", fixture("android_events.json")[0])
    assert ev["app"] == "com.android.chrome"


def test_bucket_kinds(fixture):
    buckets = fixture("buckets.json")
    assert bucket_kind(buckets["aw-watcher-window_HOST"]) == "window"
    assert bucket_kind(buckets["aw-watcher-afk_HOST"]) == "afk"
    assert bucket_kind(buckets["aw-watcher-web-chrome"]) == "web"


def test_unknown_bucket_type_is_none(fixture):
    assert bucket_kind(fixture("buckets.json")["aw-stopwatch"]) is None
    assert bucket_kind({}) is None


def test_events_url_has_iso_bounds(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return Resp(json.dumps([]).encode())

    monkeypatch.setattr(aw.urllib.request, "urlopen", fake_urlopen)
    client = aw.AwClient("http://127.0.0.1:5600/", timeout=3)
    start = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    end = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)
    assert client.events("aw-watcher-window_HOST", start, end, limit=10) == []
    url = seen["url"]
    assert url.startswith("http://127.0.0.1:5600/api/0/buckets/aw-watcher-window_HOST/events?")
    assert "start=2026-11-04T15%3A00%3A00Z" in url
    assert "end=2026-11-04T16%3A00%3A00Z" in url
    assert "limit=10" in url
    assert seen["timeout"] == 3
