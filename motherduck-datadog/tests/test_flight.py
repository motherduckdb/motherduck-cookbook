import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flight  # noqa: E402

UTC = dt.timezone.utc


def test_series_groups_points_by_metric_and_tags():
    s = flight.Series("md", ["env:test"])
    t0 = dt.datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    s.add("queries.count", flight.COUNT, t0, 3, {"query_type": "QUERY"})
    s.add("queries.count", flight.COUNT, t0 + dt.timedelta(minutes=1), 5, {"query_type": "QUERY"})
    s.add("queries.count", flight.COUNT, t0, 1, {"query_type": "DML"})
    s.add("queries.running", flight.GAUGE, t0, None)  # ignored
    batches = s.payload_batches()
    assert len(batches) == 1
    metrics = {(e["metric"], tuple(e["tags"])): e for e in batches[0]["series"]}
    assert len(metrics) == 2
    entry = metrics[("md.queries.count", ("env:test", "query_type:query"))]
    assert entry["type"] == flight.COUNT and entry["interval"] == 60
    assert [p["value"] for p in entry["points"]] == [3.0, 5.0]
    assert entry["points"][0]["timestamp"] == int(t0.timestamp())


def test_series_batches_respect_batch_size(monkeypatch):
    monkeypatch.setattr(flight, "BATCH_SIZE", 2)
    s = flight.Series("md", [])
    for i in range(5):
        s.add("m", flight.GAUGE, dt.datetime.now(UTC), i, {"i": str(i)})
    assert [len(b["series"]) for b in s.payload_batches()] == [2, 2, 1]


def test_clean_tag_normalises_values():
    assert flight.clean_tag("Mega") == "mega"
    assert flight.clean_tag("ryan@motherduck-com") == "ryan_motherduck-com"
    assert flight.clean_tag("") == "unknown"
    assert flight.clean_tag("a" * 300) == "a" * 200


def test_resolve_window_without_state(monkeypatch):
    monkeypatch.setattr(flight, "STATE_TABLE", "")
    monkeypatch.setattr(flight, "WINDOW_MINUTES", 5)
    monkeypatch.setattr(flight, "DELAY_SECONDS", 60)
    now = dt.datetime(2026, 10, 1, 10, 5, 7, tzinfo=UTC)
    start, end = flight.resolve_window(con=None, now=now)
    assert end == dt.datetime(2026, 10, 1, 10, 4, tzinfo=UTC)
    assert start == dt.datetime(2026, 10, 1, 9, 59, tzinfo=UTC)


def test_resolve_window_continues_from_state_and_caps_catch_up(monkeypatch):
    monkeypatch.setattr(flight, "DELAY_SECONDS", 60)
    now = dt.datetime(2026, 10, 1, 10, 5, 7, tzinfo=UTC)
    monkeypatch.setattr(flight, "load_window_end", lambda con: dt.datetime(2026, 10, 1, 9, 57, tzinfo=UTC))
    assert flight.resolve_window(None, now) == (
        dt.datetime(2026, 10, 1, 9, 57, tzinfo=UTC), dt.datetime(2026, 10, 1, 10, 4, tzinfo=UTC))
    monkeypatch.setattr(flight, "load_window_end", lambda con: dt.datetime(2026, 10, 1, 7, 0, tzinfo=UTC))
    start, end = flight.resolve_window(None, now)
    assert end - start == flight.MAX_POINT_AGE


def test_resolve_secret_accepts_namespaced_name(monkeypatch):
    monkeypatch.delenv("DD_API_KEY", raising=False)
    monkeypatch.setenv("datadog_DD_API_KEY", " abc ")
    assert flight.resolve_secret("DD_API_KEY") == "abc"
    monkeypatch.setenv("DD_API_KEY", "bare")
    assert flight.resolve_secret("DD_API_KEY") == "bare"


def test_state_parts_validates_identifiers(monkeypatch):
    monkeypatch.setattr(flight, "STATE_TABLE", "db.main.state")
    assert flight.state_parts() == ("db", "main", "state")
    monkeypatch.setattr(flight, "STATE_TABLE", "db.main.state; DROP TABLE x")
    try:
        flight.state_parts()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
