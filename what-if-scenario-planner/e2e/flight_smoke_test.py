#!/usr/bin/env python3
"""Runs the Flight's backend (flight/planner.py) end to end on a local DuckDB
file, no MotherDuck needed: setup (static tables + seeds), then every op the
Dive sends, the way the Flight would run them.

  uv run --with duckdb==1.5.6 --with pytz python e2e/flight_smoke_test.py
"""
import json
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flight"))
import planner  # noqa: E402


class LocalStore(planner.Store):
    def __init__(self, path: str):
        import duckdb

        self.db = "scenario_planner"
        self.lock = threading.RLock()
        self.con = duckdb.connect(path)
        self._init_session()


def check(cond, msg):
    if not cond:
        raise SystemExit(f"FAIL: {msg}")
    print(f"ok  {msg}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(os.path.join(tmp, "scenario_planner.duckdb"))
        store.ensure_schema(lambda m: print(f"    {m}"))
        api = planner.Api(store)
        call = lambda op, args: api.call(op, json.loads(json.dumps(args)), "smoke")  # noqa: E731
        names = [s["name"] for s in api.scenario_rows()]
        check(names[0] == "Baseline" and len(names) == 5, f"seeded {names}")
        check(store.one('SELECT count(*) AS n FROM "scenario_planner".main.baseline_forecast')["n"] == 26 * 24, "26 weeks x 24 region-categories forecast")
        store.ensure_schema(lambda m: None)
        cols = [r["column_name"] for r in store.q("SELECT column_name FROM duckdb_columns() WHERE schema_name = 'supply' AND table_name = 'scenario_overrides'")]
        check(cols.count("metric") == 1, "ensure_schema is idempotent and adds scenario_overrides.metric once")

        s = call("create_scenario", {"name": "Smoke test", "based_on": "baseline"})
        sid = s["scenario_id"]
        drivers = {**s["drivers"], "demand_upside_pct": 10, "expedite": {"speed": "1d", "capacity_pct": 25}, "auto_optimize": True}
        s = call("update_scenario", {"scenario_id": sid, "version": s["version"], "drivers": drivers})
        check(s["drivers"]["expedite"]["speed"] == "1d" and s["drivers"].get("auto_optimize") is True, "update_scenario saved the drivers (and auto-optimize)")
        detail = store.one("SELECT detail FROM activity_log WHERE action = 'drivers' ORDER BY ts DESC LIMIT 1")["detail"]
        check("auto-optimize on" in detail, f"activity notes auto-optimize: {detail!r}")
        s = call("set_overrides", {"scenario_id": sid, "changes": [
            {"region": "West", "category": "Outerwear", "week_idx": 3, "units": 2500},  # no metric: an old client's supply edit
            {"metric": "supply", "region": "West", "category": "Denim", "week_idx": 4, "units": 0}]})
        check(len(s["overrides"]) == 2 and s["overrides"][0]["edited_by"] == "smoke" and s["overrides"][0]["edited_at"]
              and {o["metric"] for o in s["overrides"]} == {"supply"}, "set_overrides stored who and when (metric defaults to supply)")
        s = call("set_overrides", {"scenario_id": sid, "changes": [{"region": "West", "category": "Denim", "week_idx": 4, "units": None}]})
        check(len(s["overrides"]) == 1, "a null clears an override")
        # Demand overrides: same cell and week as a supply override, plus one to clear later.
        s = call("set_overrides", {"scenario_id": sid, "changes": [
            {"metric": "demand", "region": "West", "category": "Outerwear", "week_idx": 3, "units": 9000},
            {"metric": "demand", "region": "Central", "category": "Denim", "week_idx": 0, "units": 1234}]})
        ov = {(o["metric"], o["region"], o["category"], o["week_idx"]): o["units"] for o in s["overrides"]}
        check(ov == {("supply", "West", "Outerwear", 3): 2500, ("demand", "West", "Outerwear", 3): 9000, ("demand", "Central", "Denim", 0): 1234},
              "demand and supply overrides of the same week are kept apart")
        s = call("set_overrides", {"scenario_id": sid, "changes": [{"metric": "demand", "region": "West", "category": "Outerwear", "week_idx": 3, "units": 8000}]})
        dup = store.one("SELECT count(*) AS n FROM (SELECT 1 FROM scenario_overrides GROUP BY scenario_id, metric, region, category, week_idx HAVING count(*) > 1)")["n"]
        check(dup == 0 and len(s["overrides"]) == 3, "re-editing a demand override replaces it (one row per scenario x metric x cell x week)")
        detail = store.one("SELECT detail FROM activity_log WHERE action = 'override' ORDER BY ts DESC LIMIT 1")["detail"]
        check(detail.startswith("Overrode 1 demand cell (West · Outerwear · wk of "), f"activity says which metric: {detail!r}")
        for bad in ({"metric": "inventory", "region": "West", "category": "Denim", "week_idx": 0, "units": 1},
                    {"region": "Mars", "category": "Denim", "week_idx": 0, "units": 1}):
            try:
                call("set_overrides", {"scenario_id": sid, "changes": [bad]})
                check(False, "a bad cell is rejected")
            except planner.ApiError as e:
                check(e.status == 400, f"a bad cell is rejected ({e.message})")

        copy = call("create_scenario", {"name": "Smoke copy", "based_on": sid})
        check(sorted((o["metric"], o["region"], o["category"], o["week_idx"], o["units"]) for o in copy["overrides"])
              == sorted((o["metric"], o["region"], o["category"], o["week_idx"], o["units"]) for o in s["overrides"]),
              "duplicating copies the supply and the demand overrides")
        call("delete_scenario", {"scenario_id": copy["scenario_id"]})
        s = call("set_overrides", {"scenario_id": sid, "changes": [{"metric": "demand", "region": "Central", "category": "Denim", "week_idx": 0, "units": None}]})
        check(sorted(o["metric"] for o in s["overrides"]) == ["demand", "supply"], "a null clears a demand override and leaves the rest")

        s = call("set_status", {"scenario_id": sid, "status": "submitted", "version": s["version"]})
        check(s["status"] == "submitted", "submitted")
        s = call("set_status", {"scenario_id": sid, "status": "approved", "version": s["version"]})
        rows = store.q("SELECT count(*) AS n, sum(extra_cost) AS cost, sum(net_margin) AS net FROM approved_plan WHERE scenario_id = ?", [sid])[0]
        check(s["status"] == "approved" and rows["n"] == 26 * 24, f"approved: {rows['n']} plan rows, extra cost ${rows['cost']:,.0f}")
        cell = lambda region, cat, w: store.one("SELECT demand_units, receipts_units FROM approved_plan WHERE scenario_id = ? AND region = ? AND category = ? AND week_idx = ?", [sid, region, cat, w])  # noqa: E731
        fc = store.one("SELECT units FROM baseline_forecast WHERE region = 'Central' AND category = 'Denim' AND week_idx = 0")["units"]
        check(cell("West", "Outerwear", 3) == {"demand_units": 8000, "receipts_units": 2500}, "approved_plan uses the demand and supply overrides")
        check(cell("Central", "Denim", 0)["demand_units"] == planner.engine.js_round(fc * 1.1), "a cleared demand override is back to forecast x (1 + upside)")
        r = call("push_to_erp", {"scenario_id": sid})
        check(r["lines"] > 0 and r["est_cost"] > 0, f"push_to_erp queued {r['lines']} lines (${r['est_cost']:,.0f}, 1-day at ${planner.engine.SPEEDS['1d']['cost']}/unit)")

        s = call("set_status", {"scenario_id": sid, "status": "draft", "version": s["version"]})
        call("delete_scenario", {"scenario_id": sid})
        check(sid not in [x["scenario_id"] for x in api.scenario_rows()], "reopened and deleted")
        acts = [a["action"] for a in store.q("SELECT action FROM activity_log ORDER BY ts")]
        check(acts == ["seed", "create", "drivers", "override", "override", "override", "override", "create", "delete", "override",
                       "submitted", "approved", "erp", "draft", "delete"], f"activity log {acts}")
        store.con.close()
    print("PASS: the Flight backend handles every op on a fresh database")


if __name__ == "__main__":
    main()
