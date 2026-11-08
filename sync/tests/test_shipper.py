import json
import threading
from datetime import UTC, datetime, timedelta

from usage_sync.config import Config
from usage_sync.shipper import Shipper, backoff
from usage_sync.state import State

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BUCKET = "aw-watcher-window_pc"


class FakeAw:
    """Mimics aw-server: events overlapping [start, end], newest first, cut at `limit`."""

    def __init__(self, events, buckets=None):
        self._events = events
        self._buckets = buckets or {BUCKET: {"id": BUCKET, "type": "currentwindow"}}
        self.calls = []

    def buckets(self):
        return self._buckets

    def event(self, bucket_id, event_id):
        return next(e for e in self._events[bucket_id] if e["id"] == event_id)

    @staticmethod
    def _span(e):
        try:
            ts = datetime.fromisoformat(e["timestamp"])
            return ts, ts + timedelta(seconds=e.get("duration") or 0)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def events(self, bucket_id, start, end, limit=5000):
        self.calls.append((bucket_id, start, end, limit))
        hits = []
        for e in self._events.get(bucket_id, []):
            span = self._span(e)
            if span is None:
                # unparseable timestamp: only the first span of a full pull sees it
                if start == datetime(1970, 1, 1, tzinfo=UTC):
                    hits.append((datetime.min.replace(tzinfo=UTC), e))
            elif span[1] > start and span[0] < end:
                hits.append((span[0], e))
        hits.sort(key=lambda h: h[0], reverse=True)
        return [e for _, e in hits[:limit]]


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


def test_bad_event_attribute_error_is_skipped(tmp_path):
    good = ev(1, 30)
    huge = {**ev(3, 20), "duration": 1e18}  # OverflowError in timedelta
    sh, _, post, _ = make(tmp_path, [good, ev(2, 25), huge])
    sh.aw._events[BUCKET][1]["data"] = ["not", "a", "dict"]  # AttributeError in to_event
    rep = sh.sync_once()
    assert rep.skipped_bad == 2 and rep.shipped == 1 and rep.errors == []


def test_run_survives_sync_once_exception(tmp_path):
    sh, _, _, _ = make(tmp_path, [])
    waits = []

    def boom():
        raise RuntimeError("kaboom")

    sh.sync_once = boom  # type: ignore[method-assign]

    class Stop:
        def is_set(self):
            return len(waits) >= 3

        def wait(self, seconds):
            waits.append(seconds)
            return self.is_set()

    sh.run(Stop())  # type: ignore[arg-type]
    assert waits == [60, 120, 240]


def test_window_over_limit_is_paged_forward_without_loss(tmp_path, monkeypatch):
    monkeypatch.setattr("usage_sync.shipper.FETCH_LIMIT", 10)
    events = [ev(i, 3000 - i * 10, dur=5) for i in range(1, 201)]  # 200 events over ~33 hours
    sh, st, post, aw = make(tmp_path, events, batch_size=7)
    st.advance(BUCKET, NOW - timedelta(days=3))
    seen = []
    real = st.advance
    st.advance = lambda b, ts: (seen.append(ts), real(b, ts))  # type: ignore[method-assign]
    rep = sh.sync_once()
    assert rep.errors == []
    ids = [e["source_event_id"] for c in post.calls for e in c[1]["events"]]
    assert set(ids) == set(range(1, 201))
    firsts = [ids.index(i) for i in range(1, 201)]
    assert firsts == sorted(firsts)  # time order across spans
    assert seen == sorted(seen)
    assert st.cursor(BUCKET) == NOW - timedelta(minutes=1000) + timedelta(seconds=5)
    assert all(c[2] - c[1] <= timedelta(days=1) for c in aw.calls)


