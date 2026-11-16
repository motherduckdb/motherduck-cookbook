"""Python port of dive/src/engine.ts (keep the two in lock-step).

The supply write Flight recomputes the plan with this on approval, so
supply.approved_plan never depends on numbers sent by the browser.
e2e/engine-parity.test.mjs checks both engines agree.
"""
from __future__ import annotations

import math

HORIZON = 26
SPEEDS = {
    "none": {"label": "No expediting", "days": None, "cost": 0.0, "recapture": 0.0},
    "5d": {"label": "5-day", "days": 5, "cost": 1.5, "recapture": 0.35},
    "3d": {"label": "3-day", "days": 3, "cost": 3.25, "recapture": 0.55},
    # Overnight air: costs more than most categories earn per unit, so on its own it is not worth it.
    "1d": {"label": "1-day", "days": 1, "cost": 55.0, "recapture": 0.85},
}
SPEED_ORDER = ["none", "5d", "3d", "1d"]
DROP_SHIP = {"cost": 14.0, "recapture": 0.92}
HOLDING_PCT_WEEK = 0.004
OVERRIDE_METRICS = ("demand", "supply")  # the grid rows a planner can override, in grid order


def js_round(x: float) -> int:
    """JavaScript Math.round: halves round toward +infinity."""
    return math.floor(x + 0.5)


def default_drivers(_categories=None) -> dict:
    return {
        "demand_source": "baseline",
        "demand_upside_pct": 0,
        "expedite": {"speed": "none", "capacity_pct": 20},
        "drop_ship": {"enabled": False, "share_pct": 50},
        "subcontract": {"enabled": False, "capacity_pct": 15, "premium_pct": 25},  # from the current week on
    }


def _num(v, fallback):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else fallback


def normalize_drivers(raw, _categories=None) -> dict:
    d = default_drivers()
    if not isinstance(raw, dict):
        return d
    src = raw.get("demand_source")
    d["demand_source"] = src if isinstance(src, str) and src else "baseline"
    d["demand_upside_pct"] = _num(raw.get("demand_upside_pct"), 0)
    e = raw.get("expedite") if isinstance(raw.get("expedite"), dict) else {}
    speed = "3d" if e.get("speed") == "2d" else e.get("speed")  # "2d" was an option once; read it as 3-day
    d["expedite"] = {"speed": speed if speed in SPEED_ORDER else "none", "capacity_pct": _num(e.get("capacity_pct"), 20)}
    ds = raw.get("drop_ship") if isinstance(raw.get("drop_ship"), dict) else {}
    d["drop_ship"] = {"enabled": bool(ds.get("enabled")), "share_pct": _num(ds.get("share_pct"), 50)}
    s = raw.get("subcontract") if isinstance(raw.get("subcontract"), dict) else {}
    d["subcontract"] = {
        "enabled": bool(s.get("enabled")), "capacity_pct": _num(s.get("capacity_pct"), 15),
        "premium_pct": _num(s.get("premium_pct"), 25),
    }
    if raw.get("auto_optimize") is True:  # the Dive's optimizer keeps the levers at the best net margin
        d["auto_optimize"] = True
    return d


def levers_off(d: dict) -> dict:
    return {**d, "expedite": {**d["expedite"], "speed": "none"}, "drop_ship": {**d["drop_ship"], "enabled": False},
            "subcontract": {**d["subcontract"], "enabled": False}}


def override_key(metric: str, region: str, category: str, week: int) -> str:
    """Key of one overridden week in compute_plan's overrides map (engine.ts overrideKey)."""
    return f"{metric}|{region}|{category}|{week}"


