import datetime as dt
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import alerts  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def check(name="c", warning=2.0, critical=5.0, renotify=0, op=">"):
    return {"name": name, "title": name, "sql": "", "op": op, "warning": warning, "critical": critical,
            "unit": "%", "renotify_minutes": renotify, "runbook": "do things"}


def result(c, value, group="all"):
    return {"check": c, "group": group, "value": value, "status": alerts.classify(c, value), "error": ""}


def test_classify_thresholds():
    c = check()
    assert alerts.classify(c, 1.0) == "ok"
    assert alerts.classify(c, 2.0) == "ok"  # strictly greater
    assert alerts.classify(c, 3.0) == "warning"
    assert alerts.classify(c, 5.1) == "critical"
    assert alerts.classify(check(warning=None, critical=0.0), 1.0) == "critical"
    assert alerts.classify(check(op="<", warning=10, critical=5), 4.0) == "critical"


def test_new_alert_notifies_and_steady_alert_does_not(monkeypatch):
    c = check()
    monkeypatch.setattr(alerts, "CHECKS", [c])
    d = alerts.decide([result(c, 7.0)], {}, NOW)
    assert [(x["old_status"], x["new_status"], x["kind"], x["notify"]) for x in d] == [("ok", "critical", "escalated", True)]
    prev = {("c", "all"): {"status": "critical", "value": 7.0, "since": NOW, "last_notified_at": NOW}}
    d = alerts.decide([result(c, 8.0)], prev, NOW + dt.timedelta(minutes=5))
    assert d[0]["kind"] == "steady" and not d[0]["notify"]
    assert d[0]["since"] == NOW  # episode start is preserved


def test_renotify_after_interval(monkeypatch):
    c = check(renotify=60)
    monkeypatch.setattr(alerts, "CHECKS", [c])
    prev = {("c", "all"): {"status": "critical", "value": 7.0, "since": NOW, "last_notified_at": NOW}}
    assert not alerts.decide([result(c, 7.0)], prev, NOW + dt.timedelta(minutes=59))[0]["notify"]
    d = alerts.decide([result(c, 7.0)], prev, NOW + dt.timedelta(minutes=60))[0]
    assert d["notify"] and d["kind"] == "renotify"


def test_resolution_and_disappearing_group(monkeypatch):
    c = check(name="flight_run_failed", warning=None, critical=0.0)
    monkeypatch.setattr(alerts, "CHECKS", [c])
    monkeypatch.setattr(alerts, "NOTIFY_ON_RESOLVE", True)
    prev = {("flight_run_failed", "etl"): {"status": "critical", "value": 1.0, "since": NOW, "last_notified_at": NOW},
            ("flight_run_failed", "ok_one"): {"status": "ok", "value": 0.0, "since": NOW, "last_notified_at": None}}
    d = alerts.decide([], prev, NOW)
    assert [(x["group"], x["new_status"], x["kind"], x["notify"]) for x in d] == [("etl", "ok", "resolved", True)]
    monkeypatch.setattr(alerts, "NOTIFY_ON_RESOLVE", False)
    assert not alerts.decide([], prev, NOW)[0]["notify"]


def test_alert_level_critical_mutes_warnings(monkeypatch):
    c = check()
    monkeypatch.setattr(alerts, "CHECKS", [c])
    monkeypatch.setattr(alerts, "ALERT_LEVEL", "critical")
    d = alerts.decide([result(c, 3.0)], {}, NOW)[0]
    assert d["new_status"] == "warning" and d["kind"] == "escalated" and not d["notify"]


def test_broken_rule_is_ignored_not_fired(monkeypatch):
    c = check()
    monkeypatch.setattr(alerts, "CHECKS", [c])
    broken = {"check": c, "group": "all", "value": None, "status": "error", "error": "boom"}
    assert alerts.decide([broken], {}, NOW) == []


def test_rules_are_valid_sql_against_the_schema(monkeypatch):
    """Every rule parses and runs against empty tables with the collector's DDL."""
    import flight
    con = duckdb.connect()
    for table, (_tc, columns) in flight.TABLES.items():
        con.execute(f'CREATE TABLE "memory"."main"."{table}" ({columns})')
    monkeypatch.setattr(alerts, "TARGET_DATABASE", "memory")
    results = alerts.evaluate_all(con)
    assert all(r["status"] != "error" for r in results), [r for r in results if r["status"] == "error"]
    # with no collector runs at all, the "not reporting" rule must fire
    by_name = {r["check"]["name"]: r for r in results}
    assert by_name["collector_not_reporting"]["status"] == "critical"


