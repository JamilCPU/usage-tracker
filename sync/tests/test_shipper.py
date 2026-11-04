import json
import threading
from datetime import UTC, datetime, timedelta

from usage_sync.config import Config
from usage_sync.shipper import Shipper, backoff
from usage_sync.state import State

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BUCKET = "aw-watcher-window_pc"


class FakeAw:
    def __init__(self, events, buckets=None):
        self._events = events
        self._buckets = buckets or {BUCKET: {"id": BUCKET, "type": "currentwindow"}}
        self.calls = []

    def buckets(self):
        return self._buckets

    def events(self, bucket_id, start, end, limit=5000):
        self.calls.append((bucket_id, start, end, limit))
        floor = start.isoformat()
        return [e for e in self._events.get(bucket_id, []) if e.get("timestamp", "z") >= floor]


class FakePost:
    def __init__(self, status=200, body=b'{"accepted":1,"inserted":1,"updated":0}'):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url, body, headers):
        self.calls.append((url, json.loads(body), headers))
        return self.status, self.body


def ev(i, minutes_ago, dur=10, app="code"):
    ts = NOW - timedelta(minutes=minutes_ago)
    return {
        "id": i,
        "timestamp": ts.isoformat(),
        "duration": dur,
        "data": {"app": app, "title": f"t{i}"},
    }


def make(tmp_path, events, post=None, **over):
    (tmp_path / "token").write_text("sekrit\n", encoding="utf-8")
    cfg = Config(
        ingest_url="https://ingest.example", device="pc", token_file=tmp_path / "token", **over
    )
    st = State(tmp_path / "state.json")
    post = post or FakePost()
    aw = FakeAw({BUCKET: events})
    return Shipper(cfg, aw, st, post, lambda: NOW), st, post, aw


def test_first_sync_ships_all_and_advances_to_settled_end(tmp_path):
    events = [ev(1, 60), ev(2, 30), ev(3, 1)]  # last is newer than settle (120s)
    sh, st, post, aw = make(tmp_path, events)
    rep = sh.sync_once()
    assert rep.buckets == 1 and rep.shipped == 3 and rep.errors == []
    assert rep.skipped_unsettled == 1
    url, body, headers = post.calls[0]
    assert url == "https://ingest.example/v1/ingest"
    assert headers["Authorization"] == "Bearer sekrit"
    assert body["source"] == "usage" and body["device"] == "pc"
    assert aw.calls[0][1] == datetime(1970, 1, 1, tzinfo=UTC)
    assert st.cursor(BUCKET) == NOW - timedelta(minutes=30) + timedelta(seconds=10)


def test_overlap_reships_recent_events(tmp_path):
    events = [ev(1, 600), ev(2, 30), ev(3, 20)]
    sh, st, post, aw = make(tmp_path, events)
    st.advance(BUCKET, NOW - timedelta(minutes=25))
    rep = sh.sync_once()
    assert aw.calls[0][1] == NOW - timedelta(minutes=25) - timedelta(seconds=3600)
    ids = [e["source_event_id"] for e in post.calls[0][1]["events"]]
    assert ids == [2, 3] and rep.shipped == 2


def test_failed_post_keeps_cursor_and_reports_error(tmp_path):
    sh, st, post, _ = make(tmp_path, [ev(1, 30)], FakePost(503, b"down"))
    rep = sh.sync_once()
    assert st.cursor(BUCKET) is None
    assert rep.errors == [f"{BUCKET}: HTTP 503 down"]
    assert rep.shipped == 0


def test_422_does_not_advance_cursor(tmp_path, caplog):
    sh, st, _, _ = make(tmp_path, [ev(1, 30)], FakePost(422, b'{"detail":"bad row"}'))
    with caplog.at_level("WARNING"):
        rep = sh.sync_once()
    assert st.cursor(BUCKET) is None
    assert "HTTP 422" in rep.errors[0]
    assert "bad row" in caplog.text
    assert "sekrit" not in caplog.text


def test_post_exception_is_reported(tmp_path):
    def boom(url, body, headers):
        raise OSError("refused")

    sh, st, _, _ = make(tmp_path, [ev(1, 30)], boom)
    rep = sh.sync_once()
    assert st.cursor(BUCKET) is None
    assert rep.errors == [f"{BUCKET}: refused"]


def test_events_sorted_by_start_in_batch(tmp_path):
    sh, _, post, _ = make(tmp_path, [ev(1, 10), ev(2, 50), ev(3, 30)])
    sh.sync_once()
    starts = [e["ts_start"] for e in post.calls[0][1]["events"]]
    assert starts == sorted(starts) and len(starts) == 3


def test_batches_respect_batch_size(tmp_path):
    sh, _, post, _ = make(tmp_path, [ev(i, 60 - i) for i in range(1, 8)], batch_size=3)
    rep = sh.sync_once()
    assert [len(c[1]["events"]) for c in post.calls] == [3, 3, 1]
    assert rep.shipped == 7


def test_bad_events_are_skipped_and_counted(tmp_path):
    bad = [
        {"id": 9, "duration": 1, "data": {}},  # no timestamp
        {"id": 10, "timestamp": "garbage", "duration": 1, "data": {}},
        ev(11, 30, dur=-5),  # negative duration
        ev(12, 40),
    ]
    sh, _, post, _ = make(tmp_path, bad)
    rep = sh.sync_once()
    assert rep.skipped_bad == 3 and rep.shipped == 1
    assert [e["source_event_id"] for e in post.calls[0][1]["events"]] == [12]


def test_duplicate_event_ids_collapse_last_wins(tmp_path):
    first, last = ev(5, 30, dur=1), ev(5, 30, dur=20)
    sh, _, post, _ = make(tmp_path, [first, last])
    rep = sh.sync_once()
    sent = post.calls[0][1]["events"]
    assert len(sent) == 1 and rep.shipped == 1
    ts_s, ts_e = (datetime.fromisoformat(sent[0][k]) for k in ("ts_start", "ts_end"))
    assert (ts_e - ts_s).total_seconds() == 20


def test_redaction_and_unknown_buckets(tmp_path):
    sh, _, post, aw = make(tmp_path, [ev(1, 30, app="KeePassXC")])
    aw._buckets["other"] = {"id": "other", "type": "unknown"}
    rep = sh.sync_once()
    assert rep.buckets == 1
    assert post.calls[0][1]["events"][0]["title"] is None


def test_no_events_posts_nothing(tmp_path):
    sh, st, post, _ = make(tmp_path, [])
    rep = sh.sync_once()
    assert post.calls == [] and rep.shipped == 0 and st.cursor(BUCKET) is None


def test_backoff_caps_at_600_seconds():
    assert backoff(0, 60) == 60
    assert backoff(2, 60) == 240
    assert backoff(10, 60) == 600
    assert backoff(50, 1) == 600


def test_run_backs_off_and_stops(tmp_path):
    sh, _, _, _ = make(tmp_path, [ev(1, 30)], FakePost(500, b"x"))
    waits = []

    class Stop:
        def is_set(self):
            return len(waits) >= 3

        def wait(self, seconds):
            waits.append(seconds)
            return self.is_set()

    sh.run(Stop())  # type: ignore[arg-type]
    assert waits == [60, 120, 240]


def test_run_accepts_real_event(tmp_path):
    sh, _, _, _ = make(tmp_path, [])
    stop = threading.Event()
    stop.set()
    sh.run(stop)