def compute_plan(base: dict, drivers: dict, overrides: dict[str, float]) -> dict:
    """base: {regions, categories:[{category, unit_price, unit_cost}], demand:{source: {'region|category': [26]}},
    supply:{'region|category': {weekly_receipts, on_hand_start}}}
    overrides: override_key(metric, region, category, week) -> units. A demand override replaces
    source x (1 + upside) for that week, a supply override its planned receipts."""
    source = drivers["demand_source"] if drivers["demand_source"] in base["demand"] else "baseline"
    upside = 1 + drivers["demand_upside_pct"] / 100
    speed = SPEEDS[drivers["expedite"]["speed"]]
    ds = drivers["drop_ship"]
    sub = drivers["subcontract"]
    k = {key: 0 for key in ("demand_units", "met_units", "lost_units", "revenue", "lost_revenue", "expedited_units",
                            "drop_ship_units", "subcontract_units", "expedite_cost", "drop_ship_cost", "subcontract_premium",
                            "extra_cost", "holding_cost", "net_margin", "end_inventory_units", "end_inventory_value")}
    cells = []
    for region in base["regions"]:
        for cat in base["categories"]:
            name = cat["category"]
            key = f"{region}|{name}"
            fc = base["demand"].get(source, {}).get(key, [0] * HORIZON)
            sp = base["supply"].get(key, {"weekly_receipts": 0, "on_hand_start": 0})
            inv = sp["on_hand_start"]
            c = {"region": region, "category": name, "start_inventory": inv, "overridden": {"supply": [], "demand": []},
                 **{f: [] for f in ("demand", "receipts", "subcontract", "from_stock", "expedited", "drop_ship", "lost",
                                    "revenue", "extra_cost", "net_margin", "inventory")}}
            for w in range(HORIZON):
                d_key = override_key("demand", region, name, w)
                s_key = override_key("supply", region, name, w)
                demand = max(0, js_round(overrides[d_key])) if d_key in overrides else js_round(fc[w] * upside)
                receipts = max(0, js_round(overrides[s_key])) if s_key in overrides else sp["weekly_receipts"]
                sub_units = js_round(sp["weekly_receipts"] * sub["capacity_pct"] / 100) if sub["enabled"] else 0
                available = inv + receipts + sub_units
                from_stock = min(demand, available)
                short = demand - from_stock
                exp_cap = 0 if speed["days"] is None else js_round(sp["weekly_receipts"] * drivers["expedite"]["capacity_pct"] / 100)
                expedited = min(js_round(short * speed["recapture"]), exp_cap)
                rest = short - expedited
                drop_ship = js_round(rest * (ds["share_pct"] / 100) * DROP_SHIP["recapture"]) if ds["enabled"] else 0
                lost = rest - drop_ship
                inv = available - from_stock
                met = from_stock + expedited + drop_ship
                revenue = met * cat["unit_price"]
                exp_cost = expedited * speed["cost"]
                ds_cost = drop_ship * DROP_SHIP["cost"]
                sub_premium = sub_units * cat["unit_cost"] * sub["premium_pct"] / 100
                holding = inv * cat["unit_cost"] * HOLDING_PCT_WEEK
                extra = exp_cost + ds_cost + sub_premium
                net = revenue - met * cat["unit_cost"] - extra - holding
                for f, v in (("demand", demand), ("receipts", receipts), ("subcontract", sub_units), ("from_stock", from_stock),
                             ("expedited", expedited), ("drop_ship", drop_ship), ("lost", lost), ("revenue", revenue),
                             ("extra_cost", extra), ("net_margin", net), ("inventory", inv)):
                    c[f].append(v)
                c["overridden"]["demand"].append(d_key in overrides)
                c["overridden"]["supply"].append(s_key in overrides)
                k["demand_units"] += demand
                k["met_units"] += met
                k["lost_units"] += lost
                k["revenue"] += revenue
                k["lost_revenue"] += lost * cat["unit_price"]
                k["expedited_units"] += expedited
                k["drop_ship_units"] += drop_ship
                k["subcontract_units"] += sub_units
                k["expedite_cost"] += exp_cost
                k["drop_ship_cost"] += ds_cost
                k["subcontract_premium"] += sub_premium
                k["extra_cost"] += extra
                k["holding_cost"] += holding
                k["net_margin"] += net
            k["end_inventory_units"] += inv
            k["end_inventory_value"] += inv * cat["unit_cost"]
            cells.append(c)
    k["fill_rate"] = k["met_units"] / k["demand_units"] if k["demand_units"] > 0 else 1
    return {"cells": cells, "kpis": k}