def md_information_schema(con):
    """A stand-in for the live metadata views the `details` queries read."""
    con.execute("CREATE SCHEMA md_information_schema")
    for view in ("query_history", "recent_queries"):
        con.execute(f"""
            CREATE TABLE md_information_schema.{view} (
                query_id UUID, query_text VARCHAR, start_time TIMESTAMPTZ, end_time TIMESTAMPTZ,
                total_elapsed_time INTERVAL, error_message VARCHAR, error_type VARCHAR, user_name VARCHAR,
                instance_type VARCHAR, bytes_spilled_to_disk UBIGINT, duckling_id VARCHAR)""")
    con.execute("""
        INSERT INTO md_information_schema.query_history VALUES
        ('81a13af2-2142-41ea-98e8-1efa1e3aa62f', 'SELECT   *\n FROM big', now() - INTERVAL 5 MINUTE, now() - INTERVAL 4 MINUTE,
         INTERVAL 61 SECOND, 'out of memory', 'OUT_OF_MEMORY', 'etl', 'Jumbo', 34800000000, 'etl.rw')""")
    con.execute("""
        INSERT INTO md_information_schema.recent_queries VALUES
        ('81a13af2-2142-41ea-98e8-1efa1e3aa62f', 'CALL ducklake_merge', now() - INTERVAL 20 MINUTE, NULL,
         NULL, NULL, NULL, 'etl', 'Jumbo', 0, 'etl.rw')""")


def test_details_queries_run_and_are_formatted(monkeypatch):
    con = duckdb.connect()
    md_information_schema(con)
    monkeypatch.setattr(alerts, "DETAIL_ROWS", 3)
    by_name = {c["name"]: c for c in alerts.CHECKS}
    for name, group in (("query_error_rate", "all"), ("p95_latency", "all"), ("spill_to_disk", "Jumbo")):
        rows = alerts.fetch_details(con, by_name[name], group)
        assert len(rows) == 1, name
        assert rows[0]["query_id"] == "81a13af2-2142-41ea-98e8-1efa1e3aa62f" and rows[0]["duckling_id"] == "etl.rw"
    assert alerts.fetch_details(con, by_name["spill_to_disk"], "Pulse") == []
    spill = alerts.fetch_details(con, by_name["spill_to_disk"], "Jumbo")[0]
    assert spill["detail"] == "34.8 GB spilled" and spill["query_text"] == "SELECT * FROM big"
    # long-running: the running query over the threshold, and one that just finished after running too long
    con.execute("""
        INSERT INTO md_information_schema.query_history VALUES
        ('81a13af3-0000-0000-0000-000000000000', 'CALL ducklake_merge', now() - INTERVAL 25 MINUTE, now() - INTERVAL 4 MINUTE,
         INTERVAL 1205 SECOND, NULL, NULL, 'etl', 'Jumbo', 0, 'etl.rw')""")
    rows = alerts.fetch_details(con, by_name["long_running_query"], "all")
    assert [r["query_id"][:8] for r in rows] == ["81a13af3", "81a13af2"]
    assert rows[0]["detail"].startswith("ran for 1205 s on Jumbo, finished ") and rows[1]["detail"] == "running for 1200 s on Jumbo"
    assert alerts.fetch_details(con, by_name["query_error_rate"], "all")[0]["detail"] == "OUT_OF_MEMORY: out of memory"
    # a group that needs quoting cannot break the SQL, and a rule without details returns nothing
    assert alerts.fetch_details(con, by_name["spill_to_disk"], "it's") == []
    assert alerts.fetch_details(con, by_name["flight_run_failed"], "etl") == []
    monkeypatch.setattr(alerts, "DETAIL_ROWS", 0)
    assert alerts.fetch_details(con, by_name["spill_to_disk"], "Jumbo") == []


def test_dive_links_encode_state_in_the_fragment(monkeypatch):
    import base64, json
    monkeypatch.setattr(alerts, "DIVE_URL", "https://app.motherduck.com/dives/abc")
    link = alerts.query_link("81a13af2-2142-41ea-98e8-1efa1e3aa62f", NOW)
    base, payload = link.split("#state=")
    assert base == "https://app.motherduck.com/dives/abc" and "=" not in payload
    state = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert state == {"tab": "log", "window": {"from": "2026-10-09T06:00", "to": "2026-10-09T13:00"},
                     "filters": {"query": "81a13af2-2142-41ea-98e8-1efa1e3aa62f"}}
    spill = next(c for c in alerts.CHECKS if c["name"] == "spill_to_disk")
    payload = alerts.check_link(spill, "Jumbo").split("#state=")[1]
    assert json.loads(base64.urlsafe_b64decode(payload + "==")) == {"tab": "log", "logsort": "spilled", "filters": {"instance": "Jumbo"}}
    assert alerts.check_link({"name": "x"}, "all") == ""
    monkeypatch.setattr(alerts, "DIVE_URL", "")
    assert alerts.query_link("81a13af2", NOW) == "" and alerts.check_link(spill, "Jumbo") == ""


