"""Supply planner backend: the database (Store) and the write API (Api).

Used in two places:
  - setup.py, run once per environment as the service account that owns the
    database: Store.ensure_schema creates the planning tables and seeds scenarios;
  - the supply-planner-write Flight (build_flight.py inlines supply_engine.py,
    this file and write_flight.py into one source file): every save the Dive
    makes is one Api.call(op, args, actor).

Layout of the `scenario_planner` database:
  main     categories, regions, baseline_forecast, supply_plan  (static.sql)
           approved_plan  approved DEMAND plans from an upstream demand planner,
                          an optional demand source (empty unless one writes it)
  supply   scenarios, scenario_overrides (grid edits: one row per scenario x
           metric 'supply' | 'demand' x region x category x week), activity_log,
           approved_plan (approved supply plans), erp_orders (outbox for an ERP
           connector), rpc_results
The connection works in `supply` with a search path that falls back to `main`,
so unqualified names below resolve to supply's tables or main's static ones.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
import uuid

import supply_engine as engine

HERE = os.path.dirname(os.path.abspath(__file__))
# Bump to regenerate the static tables on the next setup run.
STATIC_VERSION = "2026-09-25.5"
SCHEMA = "supply"
HORIZON = engine.HORIZON
TRANSITIONS = {("draft", "submitted"), ("submitted", "approved"), ("submitted", "draft"), ("approved", "draft")}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ─────────────────────────────── database ───────────────────────────────

class Store:
    """One MotherDuck connection guarded by a lock, working in the `supply` schema."""

    def __init__(self, token: str, db: str):
        import duckdb

        self.db = db
        self.lock = threading.RLock()
        os.environ["motherduck_token"] = token
        boot = duckdb.connect("md:")
        boot.execute(f'CREATE DATABASE IF NOT EXISTS "{db}"')
        boot.close()
        self.con = duckdb.connect(f"md:{db}")
        self._init_session()

    def _init_session(self):
        # TIMESTAMPTZ values are converted with the session time zone; pin it.
        self.con.execute("SET TimeZone = 'UTC'")
        self.con.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.db}"."{SCHEMA}"')
        self.con.execute(f'USE "{self.db}"."{SCHEMA}"')
        self.con.execute(f"SET search_path = '\"{self.db}\".\"{SCHEMA}\",\"{self.db}\".main'")
        self.base: dict | None = None
        self.categories: list[str] = []
        self.regions: list[str] = []
        self.weeks: list[str] = []

    def q(self, sql: str, params=None) -> list[dict]:
        with self.lock:
            for attempt in range(4):
                try:
                    cur = self.con.execute(sql, params or [])
                    if cur.description is None:
                        return []
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    if attempt < 3 and ("conflict" in msg.lower() or "TransactionContext" in msg):
                        time.sleep(0.2 * (attempt + 1))
                        continue
                    raise

    def one(self, sql: str, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    # ── schema (setup.py) ──
    def ensure_schema(self, log):
        main = f'"{self.db}".main'
        self.q(f"CREATE TABLE IF NOT EXISTS {main}.app_meta (key VARCHAR, value VARCHAR)")
        row = self.one(f"SELECT value FROM {main}.app_meta WHERE key = 'static_version'")
        if not row or row["value"] != STATIC_VERSION:
            t = time.time()
            log(f"generating the static planning tables (version {STATIC_VERSION})…")
            with self.lock:
                self.con.execute(f"USE {main}")
                try:
                    for stmt in _split_sql(open(os.path.join(HERE, "static.sql")).read()):
                        self.con.execute(stmt)
                finally:
                    self.con.execute(f'USE "{self.db}"."{SCHEMA}"')
            self.q(f"DELETE FROM {main}.app_meta WHERE key = 'static_version'")
            self.q(f"INSERT INTO {main}.app_meta VALUES ('static_version', ?)", [STATIC_VERSION])
            log(f"static tables ready in {time.time() - t:.1f}s")
        # Approved demand plans (an upstream demand planner writes these); the Dive
        # offers each one as a demand source.
        self.q(f"""CREATE TABLE IF NOT EXISTS {main}.approved_plan (
            scenario_id VARCHAR, scenario_name VARCHAR, approved_at TIMESTAMPTZ, approved_by VARCHAR,
            week DATE, week_idx INTEGER, region VARCHAR, category VARCHAR,
            demand_units INTEGER, sold_units INTEGER, revenue DOUBLE, gross_margin DOUBLE)""")
        self.q("""CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id VARCHAR, name VARCHAR, description VARCHAR, status VARCHAR,
            is_baseline BOOLEAN, drivers VARCHAR, version INTEGER,
            created_by VARCHAR, created_at TIMESTAMPTZ, updated_by VARCHAR, updated_at TIMESTAMPTZ,
            submitted_by VARCHAR, submitted_at TIMESTAMPTZ, approved_by VARCHAR, approved_at TIMESTAMPTZ)""")
        self.q("""CREATE TABLE IF NOT EXISTS scenario_overrides (
            scenario_id VARCHAR, region VARCHAR, category VARCHAR, week_idx INTEGER,
            units DOUBLE, edited_by VARCHAR, edited_at TIMESTAMPTZ)""")
        # Demand overrides (2026-11): which grid row an override is for. Rows from
        # before it are supply overrides. Name the columns in every INSERT.
        self.q("ALTER TABLE scenario_overrides ADD COLUMN IF NOT EXISTS metric VARCHAR DEFAULT 'supply'")
        self.q("""CREATE TABLE IF NOT EXISTS activity_log (
            ts TIMESTAMPTZ, scenario_id VARCHAR, scenario_name VARCHAR, actor VARCHAR,
            action VARCHAR, detail VARCHAR)""")
        self.q("""CREATE TABLE IF NOT EXISTS approved_plan (
            scenario_id VARCHAR, scenario_name VARCHAR, approved_at TIMESTAMPTZ, approved_by VARCHAR,
            week DATE, week_idx INTEGER, region VARCHAR, category VARCHAR,
            demand_units INTEGER, receipts_units INTEGER, subcontract_units INTEGER, sold_units INTEGER,
            expedited_units INTEGER, drop_ship_units INTEGER, lost_units INTEGER,
            revenue DOUBLE, extra_cost DOUBLE, net_margin DOUBLE)""")
        self.q("""CREATE TABLE IF NOT EXISTS erp_orders (
            batch_id VARCHAR, scenario_id VARCHAR, scenario_name VARCHAR, pushed_at TIMESTAMPTZ, pushed_by VARCHAR,
            week DATE, region VARCHAR, category VARCHAR, order_type VARCHAR, units INTEGER, est_cost DOUBLE, status VARCHAR)""")
        ensure_rpc_results(self)
        self.load_base()
        if not self.one("SELECT 1 AS x FROM scenarios LIMIT 1"):
            self.seed_scenarios("system")

    def load_base(self):
        cats = self.q("SELECT category, unit_price, unit_cost, sort_order FROM categories ORDER BY sort_order")
        regs = self.q("SELECT region FROM regions ORDER BY sort_order")
        weeks = self.q("SELECT DISTINCT week_idx, strftime(week, '%Y-%m-%d') AS week FROM baseline_forecast ORDER BY week_idx")
        base_rows = self.q("SELECT region, category, week_idx, units FROM baseline_forecast")
        supply = self.q("SELECT region, category, weekly_receipts, on_hand_start FROM supply_plan")
        self.categories = [c["category"] for c in cats]
        self.regions = [r["region"] for r in regs]
        self.weeks = [w["week"] for w in weeks]
        demand: dict[str, dict[str, list[int]]] = {"baseline": {}}
        for r in base_rows:
            demand["baseline"].setdefault(f"{r['region']}|{r['category']}", [0] * HORIZON)[r["week_idx"]] = int(r["units"])
        # Approved demand plans (main.approved_plan, not supply's own).
        for r in self.q(f'SELECT scenario_id, region, category, week_idx, demand_units FROM "{self.db}".main.approved_plan'):
            demand.setdefault(r["scenario_id"], {}).setdefault(f"{r['region']}|{r['category']}", [0] * HORIZON)[int(r["week_idx"])] = int(r["demand_units"])
        self.base = {
            "regions": self.regions,
            "categories": [{"category": c["category"], "unit_price": float(c["unit_price"]), "unit_cost": float(c["unit_cost"])} for c in cats],
            "demand": demand,
            "supply": {f"{s['region']}|{s['category']}": {"weekly_receipts": int(s["weekly_receipts"]), "on_hand_start": int(s["on_hand_start"])} for s in supply},
        }

    def seed_scenarios(self, actor: str):
        d0 = engine.default_drivers()
        fast = engine.default_drivers()
        fast["demand_upside_pct"] = 10
        fast["expedite"] = {"speed": "3d", "capacity_pct": 25}
        fast["drop_ship"] = {"enabled": True, "share_pct": 50}
        sub = engine.default_drivers()
        sub["demand_upside_pct"] = 10
        sub["subcontract"] = {"enabled": True, "capacity_pct": 20, "premium_pct": 25}
        sub["expedite"] = {"speed": "5d", "capacity_pct": 15}
        air = engine.default_drivers()
        air["demand_upside_pct"] = 10
        air["expedite"] = {"speed": "1d", "capacity_pct": 25}
        stress = engine.default_drivers()
        stress["demand_upside_pct"] = 10
        seeds = [
            ("baseline", "Baseline", "Planned supply against the baseline forecast, no expediting. Locked.", True, d0, "baseline"),
            (_new_id(), "Demand +10%, no action", "Stress test: 10% more demand than forecast with the current supply plan.", False, stress, "draft"),
            (_new_id(), "Expedite 3-day + drop ship", "Demand +10%: chase shortfalls with 3-day freight, drop ship half of the rest.", False, fast, "draft"),
            (_new_id(), "Subcontract +20%", "Demand +10%: subcontractor adds 20% capacity at a 25% premium; 5-day expediting.", False, sub, "draft"),
            (_new_id(), "Overnight air", "Demand +10%: chase every shortfall with 1-day air freight. Recovers the most demand, but costs more than it earns.", False, air, "draft"),
        ]
        ts = now_utc()
        for sid, name, desc, is_base, drivers, status in seeds:
            self.q("INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                   [sid, name, desc, status, is_base, json.dumps(drivers), actor, ts, actor, ts])
        self.log_activity(None, None, actor, "seed", f"Seeded Baseline and {len(seeds) - 1} supply scenarios")

    def log_activity(self, scenario_id, scenario_name, actor, action, detail):
        self.q("INSERT INTO activity_log VALUES (?, ?, ?, ?, ?, ?)", [now_utc(), scenario_id, scenario_name, actor, action, detail])


def ensure_rpc_results(store: Store):
    """Where the Flight stores each reply for the Dive to read (by request id)."""
    store.q("""CREATE TABLE IF NOT EXISTS rpc_results (
        request_id VARCHAR, ts TIMESTAMPTZ, op VARCHAR, actor VARCHAR,
        ok BOOLEAN, status INTEGER, error VARCHAR, result VARCHAR)""")


def _split_sql(sql: str) -> list[str]:
    # static.sql has no semicolons inside literals, so a line-comment strip + split is enough.
    no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


# ─────────────────────────────── RPC ───────────────────────────────

class Api:
    """Validated writes: optimistic versions, an activity log row per save, and
    the supply plan recomputed server-side (supply_engine.py) on approval."""

    def __init__(self, store: Store):
        self.s = store

    def call(self, op: str, args: dict, actor: str):
        fn = getattr(self, "op_" + op, None)
        if not re.fullmatch(r"[a-z_]{2,40}", op or "") or fn is None:
            raise ApiError(400, f"unknown op {op!r}")
        if not isinstance(args, dict):
            raise ApiError(400, "args must be an object")
        return fn(args, actor)

    # ── reads ──
    def scenario_rows(self):
        rows = self.s.q("SELECT * FROM scenarios ORDER BY is_baseline DESC, created_at")
        ov = self.s.q("SELECT scenario_id, metric, region, category, week_idx, units, edited_by, edited_at FROM scenario_overrides")
        by: dict[str, list] = {}
        for o in ov:
            by.setdefault(o["scenario_id"], []).append(
                {"metric": o["metric"], "region": o["region"], "category": o["category"], "week_idx": int(o["week_idx"]),
                 "units": float(o["units"]), "edited_by": o["edited_by"], "edited_at": _iso(o["edited_at"])})
        return [{
            **{k: _iso(v) for k, v in r.items() if k != "drivers"},
            "drivers": engine.normalize_drivers(json.loads(r["drivers"] or "{}")),
            "overrides": by.get(r["scenario_id"], []),
        } for r in rows]

    def _get(self, scenario_id) -> dict:
        if not isinstance(scenario_id, str) or not re.fullmatch(r"[a-z0-9]{1,40}", scenario_id):
            raise ApiError(400, "bad scenario_id")
        row = self.s.one("SELECT * FROM scenarios WHERE scenario_id = ?", [scenario_id])
        if not row:
            raise ApiError(404, "scenario not found")
        return row

    def _editable(self, row, version=None):
        if row["is_baseline"]:
            raise ApiError(403, "The Baseline scenario is locked. Create a scenario from it instead.")
        if row["status"] != "draft":
            raise ApiError(409, f"Scenario is {row['status']}; move it back to draft to edit.")
        if version is not None and int(version) != int(row["version"]):
            raise ApiError(409, "Someone else changed this scenario. Reloaded the latest version.")

    def _bump(self, scenario_id, actor):
        self.s.q("UPDATE scenarios SET version = version + 1, updated_by = ?, updated_at = ? WHERE scenario_id = ?",
                 [actor, now_utc(), scenario_id])

    def _one_scenario(self, scenario_id):
        return next(x for x in self.scenario_rows() if x["scenario_id"] == scenario_id)

    # ── writes ──
    def op_create_scenario(self, args, actor):
        name = _clean_text(args.get("name"), 60) or "Untitled scenario"
        desc = _clean_text(args.get("description"), 280)
        src = self._get(args.get("based_on") or "baseline")
        sid = _new_id()
        ts = now_utc()
        self.s.q("INSERT INTO scenarios VALUES (?, ?, ?, 'draft', false, ?, 1, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                 [sid, name, desc, src["drivers"], actor, ts, actor, ts])
        self.s.q("""INSERT INTO scenario_overrides (scenario_id, metric, region, category, week_idx, units, edited_by, edited_at)
                    SELECT ?, metric, region, category, week_idx, units, edited_by, edited_at
                    FROM scenario_overrides WHERE scenario_id = ?""", [sid, src["scenario_id"]])
        self.s.log_activity(sid, name, actor, "create", f"Created from “{src['name']}”")
        return self._one_scenario(sid)

    def op_update_scenario(self, args, actor):
        row = self._get(args.get("scenario_id"))
        self._editable(row, args.get("version"))
        changes = []
        if "drivers" in args:
            drivers = _validate_drivers(args["drivers"])
            old = engine.normalize_drivers(json.loads(row["drivers"] or "{}"))
            self.s.q("UPDATE scenarios SET drivers = ? WHERE scenario_id = ?", [json.dumps(drivers), row["scenario_id"]])
            changes.append(_describe_driver_change(old, drivers))
        if "name" in args or "description" in args:
            name = _clean_text(args.get("name", row["name"]), 60) or row["name"]
            desc = _clean_text(args.get("description", row["description"]), 280)
            self.s.q("UPDATE scenarios SET name = ?, description = ? WHERE scenario_id = ?", [name, desc, row["scenario_id"]])
            changes.append("Renamed" if name != row["name"] else "Edited description")
        self._bump(row["scenario_id"], actor)
        detail = "; ".join(c for c in changes if c) or "Saved"
        self.s.log_activity(row["scenario_id"], row["name"], actor, "drivers", detail)
        return self._one_scenario(row["scenario_id"])

    def op_set_overrides(self, args, actor):
        """Grid overrides: units per metric x region x category x week, where metric is
        'supply' (planned receipts) or 'demand' (that week's demand); null clears one."""
        row = self._get(args.get("scenario_id"))
        self._editable(row)
        changes = args.get("changes")
        if not isinstance(changes, list) or not (0 < len(changes) <= HORIZON * 30):
            raise ApiError(400, "changes must be a non-empty list")
        cells = []
        for c in changes:
            if not isinstance(c, dict):
                raise ApiError(400, "each change must be an object")
            # Clients from before demand overrides send no metric.
            metric, region, cat, w, units = c.get("metric", "supply"), c.get("region"), c.get("category"), c.get("week_idx"), c.get("units")
            if (metric not in engine.OVERRIDE_METRICS or region not in self.s.regions or cat not in self.s.categories
                    or not isinstance(w, int) or isinstance(w, bool) or not 0 <= w < HORIZON):
                raise ApiError(400, f"bad cell {metric!r}/{region!r}/{cat!r}/{w!r}")
            if units is not None and (not isinstance(units, (int, float)) or isinstance(units, bool) or not 0 <= units <= 1_000_000):
                raise ApiError(400, "units must be a number between 0 and 1,000,000")
            cells.append((metric, region, cat, w, units))
        ts = now_utc()
        sid = row["scenario_id"]
        set_n, clear_n = dict.fromkeys(engine.OVERRIDE_METRICS, 0), dict.fromkeys(engine.OVERRIDE_METRICS, 0)
        for metric, region, cat, w, units in cells:
            # (scenario, metric, region, category, week) is unique: replace, never append.
            self.s.q("DELETE FROM scenario_overrides WHERE scenario_id = ? AND metric = ? AND region = ? AND category = ? AND week_idx = ?",
                     [sid, metric, region, cat, w])
            if units is None:
                clear_n[metric] += 1
                continue
            self.s.q("""INSERT INTO scenario_overrides (scenario_id, metric, region, category, week_idx, units, edited_by, edited_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", [sid, metric, region, cat, w, float(units), actor, ts])
            set_n[metric] += 1
        self._bump(sid, actor)
        _, region, cat, w, _ = cells[0]
        where = f" ({region} · {cat} · wk of {self.s.weeks[w]})" if len(cells) == 1 else ""
        self.s.log_activity(sid, row["name"], actor, "override", _describe_overrides(set_n, clear_n) + where)
        return self._one_scenario(sid)

    def op_set_status(self, args, actor):
        row = self._get(args.get("scenario_id"))
        if row["is_baseline"]:
            raise ApiError(403, "The Baseline scenario is locked.")
        target = args.get("status")
        if (row["status"], target) not in TRANSITIONS:
            raise ApiError(409, f"Cannot move a {row['status']} scenario to {target}.")
        if args.get("version") is not None and int(args["version"]) != int(row["version"]):
            raise ApiError(409, "Someone else changed this scenario. Reloaded the latest version.")
        ts = now_utc()
        sid = row["scenario_id"]
        if target == "submitted":
            self.s.q("UPDATE scenarios SET status = 'submitted', submitted_by = ?, submitted_at = ? WHERE scenario_id = ?", [actor, ts, sid])
            detail = "Submitted for approval"
        elif target == "approved":
            detail = self._approve(row, actor, ts)
        else:
            self.s.q("UPDATE scenarios SET status = 'draft' WHERE scenario_id = ?", [sid])
            detail = "Sent back to draft" if row["status"] == "submitted" else "Reopened for editing"
        note = _clean_text(args.get("note"), 200)
        self._bump(sid, actor)
        self.s.log_activity(sid, row["name"], actor, target, detail + (f" — “{note}”" if note else ""))
        return self._one_scenario(sid)

    def _approve(self, row, actor, ts) -> str:
        """Recompute the supply plan from the stored drivers + supply and demand overrides
        (never from numbers sent by the browser) and write it to supply.approved_plan."""
        sid = row["scenario_id"]
        drivers = engine.normalize_drivers(json.loads(row["drivers"] or "{}"))
        ov = self.s.q("SELECT metric, region, category, week_idx, units FROM scenario_overrides WHERE scenario_id = ?", [sid])
        overrides = {engine.override_key(o["metric"], o["region"], o["category"], int(o["week_idx"])): float(o["units"]) for o in ov}
        plan = engine.compute_plan(self.s.base, drivers, overrides)
        rows = []
        for c in plan["cells"]:
            for w in range(HORIZON):
                met = c["from_stock"][w] + c["expedited"][w] + c["drop_ship"][w]
                rows.append({
                    "scenario_id": sid, "scenario_name": row["name"], "approved_at": ts, "approved_by": actor,
                    "week": dt.date.fromisoformat(self.s.weeks[w]), "week_idx": w, "region": c["region"], "category": c["category"],
                    "demand_units": int(c["demand"][w]), "receipts_units": int(c["receipts"][w]), "subcontract_units": int(c["subcontract"][w]),
                    "sold_units": int(met), "expedited_units": int(c["expedited"][w]), "drop_ship_units": int(c["drop_ship"][w]),
                    "lost_units": int(c["lost"][w]), "revenue": float(c["revenue"][w]), "extra_cost": float(c["extra_cost"][w]),
                    "net_margin": float(c["net_margin"][w]),
                })
        self.s.q("DELETE FROM approved_plan WHERE scenario_id = ?", [sid])
        self.s.q("INSERT INTO approved_plan SELECT unnest(?, recursive := true)", [rows])
        self.s.q("UPDATE scenarios SET status = 'approved', approved_by = ?, approved_at = ? WHERE scenario_id = ?", [actor, ts, sid])
        k = plan["kpis"]
        return (f"Approved — wrote {len(rows)} rows to approved_plan (fill rate {k['fill_rate'] * 100:.1f}%, "
                f"extra supply cost ${k['extra_cost'] / 1e3:,.1f}K)")

    def op_push_to_erp(self, args, actor):
        """Queue the approved plan's supply actions (expedites, drop ships, subcontract
        orders) in erp_orders, the outbox an ERP connector picks up. Re-pushing a
        scenario supersedes its earlier queued lines."""
        row = self._get(args.get("scenario_id"))
        if row["status"] != "approved":
            raise ApiError(409, "Approve the scenario before pushing it to the ERP.")
        drivers = engine.normalize_drivers(json.loads(row["drivers"] or "{}"))
        speed = drivers["expedite"]["speed"]
        sid = row["scenario_id"]
        ts = now_utc()
        batch = _new_id()
        self.s.q("UPDATE erp_orders SET status = 'superseded' WHERE scenario_id = ? AND status = 'queued'", [sid])
        self.s.q(f"""INSERT INTO erp_orders
            SELECT ? AS batch_id, scenario_id, scenario_name, ? AS pushed_at, ? AS pushed_by, week, region, category,
                   t.order_type, t.units, t.est_cost, 'queued' AS status
            FROM approved_plan a, LATERAL (VALUES
                ('expedite_{speed}', a.expedited_units, a.expedited_units * {engine.SPEEDS[speed]['cost']}),
                ('drop_ship', a.drop_ship_units, a.drop_ship_units * {engine.DROP_SHIP['cost']}),
                ('subcontract', a.subcontract_units, NULL)) t(order_type, units, est_cost)
            WHERE a.scenario_id = ? AND t.units > 0""", [batch, ts, actor, sid])
        tot = self.s.one("SELECT count(*) AS lines, coalesce(sum(units), 0) AS units, coalesce(sum(est_cost), 0) AS cost FROM erp_orders WHERE batch_id = ?", [batch])
        self.s.log_activity(sid, row["name"], actor, "erp", f"Pushed {tot['lines']} order lines ({int(tot['units']):,} units) to the ERP outbox")
        return {"batch_id": batch, "lines": int(tot["lines"]), "units": int(tot["units"]), "est_cost": float(tot["cost"]),
                "scenario": self._one_scenario(sid)}

    def op_delete_scenario(self, args, actor):
        row = self._get(args.get("scenario_id"))
        if row["is_baseline"]:
            raise ApiError(403, "The Baseline scenario cannot be deleted.")
        if row["status"] == "approved":
            raise ApiError(409, "Reopen an approved scenario before deleting it.")
        self.s.q("DELETE FROM scenario_overrides WHERE scenario_id = ?", [row["scenario_id"]])
        self.s.q("DELETE FROM scenarios WHERE scenario_id = ?", [row["scenario_id"]])
        self.s.log_activity(row["scenario_id"], row["name"], actor, "delete", "Deleted scenario")
        return {"deleted": row["scenario_id"]}


def _clean_text(v, n):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ApiError(400, "text fields must be strings")
    return re.sub(r"[\x00-\x1f\x7f]", " ", v).strip()[:n]


def _describe_overrides(set_n: dict, clear_n: dict) -> str:
    """{"demand": 2, "supply": 1}, {"demand": 1, "supply": 0} ->
    "Overrode 2 demand cells and 1 supply cell, cleared 1 demand cell"."""
    def cells(n: dict) -> str:
        return " and ".join(f"{k} {m} cell{'s' if k != 1 else ''}" for m, k in n.items() if k)

    s = ", ".join(p for p in (f"overrode {cells(set_n)}" if any(set_n.values()) else "",
                              f"cleared {cells(clear_n)}" if any(clear_n.values()) else "") if p)
    return s[:1].upper() + s[1:]


def _validate_drivers(raw) -> dict:
    if not isinstance(raw, dict):
        raise ApiError(400, "drivers must be an object")
    d = engine.normalize_drivers(raw)

    def rng(name, v, lo, hi):
        if not lo <= v <= hi:
            raise ApiError(400, f"{name} must be between {lo} and {hi}")

    rng("Demand vs forecast %", d["demand_upside_pct"], -30, 60)
    rng("Expedite capacity %", d["expedite"]["capacity_pct"], 0, 100)
    rng("Drop ship share %", d["drop_ship"]["share_pct"], 0, 100)
    s = d["subcontract"]
    rng("Subcontract capacity %", s["capacity_pct"], 0, 100)
    rng("Subcontract premium %", s["premium_pct"], 0, 200)
    return d


def _describe_driver_change(old: dict, new: dict) -> str:
    parts = []
    if old["demand_source"] != new["demand_source"]:
        parts.append(f"demand from {new['demand_source']}")
    if old["demand_upside_pct"] != new["demand_upside_pct"]:
        parts.append(f"demand {old['demand_upside_pct']:+g}% → {new['demand_upside_pct']:+g}%")
    if old["expedite"] != new["expedite"]:
        e = new["expedite"]
        parts.append("expediting off" if e["speed"] == "none" else
                     f"expedite {engine.SPEEDS[e['speed']]['label']} up to {e['capacity_pct']:g}% of weekly supply")
    if old["drop_ship"] != new["drop_ship"]:
        ds = new["drop_ship"]
        parts.append(f"drop ship {ds['share_pct']:g}% of the rest" if ds["enabled"] else "drop ship off")
    if old["subcontract"] != new["subcontract"]:
        s = new["subcontract"]
        parts.append(f"subcontract +{s['capacity_pct']:g}% at +{s['premium_pct']:g}% cost" if s["enabled"] else "subcontract off")
    if bool(old.get("auto_optimize")) != bool(new.get("auto_optimize")):
        parts.append("auto-optimize on" if new.get("auto_optimize") else "auto-optimize off")
    return "; ".join(parts) or "Saved"