def test_span_floor_logs_error_and_terminates(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("usage_sync.shipper.FETCH_LIMIT", 3)
    events = [
        {
            "id": i,
            "timestamp": (NOW - timedelta(minutes=30, seconds=-i)).isoformat(),
            "duration": 0,
            "data": {"app": "code", "title": "t"},
        }
        for i in range(10)
    ]  # ten events inside one 5 minute span: it cannot be split further
    sh, st, post, aw = make(tmp_path, events)
    st.advance(BUCKET, NOW - timedelta(minutes=40))
    with caplog.at_level("ERROR"):
        rep = sh.sync_once()
    assert "row limit" in caplog.text and BUCKET in caplog.text
    assert rep.errors == [] and len(aw.calls) < 100
    assert min(c[2] - c[1] for c in aw.calls) == timedelta(minutes=5)


def test_failure_mid_way_keeps_cursor_at_last_shipped_span(tmp_path, monkeypatch):
    monkeypatch.setattr("usage_sync.shipper.FETCH_LIMIT", 5)
    # one event every 2 hours for ~2.7 days: a day holds 12 events, so spans shrink to 6h
    events = [ev(i, 4000 - i * 120, dur=60) for i in range(1, 34)]
    sh, st, post, aw = make(tmp_path, events)
    st.advance(BUCKET, NOW - timedelta(days=3))
    seen = []
    real = st.advance
    st.advance = lambda b, ts: (seen.append(ts), real(b, ts))  # type: ignore[method-assign]
    n = {"posts": 0}

    def flaky(url, body, headers):
        n["posts"] += 1
        if n["posts"] == 4:
            return 503, b"down"
        return post(url, body, headers)

    sh.post = flaky
    rep = sh.sync_once()
    assert rep.errors and "503" in rep.errors[0]
    assert len(seen) == 3 and seen == sorted(seen)
    assert st.cursor(BUCKET) == seen[-1]
    newest = max(datetime.fromisoformat(e["ts_end"]) for c in post.calls for e in c[1]["events"])
    assert st.cursor(BUCKET) == newest  # nothing past the last fully shipped span


def test_first_pull_from_epoch_widens_over_empty_spans(tmp_path):
    sh, st, post, aw = make(tmp_path, [ev(1, 60)])
    sh.sync_once()
    assert len(aw.calls) < 120 and aw.calls[0][1] == datetime(1970, 1, 1, tzinfo=UTC)
    assert [e["source_event_id"] for e in post.calls[0][1]["events"]] == [1]


# --- events trimmed by the AW query window are re-fetched untrimmed by id ---------------


class TrimmingAw(FakeAw):
    """Like aw-server-rust: events straddling the query window are trimmed to it."""

    def __init__(self, events, fail_ids=()):
        super().__init__(events)
        self.by_id_calls = []
        self.fail_ids = set(fail_ids)

    def events(self, bucket_id, start, end, limit=5000):
        out = []
        for e in super().events(bucket_id, start, end, limit):
            s, t = self._span(e)
            ns, ne = max(s, start), min(t, end)
            out.append({**e, "timestamp": ns.isoformat(), "duration": (ne - ns).total_seconds()})
        return out

    def event(self, bucket_id, event_id):
        from usage_sync.aw import AwError

        self.by_id_calls.append((bucket_id, event_id))
        if event_id in self.fail_ids:
            raise AwError("boom")
        return next(e for e in self._events[bucket_id] if e["id"] == event_id)


def make_trim(tmp_path, events, fail_ids=(), **over):
    (tmp_path / "token").write_text("sekrit\n", encoding="utf-8")
    cfg = Config(
        ingest_url="https://ingest.example", device="pc", token_file=tmp_path / "token", **over
    )
    post = FakePost()
    aw = TrimmingAw({BUCKET: events}, fail_ids)
    return Shipper(cfg, aw, State(tmp_path / "state.json"), post, lambda: NOW), post, aw


def shipped(post):
    return [e for _, body, _ in post.calls for e in body["events"]]


def test_event_straddling_span_start_is_shipped_untrimmed(tmp_path):
    sh, post, aw = make_trim(tmp_path, [ev(1, 60, dur=1800)], overlap_seconds=0)
    sh.state.advance(BUCKET, NOW - timedelta(minutes=45))
    rep = sh.sync_once()
    assert rep.errors == []
    (e,) = shipped(post)
    assert e["ts_start"] == (NOW - timedelta(minutes=60)).isoformat()
    assert aw.by_id_calls == [(BUCKET, 1)]


def test_event_straddling_two_spans_ships_once_with_full_extent(tmp_path, monkeypatch):
    import usage_sync.shipper as mod

    monkeypatch.setattr(mod, "INITIAL_SPAN", timedelta(minutes=30))
    sh, post, aw = make_trim(tmp_path, [ev(1, 60, dur=1800), ev(2, 5, dur=10)], overlap_seconds=0)
    # event 1 spans 11:00-11:30; start the pull at 10:50 so span edges cut through it
    sh.state.advance(BUCKET, NOW - timedelta(minutes=70))
    sh.sync_once()
    rows = [e for e in shipped(post) if e["source_event_id"] == 1]
    assert {r["ts_start"] for r in rows} == {(NOW - timedelta(minutes=60)).isoformat()}
    assert {r["ts_end"] for r in rows} == {(NOW - timedelta(minutes=30)).isoformat()}


def test_by_id_failure_falls_back_to_trimmed_copy(tmp_path):
    sh, post, aw = make_trim(tmp_path, [ev(1, 60, dur=1800)], fail_ids={1}, overlap_seconds=0)
    sh.state.advance(BUCKET, NOW - timedelta(minutes=45))
    rep = sh.sync_once()
    assert rep.errors == []
    (e,) = shipped(post)
    assert e["ts_start"] == (NOW - timedelta(minutes=45)).isoformat()


def test_non_boundary_events_trigger_no_by_id_calls(tmp_path):
    sh, post, aw = make_trim(tmp_path, [ev(1, 60), ev(2, 30)])
    sh.sync_once()
    assert aw.by_id_calls == []
    assert len(shipped(post)) == 2