def test_slack_text_lists_example_queries(monkeypatch):
    monkeypatch.setattr(alerts, "DIVE_URL", "https://app.motherduck.com/dives/abc")
    c = {**check(name="spill_to_disk", warning=25, critical=100), "unit": "GB",
         "dive": {"tab": "log", "filters": {"instance": "{group}"}}}
    d = {"check": c, "group": "Jumbo", "value": 65.5, "old_status": "ok", "new_status": "warning", "kind": "escalated",
         "notify": True, "since": NOW, "last_notified_at": None,
         "details": [{"query_id": "81a13af2-2142-41ea-98e8-1efa1e3aa62f", "duckling_id": "etl.rw", "user_name": "etl",
                      "detail": "34.8 GB spilled", "query_text": "SELECT `a` FROM t WHERE x < 1 & y > 2"}]}
    text = alerts.slack_text(d, NOW)
    assert "|open in the Dive>" in text
    assert "<https://app.motherduck.com/dives/abc#state=" in text and "|81a13af2>" in text
    assert "duckling `etl.rw` · user `etl` · 34.8 GB spilled" in text
    assert "`SELECT 'a' FROM t WHERE x &lt; 1 &amp; y &gt; 2`" in text
    assert alerts.detail_line(d["details"][0]).startswith("- 81a13af2 duckling=etl.rw user=etl 34.8 GB spilled: SELECT")


def test_resolved_message_has_value_duration_and_examples(monkeypatch):
    monkeypatch.setattr(alerts, "DIVE_URL", "https://app.motherduck.com/dives/abc")
    c = {**check(name="long_running_query", warning=900, critical=1800), "unit": "s", "dive": {"tab": "users"}}
    prev = {("long_running_query", "all"): {"status": "warning", "value": 1205.0, "since": NOW - dt.timedelta(minutes=5),
                                             "last_notified_at": NOW - dt.timedelta(minutes=5)}}
    monkeypatch.setattr(alerts, "CHECKS", [c])
    d = alerts.decide([result(c, 129.5)], prev, NOW)[0]
    assert d["kind"] == "resolved" and d["episode_start"] == NOW - dt.timedelta(minutes=5)
    d["details"] = [{"query_id": "81a13af3-0000", "duckling_id": "etl.rw", "user_name": "etl",
                     "detail": "ran for 1205 s on Jumbo, finished 15:30 UTC", "query_text": "CALL ducklake_merge"}]
    text = alerts.slack_text(d, NOW)
    assert text.startswith(":large_green_circle: *Resolved:* long_running_query — now 129.5 s, after 5 min warning · <https://")
    assert "duckling `etl.rw`" in text and "ran for 1205 s on Jumbo, finished 15:30 UTC" in text
    gone = alerts.decide([], prev, NOW)[0]
    assert "no longer reported" in alerts.slack_text(gone, NOW)
    assert alerts.duration(dt.timedelta(minutes=125)) == "2 h 05 min"


def test_headline_counts_what_is_still_firing():
    firing = {"new_status": "warning"}
    ok = {"new_status": "ok"}
    assert alerts.headline([ok]) == "MotherDuck: 1 alert(s) resolved, all clear"
    assert alerts.headline([ok], still_firing=1) == "MotherDuck: 1 alert(s) resolved, 1 still firing"
    assert alerts.headline([firing, ok]) == "MotherDuck: 1 alert(s) firing, 1 resolved"
    assert alerts.headline([firing], still_firing=2) == "MotherDuck: 1 alert(s) firing, 2 other(s) still firing"


def test_slack_text_and_line_formatting():
    c = check(name="p95_latency", warning=10000, critical=30000)
    d = {"check": c, "group": "all", "value": 45000.0, "old_status": "ok", "new_status": "critical",
         "kind": "escalated", "notify": True, "since": NOW, "last_notified_at": None}
    text = alerts.slack_text(d)
    assert ":red_circle:" in text and "45,000 %" in text and "threshold 30,000" in text
    assert alerts.format_line(d).startswith("CRITICAL p95_latency: 45,000 %")
    d_ok = {**d, "new_status": "ok", "kind": "resolved"}
    assert alerts.slack_text(d_ok).startswith(":large_green_circle: *Resolved:*")
