"""Generative Dive engine (template). Do not edit per domain: edit recipes/<name>/domain.py.

Turns a question, or the domain's daily briefing plan, into a validated Dive spec.

    marts     build the recipe's curated tables (MARTS) from its sources (ATTACH)
    plan      MODE=ask: prompt() picks datasets, measures, dimensions and components from
              CATALOG (strict JSON schema whose enums are the catalog ids).
              MODE=briefing: the recipe's fixed BRIEFING_PLAN.
    compile   validate the plan against CATALOG and compile it to SQL; run it
    probe     prompt() proposes up to ANALYST_PROBES follow-up queries that test why
              something moved; they compile like any block (catalog ids only)
    narrate   prompt() writes headline, insights, captions, block insights and
              annotations, with memory of earlier briefings, viewer feedback and the
              recipe's Guides; every number is checked against the data
    publish   one JSON spec row in "<DB>".main.dive_specs; the Dive renders it
    learn     logs the run (gen_runs, llm_calls, insights) and, after a briefing,
              updates the recipe's memory Guide

The spec holds data and presentation only, never SQL the Dive would execute.

Config (strings; the Dive sends them through useRunFlight):
    MODE        briefing | ask | investigate | refresh | feedback
    PROMPT      the question, for MODE=ask and MODE=investigate
    PARENT      MODE=investigate: id of the spec whose insight is being investigated
    REQUEST_ID  opaque id from the Dive, so it can open the spec it asked for
    AS_OF       analysis date; empty means the recipe's AS_OF_DEFAULT
    MODEL       prompt() model
    FEEDBACK    JSON list of viewer events from the Dive (any mode records them;
                MODE=feedback only records them)
"""

import hashlib

import datetime as dt
import json
import os
import re
import time
import traceback
import uuid

import duckdb

SPEC_VERSION = 1

# @domain: build.py inserts recipes/<name>/domain.py here
# @domain-end

LABELS = globals().get("LABELS", {})
# Components the planner may choose. build.py inlines COMPONENTS from
# dive-components/components.json (entries with a plan_rule); a recipe can add its
# own in domain.py: CUSTOM_COMPONENTS = {"funnel": {"plan_rule": "...", "compile": fn}}
# where fn(compiler, block) returns the block's data (see SKILL.md).
COMPONENTS = globals().get("COMPONENTS") or [
    {"type": "kpi", "plan_rule": "kpi: 2-4 headline measures with a comparison."},
    {"type": "line", "plan_rule": "line: trends over time."},
    {"type": "bar", "plan_rule": "bar: a measure across a categorical dimension."},
    {"type": "table", "plan_rule": "table: rankings or several measures per row."},
]
CUSTOM_COMPONENTS = globals().get("CUSTOM_COMPONENTS", {})
ACRONYMS = globals().get("ACRONYMS", {"gmv": "GMV", "avg": "Avg", "pct": "%"})
PLANNER_HINTS = globals().get("PLANNER_HINTS", "")


def mark(node, state, **fields):
    extra = " ".join(f'{k}="{v}"' for k, v in fields.items())
    print(f"@flight-run {node} {state} {extra}".rstrip(), flush=True)


# --------------------------------------------------------------------------
# Marts: the curated, documented layer the generator is allowed to choose from
# --------------------------------------------------------------------------

SPECS_DDL = f"""
CREATE TABLE IF NOT EXISTS "{DB}".main.dive_specs (
    id VARCHAR, request_id VARCHAR, kind VARCHAR, prompt VARCHAR, title VARCHAR,
    created_at TIMESTAMP, as_of DATE, model VARCHAR, status VARCHAR, error VARCHAR,
    duration_s DOUBLE, spec VARCHAR
)"""


def build_marts(con, force, refresh_ok=True):
    """Build missing marts. MARTS_REFRESH="always" rebuilds them only on runs that may
    refresh (briefing, refresh): questions read the marts the last briefing built, so
    they stay fast and two viewers asking at once don't write the same table."""
    existing = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = ?", [DB]
        ).fetchall()
    }
    built = 0
    for name, sql in MARTS.items():
        if force or (MARTS_REFRESH == "always" and refresh_ok) or name not in existing:
            con.execute(f'CREATE OR REPLACE TABLE "{DB}".main.{name} AS {sql}')
            built += 1
    return built


# --------------------------------------------------------------------------
# Catalog: the only identifiers the generator can reference
# --------------------------------------------------------------------------
# measure: (sql aggregate over the mart, format, description)

ALL_MEASURES = [f"{d}.{m}" for d, spec in CATALOG.items() for m in spec["measures"]]
ALL_DIMS = sorted({dim for spec in CATALOG.values() for dim in spec["dims"]})
WINDOWS = ["last_7_days", "last_28_days", "last_30_days", "last_90_days", "last_6_months", "last_12_months", "all_time"]
BLOCK_TYPES = [c["type"] for c in COMPONENTS if c.get("plan_rule")] + list(CUSTOM_COMPONENTS)
COMPONENT_RULES = "\n".join(f"- {c['plan_rule']}" for c in COMPONENTS if c.get("plan_rule")) + "".join(f"\n- {v['plan_rule']}" for v in CUSTOM_COMPONENTS.values())
ONE_MEASURE = {"drivers", "heatmap", "sankey", "treemap"}
NEEDS_DIMENSION = {"bar", "table", "sankey", "treemap"}
KEEPS_SERIES_DIMENSION = {"line", "sankey", "treemap"}


def measure_info(qualified):
    ds, m = qualified.split(".", 1)
    sql, fmt, desc = CATALOG[ds]["measures"][m][:3]
    return ds, m, sql, fmt, desc


def good_direction(qualified):
    ds, m = qualified.split(".", 1)
    t = CATALOG[ds]["measures"][m]
    return t[3] if len(t) > 3 else "up"


def catalog_text(con):
    lines = []
    for ds, spec in CATALOG.items():
        lines.append(f"## dataset {ds}: {spec['about']} Time grain: {spec['time']}.")
        for dim, (col, desc) in spec["dims"].items():
            vals = con.execute(
                f'SELECT string_agg(DISTINCT {col}::VARCHAR, \', \') FROM (SELECT {col} FROM "{DB}".main.{spec["table"]} GROUP BY 1 ORDER BY count(*) DESC LIMIT 8)'
            ).fetchone()[0]
            lines.append(f"- dimension {dim}: {desc}. Values include: {vals}")
        for m, t in spec["measures"].items():
            lines.append(f"- measure {ds}.{m} ({t[1]}{', lower is better' if len(t) > 3 and t[3] == 'down' else ''}): {t[2]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Compiler: plan block -> SQL -> rendered block data
# --------------------------------------------------------------------------


def window_bounds(window, as_of, first_day):
    days = {"last_7_days": 7, "last_28_days": 28, "last_30_days": 30, "last_90_days": 90, "last_6_months": 182, "last_12_months": 365}
    if window == "all_time":
        return first_day, as_of
    return as_of - dt.timedelta(days=days[window] - 1), as_of


def shift(bounds, compare):
    start, end = bounds
    if compare == "previous_year":
        return start - dt.timedelta(days=364), end - dt.timedelta(days=364)
    length = (end - start).days + 1
    return start - dt.timedelta(days=length), end - dt.timedelta(days=length)


def where_clause(ds, bounds, filters):
    spec = CATALOG[ds]
    t = spec["time"]
    start, end = bounds
    lag = spec.get("lag_days", 0)
    if lag:
        start, end = start - dt.timedelta(days=lag), end - dt.timedelta(days=lag)
    if t == "month":
        start = start.replace(day=1)
    parts = [f"{t} BETWEEN ?::DATE AND ?::DATE"]
    params = [start.isoformat(), end.isoformat()]
    for f in filters:
        col = spec["dims"][f["dimension"]][0]
        vals = [str(v) for v in f["values"]][:20]
        if vals:
            parts.append(f"{col}::VARCHAR IN ({', '.join('?' for _ in vals)})")
            params.extend(vals)
    return " AND ".join(parts), params


def render_sql(sql, params):
    """Inline params for display only. Never executed."""
    out = sql
    for p in params:
        out = out.replace("?", "'" + str(p).replace("'", "''") + "'", 1)
    return re.sub(r"\s+", " ", out).strip()




def pretty(m):
    """Label for a measure. Accepts 'dataset.measure' or 'measure'."""
    if m in LABELS:
        return LABELS[m]
    ds, short = (m.split(".", 1) + [None])[:2] if "." in m else (None, m)
    if short in LABELS:
        return LABELS[short]
    words = [ACRONYMS.get(w, w) for w in short.split("_")]
    text = " ".join(words)
    text = text[0].upper() + text[1:]
    if ds and sum(short in spec["measures"] for spec in CATALOG.values()) > 1:
        text = f"{CATALOG[ds].get('label', ds.replace('_', ' ').capitalize())} {text[0].lower() + text[1:]}"
    return text


def num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 6)


class Compiler:
    def __init__(self, con, as_of):
        self.con = con
        self.as_of = as_of
        self.first_day = con.execute(f'SELECT min({ANCHOR[1]}) FROM "{DB}".main.{ANCHOR[0]}').fetchone()[0]

    def q(self, sql, params):
        return self.con.execute(sql, params).fetchall()

    def validate(self, b):
        measures = [m for m in b.get("measures", []) if m in ALL_MEASURES]
        if not measures:
            raise ValueError(f"block '{b.get('title')}' has no valid measures")
        if b["type"] != "kpi":
            ds = measures[0].split(".")[0]
            measures = [m for m in measures if m.startswith(ds + ".")]
            b["dataset"] = ds
            dims = CATALOG[ds]["dims"]
            if b.get("dimension") not in dims:
                b["dimension"] = next(iter(dims)) if b["type"] in NEEDS_DIMENSION else ""
            if b.get("series_dimension") not in dims or b["type"] not in KEEPS_SERIES_DIMENSION:
                b["series_dimension"] = ""
            if b["type"] == "sankey" and b["series_dimension"] in ("", b["dimension"]):
                b["series_dimension"] = next((d for d in dims if d != b["dimension"]), "")
                if not b["series_dimension"]:
                    raise ValueError("a flow needs two dimensions")
            if b["type"] in ("sankey", "treemap") and measure_info(measures[0])[3] not in ("count", "currency"):
                raise ValueError(f"{b['type']} needs a count or currency measure")
            b["filters"] = [f for f in b.get("filters", []) if f.get("dimension") in dims]
        else:
            b["filters"] = [f for f in b.get("filters", []) if f.get("dimension") in ALL_DIMS]
        if b["type"] in ("line", "bar"):
            fmt0 = measure_info(measures[0])[3]
            measures = [m for m in measures if measure_info(m)[3] == fmt0]
        b["measures"] = measures[: 5 if b["type"] in ("kpi", "table") else 1 if b["type"] in ONE_MEASURE else 3]
        b["window"] = b.get("window") if b.get("window") in WINDOWS else "last_30_days"
        b["compare"] = b.get("compare") if b.get("compare") in COMPARES else "none"
        if b["type"] == "drivers" and b["compare"] == "none":
            b["compare"] = DEFAULT_COMPARE if DEFAULT_COMPARE != "none" else "previous_period"
        b["limit"] = max(3, min(int(b.get("limit") or 10), 25))
        b["span"] = b.get("span") if b.get("span") in (4, 6, 8, 12) else 6
        return b

    def compile(self, b, idx):
        b = self.validate(b)
        custom = CUSTOM_COMPONENTS.get(b["type"])
        out = custom["compile"](self, b) if custom else getattr(self, f"_{b['type']}")(b)
        out.update(
            id=f"b{idx}",
            type=out.get("type", b["type"]),
            title=b.get("title") or "",
            span=b["span"],
            why=b.get("why", ""),
        )
        return out

    # ---- KPI: one value per measure, a comparison, and a weekly sparkline
    def _kpi(self, b):
        items, sqls = [], []
        bounds = window_bounds(b["window"], self.as_of, self.first_day)
        for qm in b["measures"]:
            ds, m, expr, fmt, desc = measure_info(qm)
            spec = CATALOG[ds]
            filt = [f for f in b["filters"] if f["dimension"] in spec["dims"]]
            w, p = where_clause(ds, bounds, filt)
            sql = f'SELECT {expr} FROM "{DB}".main.{spec["table"]} WHERE {w}'
            value = num(self.q(sql, p)[0][0])
            prior = None
            if b["compare"] != "none":
                w2, p2 = where_clause(ds, shift(bounds, b["compare"]), filt)
                prior = num(self.q(f'SELECT {expr} FROM "{DB}".main.{spec["table"]} WHERE {w2}', p2)[0][0])
            grain = "month" if spec["time"] == "month" else "week"
            spark_start = self.as_of - dt.timedelta(days=364)
            ws, ps = where_clause(ds, (spark_start, self.as_of), filt)
            spark = self.q(
                f'SELECT date_trunc(\'{grain}\', {spec["time"]}) AS t, {expr} FROM "{DB}".main.{spec["table"]} '
                f"WHERE {ws} GROUP BY 1 ORDER BY 1",
                ps,
            )
            spark_vals = [num(v) for _, v in spark]
            spark_x = [str(t_)[:10] for t_, _ in spark]
            if grain == "week" and len(spark_vals) > 2:
                spark_vals, spark_x = spark_vals[1:-1], spark_x[1:-1]  # drop partial weeks at both ends
            items.append(
                {
                    "key": qm,
                    "label": pretty(qm) + (" · " + ", ".join(v for f in filt for v in f["values"]) if filt else ""),
                    "value": value,
                    "prior": prior,
                    "format": fmt,
                    "good": good_direction(qm),
                    "spark": spark_vals,
                    "spark_x": spark_x,
                    "spark_grain": grain,
                }
            )
            sqls.append(render_sql(sql, p))
        return {
            "items": items,
            "compare": b["compare"],
            "window": b["window"],
            "source": {"datasets": sorted({i["key"].split(".")[0] for i in items}), "measures": b["measures"], "window": b["window"], "filters": b["filters"], "compare": b["compare"], "sql": sqls},
        }

    def _series_cols(self, b):
        cols = []
        for qm in b["measures"]:
            _, m, expr, fmt, desc = measure_info(qm)
            cols.append((m, expr, fmt, desc))
        return cols

    # ---- Line: time series of 1-3 measures, or one measure split by a dimension
    def _line(self, b):
        ds = b["dataset"]
        spec = CATALOG[ds]
        t = spec["time"]
        grain = "month" if t == "month" else b.get("time_grain") or "week"
        if grain not in ("day", "week", "month"):
            grain = "week"
        start, end = window_bounds(b["window"], self.as_of, self.first_day)
        if grain == "week":
            start = start + dt.timedelta(days=(7 - start.weekday()) % 7)
        elif grain == "month" and start.day != 1:
            start = (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        w, p = where_clause(ds, (start, end), b["filters"])
        cols = self._series_cols(b)
        bucket = f"date_trunc('{grain}', {t})"
        # Exclude a trailing partial bucket so the last point isn't a false drop
        trim = ""
        if grain != "day":
            trim = f" AND {bucket} + INTERVAL 1 {grain} - INTERVAL 1 day <= ?::DATE"
            p = p + [self.as_of.isoformat()]
        sdim = b.get("series_dimension")
        if sdim:
            m, expr, fmt, desc = cols[0]
            dcol = spec["dims"][sdim][0]
            top = [
                r[0]
                for r in self.q(
                    # Rates rank tiny groups first; pick the lines by volume instead.
                    f'SELECT {dcol}::VARCHAR FROM "{DB}".main.{spec["table"]} WHERE {w} GROUP BY 1 ORDER BY {expr if fmt in ("count", "currency") else "count(*)"} DESC NULLS LAST LIMIT 6',
                    p[: len(p) - (1 if trim else 0)],
                )
            ]
            sql = (
                f"SELECT strftime({bucket}, '%Y-%m-%d') AS period, {dcol}::VARCHAR AS s, {expr} AS v "
                f'FROM "{DB}".main.{spec["table"]} WHERE {w}{trim} GROUP BY ALL ORDER BY 1'
            )
            rows = self.q(sql, p)
            data = {}
            for period, s, v in rows:
                if s in top:
                    data.setdefault(period, {"x": period})[s] = num(v)
            series = [{"key": s, "label": s, "format": fmt} for s in top]
            ylabel = desc
        else:
            sel = ", ".join(f"{expr} AS {m}" for m, expr, _, _ in cols)
            sql = (
                f"SELECT strftime({bucket}, '%Y-%m-%d') AS period, {sel} "
                f'FROM "{DB}".main.{spec["table"]} WHERE {w}{trim} GROUP BY 1 ORDER BY 1'
            )
            rows = self.q(sql, p)
            data = {r[0]: {"x": r[0], **{c[0]: num(v) for c, v in zip(cols, r[1:])}} for r in rows}
            series = [{"key": m, "label": pretty(m), "format": fmt, "good": good_direction(f"{ds}.{m}")} for m, _, fmt, desc in cols]
            ylabel = ""
        return {
            "x": {"key": "x", "kind": "time", "grain": grain},
            "series": series,
            "data": list(data.values()),
            "ylabel": ylabel,
            "source": {"dataset": ds, "measures": b["measures"], "dimension": sdim or "time", "window": b["window"], "filters": b["filters"], "sql": [render_sql(sql, p)]},
        }

    # ---- Bar / table: measures by a categorical dimension, optional comparison
    def _grouped(self, b):
        ds = b["dataset"]
        spec = CATALOG[ds]
        dim = b["dimension"]
        dcol = spec["dims"][dim][0]
        bounds = window_bounds(b["window"], self.as_of, self.first_day)
        w, p = where_clause(ds, bounds, b["filters"])
        cols = self._series_cols(b)
        sel = ", ".join(f"{expr} AS {m}" for m, expr, _, _ in cols)
        order = "ASC" if b.get("sort") == "asc" else "DESC"
        sql = (
            f'SELECT {dcol}::VARCHAR AS label, {sel} FROM "{DB}".main.{spec["table"]} '
            f"WHERE {w} GROUP BY 1 ORDER BY 2 {order} NULLS LAST LIMIT {b['limit']}"
        )
        rows = self.q(sql, p)
        data = [{"x": r[0], **{c[0]: num(v) for c, v in zip(cols, r[1:])}} for r in rows]
        series = [{"key": m, "label": pretty(m), "format": fmt, "good": good_direction(f"{ds}.{m}")} for m, _, fmt, desc in cols]
        sqls = [render_sql(sql, p)]
        if b["compare"] != "none" and data:
            m0, expr0, fmt0, desc0 = cols[0]
            labels = [d["x"] for d in data]
            w2, p2 = where_clause(ds, shift(bounds, b["compare"]), b["filters"])
            sql2 = (
                f'SELECT {dcol}::VARCHAR, {expr0} FROM "{DB}".main.{spec["table"]} WHERE {w2} '
                f"AND {dcol}::VARCHAR IN ({', '.join('?' for _ in labels)}) GROUP BY 1"
            )
            prior = dict(self.q(sql2, p2 + labels))
            label = "Last year" if b["compare"] == "previous_year" else "Previous period"
            for d in data:
                d[f"{m0}_prior"] = num(prior.get(d["x"]))
            series.insert(1, {"key": f"{m0}_prior", "label": f"{label}", "format": fmt0, "muted": True})
            sqls.append(render_sql(sql2, p2 + labels))
        return {
            "x": {"key": "x", "kind": "category", "label": dim.replace("_", " ")},
            "series": series,
            "data": data,
            "source": {"dataset": ds, "measures": b["measures"], "dimension": dim, "window": b["window"], "filters": b["filters"], "compare": b["compare"], "sql": sqls},
        }

    _bar = _grouped
    _table = _grouped

    # ---- Heatmap: a calendar of daily values, or a dimension against weeks
    def _heatmap(self, b):
        ds = b["dataset"]
        spec = CATALOG[ds]
        t = spec["time"]
        _, m, expr, fmt, desc = measure_info(b["measures"][0])
        start, end = window_bounds(b["window"], self.as_of, self.first_day)
        table = f'"{DB}".main.{spec["table"]}'
        dim = b.get("dimension")
        if not dim and t == "day":
            w, p = where_clause(ds, (start, end), b["filters"])
            sql = f"SELECT strftime({t}, '%Y-%m-%d') AS x, {expr} FROM {table} WHERE {w} GROUP BY 1 ORDER BY 1"
            rows = self.q(sql, p)
            return {
                "type": "heatmap",
                "mode": "calendar",
                "x": {"key": "x", "kind": "time", "grain": "day"},
                "series": [{"key": m, "label": pretty(b["measures"][0]), "format": fmt}],
                "data": [{"x": x, m: num(v)} for x, v in rows],
                "source": {"dataset": ds, "measures": b["measures"], "dimension": "day", "window": b["window"], "filters": b["filters"], "sql": [render_sql(sql, p)]},
            }
        grain = "month" if t == "month" else "week"
        if grain == "week":
            start = start + dt.timedelta(days=(7 - start.weekday()) % 7)
        w, p = where_clause(ds, (start, end), b["filters"])
        bucket = f"date_trunc('{grain}', {t})"
        trim = f" AND {bucket} + INTERVAL 1 {grain} - INTERVAL 1 day <= ?::DATE"
        if dim:
            dcol = spec["dims"][dim][0]
            top = [r[0] for r in self.q(f"SELECT {dcol}::VARCHAR FROM {table} WHERE {w} GROUP BY 1 ORDER BY {expr if fmt in ('count', 'currency') else 'count(*)'} DESC NULLS LAST LIMIT 10", p)]
            sql = f"SELECT strftime({bucket}, '%Y-%m-%d') AS x, {dcol}::VARCHAR AS s, {expr} FROM {table} WHERE {w}{trim} GROUP BY ALL ORDER BY 1"
        else:
            top = [pretty(b["measures"][0])]
            sql = f"SELECT strftime({bucket}, '%Y-%m-%d') AS x, '{top[0]}' AS s, {expr} FROM {table} WHERE {w}{trim} GROUP BY 1 ORDER BY 1"
        params = p + [self.as_of.isoformat()]
        data = {}
        for x, sv, v in self.q(sql, params):
            if sv in top:
                data.setdefault(x, {"x": x})[sv] = num(v)
        return {
            "type": "heatmap",
            "x": {"key": "x", "kind": "time", "grain": grain},
            "series": [{"key": v, "label": v, "format": fmt} for v in top],
            "data": list(data.values()),
            "source": {"dataset": ds, "measures": b["measures"], "dimension": dim or "time", "window": b["window"], "filters": b["filters"], "sql": [render_sql(sql, params)]},
        }

    # ---- Sankey: an additive measure flowing from one dimension to another
    def _sankey(self, b):
        ds = b["dataset"]
        spec = CATALOG[ds]
        _, m, expr, fmt, desc = measure_info(b["measures"][0])
        a_dim, b_dim = b["dimension"], b["series_dimension"]
        a_col, b_col = spec["dims"][a_dim][0], spec["dims"][b_dim][0]
        w, p = where_clause(ds, window_bounds(b["window"], self.as_of, self.first_day), b["filters"])
        table = f'"{DB}".main.{spec["table"]}'
        top = lambda col: [r[0] for r in self.q(f"SELECT {col}::VARCHAR FROM {table} WHERE {w} GROUP BY 1 ORDER BY {expr} DESC NULLS LAST LIMIT 6", p)]
        ta, tb = top(a_col), top(b_col)
        ph = lambda vals: ", ".join("?" for _ in vals)
        sql = (
            f"SELECT CASE WHEN {a_col}::VARCHAR IN ({ph(ta)}) THEN {a_col}::VARCHAR ELSE 'Other' END AS a, "
            f"CASE WHEN {b_col}::VARCHAR IN ({ph(tb)}) THEN {b_col}::VARCHAR ELSE 'Other' END AS b, {expr} AS v "
            f"FROM {table} WHERE {w} GROUP BY 1, 2"
        )
        params = ta + tb + p
        flows = [(a, bb, num(v) or 0) for a, bb, v in self.q(sql, params) if (num(v) or 0) > 0]
        tot = lambda i, name: sum(f[2] for f in flows if f[i] == name)
        srcs = sorted({f[0] for f in flows}, key=lambda n: (n == "Other", -tot(0, n)))
        dsts = sorted({f[1] for f in flows}, key=lambda n: (n == "Other", -tot(1, n)))
        nodes = [{"name": n, "side": "source"} for n in srcs] + [{"name": n, "side": "target"} for n in dsts]
        links = [{"source": srcs.index(a), "target": len(srcs) + dsts.index(bb), "value": v} for a, bb, v in flows]
        flows.sort(key=lambda f: -f[2])
        return {
            "type": "sankey",
            "format": fmt,
            "source_label": a_dim.replace("_", " "),
            "target_label": b_dim.replace("_", " "),
            "nodes": nodes,
            "links": links,
            "x": {"key": "x", "kind": "category", "label": f"{a_dim} → {b_dim}"},
            "series": [{"key": m, "label": pretty(b["measures"][0]), "format": fmt}],
            "data": [{"x": f"{a} → {bb}", m: v} for a, bb, v in flows[: b["limit"]]],
            "source": {"dataset": ds, "measures": b["measures"], "dimension": f"{a_dim} → {b_dim}", "window": b["window"], "filters": b["filters"], "sql": [render_sql(sql, params)]},
        }

    # ---- Treemap: share of an additive measure, optionally grouped
    def _treemap(self, b):
        group = b.get("series_dimension")
        if not group:
            b["limit"] = max(b["limit"], 12)
            b["compare"] = "none"
            out = self._grouped(b)
            out.update(type="treemap", format=out["series"][0]["format"])
            return out
        ds = b["dataset"]
        spec = CATALOG[ds]
        _, m, expr, fmt, desc = measure_info(b["measures"][0])
        g_col, l_col = spec["dims"][group][0], spec["dims"][b["dimension"]][0]
        w, p = where_clause(ds, window_bounds(b["window"], self.as_of, self.first_day), b["filters"])
        table = f'"{DB}".main.{spec["table"]}'
        sql = f"SELECT {g_col}::VARCHAR AS g, {l_col}::VARCHAR AS l, {expr} AS v FROM {table} WHERE {w} GROUP BY 1, 2"
        rows = [(g, l, num(v) or 0) for g, l, v in self.q(sql, p) if (num(v) or 0) > 0]
        totals = {}
        for g, l, v in rows:
            totals[g] = totals.get(g, 0) + v
        groups = sorted(totals, key=lambda g: -totals[g])[:8]
        children = []
        for g in groups:
            leaves = sorted([(l, v) for gg, l, v in rows if gg == g], key=lambda x: -x[1])
            kids = [{"name": l or "(none)", "value": v} for l, v in leaves[:6]]
            rest = sum(v for _, v in leaves[6:])
            if rest:
                kids.append({"name": "other", "value": rest})
            children.append({"name": g or "(none)", "children": kids})
        return {
            "type": "treemap",
            "format": fmt,
            "tree": {"name": "All", "children": children},
            "x": {"key": "x", "kind": "category", "label": group.replace("_", " ")},
            "series": [{"key": m, "label": pretty(b["measures"][0]), "format": fmt}],
            "data": [{"x": g, m: totals[g]} for g in groups],
            "source": {"dataset": ds, "measures": b["measures"], "dimension": f"{b['dimension']} within {group}", "window": b["window"], "filters": b["filters"], "sql": [render_sql(sql, p)]},
        }

    # ---- Drivers: which values of every other dimension explain a change
    def _drivers(self, b):
        ds = b["dataset"]
        spec = CATALOG[ds]
        qm = b["measures"][0]
        _, m, expr, fmt, desc = measure_info(qm)
        additive = fmt in ("count", "currency")
        bounds = window_bounds(b["window"], self.as_of, self.first_day)
        w, p = where_clause(ds, bounds, b["filters"])
        w2, p2 = where_clause(ds, shift(bounds, b["compare"]), b["filters"])
        table = f'"{DB}".main.{spec["table"]}'
        tot = self.q(f"SELECT {expr}, count(*) FROM {table} WHERE {w}", p)[0]
        tot2 = self.q(f"SELECT {expr}, count(*) FROM {table} WHERE {w2}", p2)[0]
        now_t, before_t = num(tot[0]) or 0, num(tot2[0]) or 0
        delta_t = now_t - before_t
        min_rows = max(20, int((tot[1] or 0) * 0.01))
        filtered = {f["dimension"] for f in b["filters"]}
        rows, sqls = [], []
        for dim, (dcol, _) in spec["dims"].items():
            if dim in filtered:
                continue
            sql = (
                f"WITH cur AS (SELECT {dcol}::VARCHAR AS v, {expr} AS m, count(*) AS n FROM {table} WHERE {w} GROUP BY 1), "
                f"pri AS (SELECT {dcol}::VARCHAR AS v, {expr} AS m, count(*) AS n FROM {table} WHERE {w2} GROUP BY 1) "
                f"SELECT v, cur.m, pri.m, coalesce(cur.n, 0) + coalesce(pri.n, 0) FROM cur FULL JOIN pri USING (v)"
            )
            cand = []
            for v, now, before, n in self.q(sql, p + p2):
                now, before = num(now) or 0, num(before) or 0
                if v is None or (not additive and n < min_rows):
                    continue
                d = now - before
                cand.append((abs(d), dim, v, before, now, d))
            if len(cand) < 2:
                continue  # one value in the segment explains nothing
            cand.sort(reverse=True)
            rows += cand[:3]
            sqls.append(render_sql(sql, p + p2))
        rows.sort(reverse=True)
        rows = rows[: b["limit"]]
        good = good_direction(qm)
        data = []
        for _, dim, v, before, now, d in rows:
            row = {
                "x": f"{dim.replace('_', ' ')}: {v}",
                "dimension": dim.replace("_", " "),
                "value": v,
                "before": before,
                "now": now,
                "change": (d / abs(before)) if before else None,
                "_format": fmt,
                "_good": good,
            }
            if additive:
                row["share"] = (d / delta_t) if delta_t else None
            data.append(row)
        label = "Last year" if b["compare"] == "previous_year" else "Before"
        series = [
            {"key": "before", "label": label, "format": "row"},
            {"key": "now", "label": "Now", "format": "row"},
            {"key": "change", "label": "Change", "format": "delta_pct"},
        ]
        if additive:
            series.append({"key": "share", "label": "Share of change", "format": "pct"})
        return {
            "type": "table",
            "drivers": True,
            "x": {"key": "x", "kind": "category", "label": f"Driver of {pretty(qm).lower()}"},
            "series": series,
            "data": data,
            "total": {"label": pretty(qm), "before": before_t, "now": now_t, "format": fmt},
            "source": {"dataset": ds, "measures": [qm], "dimension": "every dimension", "window": b["window"], "filters": b["filters"], "compare": b["compare"], "sql": sqls},
        }


# --------------------------------------------------------------------------
# Change detection: deterministic "what moved" facts for the narrator
# --------------------------------------------------------------------------



def detect_changes(con, as_of):
    out = []
    for qm, label in WATCH:
        ds, m, expr, fmt, _ = measure_info(qm)
        spec = CATALOG[ds]
        t = spec["time"]
        if t == "month":
            continue
        rows = con.execute(
            f'SELECT date_trunc(\'week\', {t}) AS w, {expr} FROM "{DB}".main.{spec["table"]} '
            f"WHERE {t} <= ?::DATE GROUP BY 1 ORDER BY 1",
            [as_of.isoformat()],
        ).fetchall()
        rows = [(w, num(v)) for w, v in rows[1:-1] if v is not None]
        if len(rows) < max(16, BASELINE_WEEKS + 4):
            continue
        vals = [v for _, v in rows]
        best = None
        for i in range(8, len(vals) - 4):
            before = sum(vals[i - 8 : i]) / 8
            after = sum(vals[i : i + 4]) / 4
            sd = (sum((v - before) ** 2 for v in vals[i - 8 : i]) / 8) ** 0.5 or 1e-9
            z = abs(after - before) / sd
            if best is None or z > best[0]:
                best = (z, i, before, after)
        z, i, before, after = best
        latest = sum(vals[-4:]) / 4
        base = sum(vals[:BASELINE_WEEKS]) / BASELINE_WEEKS
        out.append(
            {
                "metric": qm,
                "label": label,
                "format": fmt,
                "changed_week": rows[i][0].isoformat(),
                "before": round(before, 6),
                "after": round(after, 6),
                "latest_4w": round(latest, 6),
                "baseline": round(base, 6),
                "baseline_from": rows[0][0].isoformat()[:10],
                "baseline_to": rows[BASELINE_WEEKS - 1][0].isoformat()[:10],
                "good": good_direction(qm),
                "change_vs_baseline_pct": round((latest - base) / base * 100, 1) if base else None,
                "strength": round(min(z, 99), 1),
            }
        )
    out.sort(key=lambda r: -abs(r["change_vs_baseline_pct"] or 0))
    return out


def fmt_range(a, b):
    m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    da, db = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    if da.year == db.year:
        return f"{m[da.month - 1]}-{m[db.month - 1]} {db.year}"
    return f"{m[da.month - 1]} {da.year % 100:02d}-{m[db.month - 1]} {db.year % 100:02d}"


def shifts_for_model(changes):
    """The change scan as the model should read it: only the shifts the page shows, with
    self-describing fields, so the step's before and after are not mistaken for the period."""
    return [
        {
            "metric": c["metric"], "label": c["label"], "format": c["format"], "better_when": c["good"],
            "last_4_weeks_avg": c["latest_4w"],
            f"baseline_avg_{c['baseline_from']}_to_{c['baseline_to'][:10]}": c["baseline"],
            "last_4_weeks_vs_baseline_pct": c["change_vs_baseline_pct"],
            "largest_step": {"week": c["changed_week"][:10], "avg_8_weeks_before": c["before"], "avg_4_weeks_after": c["after"]},
        }
        for c in changes if c["strength"] >= 3
    ][:7]


def changes_block(changes, idx):
    base_label = fmt_range(changes[0]["baseline_from"], changes[0]["baseline_to"]) if changes else "Baseline"
    rows = [
        {
            "x": c["label"],
            "baseline": c["baseline"],
            "latest": c["latest_4w"],
            "change": (c["change_vs_baseline_pct"] or 0) / 100,
            "since": c["changed_week"][:10],
            "_format": c["format"],
            "_good": c["good"],
        }
        for c in changes
        if c["strength"] >= 3
    ]
    return {
        "id": f"b{idx}",
        "type": "table",
        "title": "What moved since the baseline",
        "span": 12,
        "x": {"key": "x", "kind": "category", "label": "metric"},
        "series": [
            {"key": "baseline", "label": base_label, "format": "row"},
            {"key": "latest", "label": "Last 4 weeks", "format": "row"},
            {"key": "change", "label": "Change", "format": "delta_pct"},
            {"key": "since", "label": "Shift detected", "format": "text"},
        ],
        "data": rows,
        "timeline": {"date": "since", "title": "x"},
        "why": "Deterministic change-point scan over weekly metrics: largest step between the 8 weeks before and 4 weeks after.",
        "source": {"dataset": "several", "measures": [c["metric"] for c in changes], "dimension": "metric", "window": "all_time", "sql": ["weekly series per metric; step = mean(4 weeks after) - mean(8 weeks before), scored against the 8-week standard deviation"]},
    }


# --------------------------------------------------------------------------
# LLM steps
# --------------------------------------------------------------------------


def schema_arg(name, schema):
    return json.dumps({"name": name, "strict": True, "schema": schema})


S = {"type": "string"}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "subtitle", "blocks"],
    "properties": {
        "title": {"type": "string", "description": "Short page title, max 6 words"},
        "subtitle": {"type": "string", "description": "One line on scope and time range"},
        "blocks": {
            "type": "array",
            "description": "3 to 6 data components in reading order",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "title", "measures", "dimension", "series_dimension", "time_grain", "window", "compare", "filters", "sort", "limit", "span", "why"],
                "properties": {
                    "type": {"type": "string", "enum": BLOCK_TYPES, "description": "Component type; see the component rules"},
                    "title": {"type": "string", "description": "Descriptive title with units, max 8 words"},
                    "measures": {"type": "array", "items": {"type": "string", "enum": ALL_MEASURES}, "description": "kpi: 2-4 measures from any dataset. line/bar/table: 1-3 measures from ONE dataset"},
                    "dimension": {"type": "string", "enum": ALL_DIMS + [""], "description": "bar/table/treemap/sankey(source): the categorical dimension of the chosen dataset; heatmap: rows (or empty for a daily calendar). Empty for kpi/line"},
                    "series_dimension": {"type": "string", "enum": ALL_DIMS + [""], "description": "line: one line per value of this dimension; sankey: the target dimension; treemap: the grouping level. Usually empty"},
                    "time_grain": {"type": "string", "enum": ["day", "week", "month"]},
                    "window": {"type": "string", "enum": WINDOWS},
                    "compare": {"type": "string", "enum": COMPARES, "description": "kpi/bar/table: add a comparison"},
                    "filters": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": False, "required": ["dimension", "values"], "properties": {"dimension": {"type": "string", "enum": ALL_DIMS}, "values": {"type": "array", "items": S}}},
                    },
                    "sort": {"type": "string", "enum": ["desc", "asc"]},
                    "limit": {"type": "integer", "description": "bar/table rows, 5-15"},
                    "span": {"type": "integer", "enum": [4, 6, 8, 12], "description": "width in a 12-column grid"},
                    "why": {"type": "string", "description": "One sentence: why this component answers part of the question"},
                },
            },
        },
    },
}

PLAN_INSTRUCTIONS = """You design an analytical dashboard page (a MotherDuck Dive) that answers a {audience}'s question about {domain}.
Pick ONLY datasets, measures and dimensions from the catalog. The data runs to {as_of}; windows are relative to that date.

Components (from the component catalog):
{component_rules}
General rules:
- Start with one kpi block (compare {default_compare} unless the question implies otherwise, span 12).
- 3 to 6 blocks total. Each block must answer a distinct part of the question. Never show the same measures by the same dimension twice (not as a bar and again as a table).
- line and bar blocks: all measures must share a unit (all pct, all currency or all count). Put mixed units in a table or kpi.
- Pick the dataset whose grain matches the question. Rates are better evidence than raw counts when comparing groups of different size.
- Titles: sentence case, include the unit, e.g. "Conversion rate by section (%)". The page title is sentence case too.
- Spans: pair blocks to fill rows of 12 (8+4, 6+6, or 12).
- Use filters to focus when the question names a segment; kpi filters apply to every measure whose dataset has that dimension. Filter values must be real values from the catalog, matching case exactly.
{hints}

Catalog:
{catalog}"""


INVESTIGATE_INSTRUCTIONS = """You investigate WHY something happened, for a {audience} looking at {domain}. Design a drill-down page (a MotherDuck Dive) that explains the insight below and tests whether it is real.
Pick ONLY datasets, measures and dimensions from the catalog. The data runs to {as_of}; windows are relative to that date. Default comparison: {default_compare}.

Build 5 or 6 blocks:
1. kpi (span 12): the focus measure plus 1-3 measures that show its quality or impact (for traffic: pages per session, conversion), filtered to the segment the insight is about, with a comparison.
2. line (span 12): weekly trend of the focus measure over last_90_days or last_6_months. Filter it to the segment, and use series_dimension with the dimension most likely to explain the change (for traffic sources: referrer).
3. drivers: the focus measure, filtered to the segment, window last_28_days with the comparison. It ranks which values of every other dimension moved the number.
4. 1-3 bar or table blocks that break the segment down by the dimensions most likely to explain it (referrer, page, country, browser, topic, ...), with a comparison. Prefer a table with quality measures (rates, pages per session) next to volume when the insight questions quality.
Other components you may use where they explain better:
{component_rules}
Do not repeat the overall site view: every block should be filtered to the segment, unless it compares the segment with the rest.
Filters must use real values from the catalog, matching case exactly.
Titles: sentence case with the unit. Spans pair to fill rows of 12.
{hints}

Catalog:
{catalog}"""

NARRATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "summary", "callouts", "blocks", "follow_ups", "promote", "viewer_questions"],
    "properties": {
        "headline": {"type": "string", "description": "One sentence, the single most important finding, with a number"},
        "summary": {"type": "string", "description": "2-3 plain sentences a manager can read in 15 seconds. Specific numbers. No hype words."},
        "callouts": {
            "type": "array",
            "description": "1-4 findings that need attention or action, most important first",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tone", "title", "body", "block_id", "question", "continuity", "refers_to"],
                "properties": {
                    "tone": {"type": "string", "enum": ["critical", "warning", "positive", "info"]},
                    "continuity": {"type": "string", "enum": ["new", "ongoing", "worsening", "improving", "resolved"], "description": "Against the previous briefings' insights in memory: new, or how a previous insight developed"},
                    "refers_to": {"type": "string", "description": "Exact title of the previous insight this continues, or empty"},
                    "title": {"type": "string", "description": "max 8 words"},
                    "body": {"type": "string", "description": "1-2 sentences, at most 45 words: the evidence and a suggested action"},
                    "block_id": {"type": "string", "description": "id of the component that shows the evidence, or empty"},
                    "question": {"type": "string", "description": "The investigation a reader would run next to find out WHY, as a short question naming the metric and segment, e.g. 'Why did Other-channel visitors jump in the last 4 weeks?'"},
                },
            },
        },
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "caption", "insight", "highlight", "annotations"],
                "properties": {
                    "id": S,
                    "caption": {"type": "string", "description": "One sentence: what to notice in this component, with a number"},
                    "insight": {"type": "string", "description": "Optional second sentence: what it means for the reader or what to do, using the Guides and memory when they apply. Empty when the caption says it all"},
                    "highlight": {"type": "array", "items": S, "description": "x values (category labels or period dates) to emphasise. Must be exact x values from the data"},
                    "annotations": {
                        "type": "array",
                        "description": "line charts only: 0-3 labelled markers at exact period values from the data, e.g. where a shift starts",
                        "items": {"type": "object", "additionalProperties": False, "required": ["x", "label", "series"], "properties": {"x": S, "label": {"type": "string", "description": "max 4 words"}, "series": {"type": "string", "description": "key of the series (line) the marker is about, exactly as listed in the component's series"}}},
                    },
                },
            },
        },
        "follow_ups": {"type": "array", "items": S, "description": "3 short follow-up questions a manager would ask next, answerable with the catalog"},
        "promote": {"type": "array", "items": S, "description": "0-2 probe ids (p1, p2, p3) whose result a reader should see on the page"},
        "viewer_questions": {
            "type": "array",
            "description": "0-2 questions for the page's viewers about something the data cannot explain",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "block_id", "x", "question", "options"],
                "properties": {
                    "kind": {"type": "string", "enum": ["date", "topic"], "description": "date: a spike or drop in one period of a time chart; topic: one value of a category (a referrer, page, borough) that behaves unusually"},
                    "block_id": {"type": "string", "description": "id of the component that shows it"},
                    "x": {"type": "string", "description": "the exact x value in that component's data: the period for a date, the category value for a topic"},
                    "question": {"type": "string", "description": "max 20 words, plain, dates written as '2 Nov' (never ISO), e.g. 'What happened in the week of 2 Nov? Other-channel visitors doubled.'"},
                    "options": {"type": "array", "items": S, "description": "2-4 short likely explanations a viewer can pick, e.g. 'Newsletter mention', 'Bot traffic'"},
                },
            },
        },
    },
}

NARRATE_INSTRUCTIONS = """You are writing the narrative layer of an analytical page for a {audience}. The data below is the exact content of each component.
Rules: state what the data shows, with specific numbers; percentages for rates. Do not invent numbers: every number you write must come from the data, the probes or memory. Use only x values that appear in the data for highlight and annotations. Keep the headline under 25 words and each caption to one short sentence.
On an investigation page, the headline answers why, and the callouts separate what the data shows from what still needs checking.
Probes are follow-up queries an analyst ran to test why things moved. Use their results as evidence, but refer to them by what they show ("a follow-up query shows ..."), never by id. List in promote the ids (at most 2) of probes a reader should see.
detected_shifts compare the last 4 weeks with the start of the history; largest_step is the biggest step anywhere in that history, not the current period. Neither is the previous-period comparison the components use: when they point different ways, say which comparison you mean instead of calling it a conflict.
Memory holds earlier briefings' insights and what viewers did with them. Set each callout's continuity against it: new, ongoing (still true, similar size), worsening, improving, or resolved (an earlier insight that no longer holds; at most one, with tone positive). Set refers_to to the earlier title when continuing it, and say how long an ongoing issue has lasted. Do not repeat an earlier insight unchanged unless it is critical. Avoid insights similar to ones viewers rated not useful.
Business context from the recipe's Guides (definitions, targets, known events) decides what matters: cite a target when the data crosses it.
Viewer notes in memory are what people who know the business said about a date or a segment. Use them as the explanation when the data moves there ("the spike in the week of 2 Nov follows the launch viewers noted"), treat periods noted as a data issue as unreliable, and do not report movements noted as not important unless they grow. A component's viewer_notes are notes on periods or values it shows: when a callout, caption or insight discusses that period or value, say what viewers noted.
Ask viewer_questions when the data shows what moved but not why in business terms: a launch, campaign, partner mention, outage, tracking change or bot traffic that only a person would know. Ask about a sharp spike or drop in one period of a time chart (kind date) or about one segment that changed sharply, such as a single referrer, page or borough (kind topic). Prefer asking over guessing: when a movement drives an insight and nothing in the data, Guides or viewer notes explains its cause, ask one question. Never ask what a viewer note already answers or what open_questions already ask: those are still waiting for an answer. Offer 2-4 concrete likely explanations as options.
Known context about {domain} (use only when the data supports it): {context}
Business context from Guides:
{guides}
Question or brief: {question}
Data as of {as_of}."""

PROBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["probes"],
    "properties": {
        "probes": {
            "type": "array",
            "description": "0-3 follow-up queries that test why something moved",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hypothesis", "type", "measure", "dimension", "filters", "window", "compare"],
                "properties": {
                    "hypothesis": {"type": "string", "description": "What this query tests, as a short title, e.g. 'Is the heat rise concentrated in the Bronx?'"},
                    "type": {"type": "string", "enum": ["drivers", "bar"], "description": "drivers: rank which values of every other dimension moved the measure; bar: the measure by one dimension with a comparison"},
                    "measure": {"type": "string", "enum": ALL_MEASURES},
                    "dimension": {"type": "string", "enum": ALL_DIMS + [""], "description": "bar only; empty for drivers"},
                    "filters": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["dimension", "values"], "properties": {"dimension": {"type": "string", "enum": ALL_DIMS}, "values": {"type": "array", "items": S}}}},
                    "window": {"type": "string", "enum": WINDOWS},
                    "compare": {"type": "string", "enum": COMPARES},
                },
            },
        }
    },
}

PROBE_INSTRUCTIONS = """You are a data analyst checking a page draft for a {audience} before it is written up. Below are the page's components with their data, the shifts a change detector found, and earlier briefings' insights.
Propose up to {n} follow-up queries that would best explain WHY the most important numbers moved: which segment drives a change, whether a shift is concentrated in one value, whether an earlier insight still holds. Do not repeat what a component already shows. Use only catalog ids, and filter values that appear in the data or the catalog, matching case exactly. Return no probes when nothing needs explaining. detected_shifts come from a weekly scan that compares the last 4 weeks with the start of the history; largest_step is the biggest step anywhere in that history, not the current period. Neither is the previous-period comparison the components use. When they point different ways, say which comparison you mean; do not call it a conflict.
Business context from Guides:
{guides}
Catalog:
{catalog}"""


def with_notes(out, block, memory):
    notes = notes_for(block, memory.get("viewer_notes") or [])
    return dict(out, viewer_notes=notes) if notes else out


def compact(block):
    out = {"id": block["id"], "type": block["type"], "title": block["title"]}
    if block.get("hypothesis"):
        out["hypothesis"] = block["hypothesis"]
    if block["type"] == "kpi":
        out["items"] = [{k: i[k] for k in ("label", "value", "prior", "format")} for i in block["items"]]
        out["compare"] = block["compare"]
    else:
        out["series"] = [s["key"] for s in block["series"]]
        data = block["data"]
        if block.get("mode") == "calendar" and data:
            # A daily calendar is too long to send whole: summarise it by weekday and peak days.
            key = block["series"][0]["key"]
            days = [(dt.date.fromisoformat(d["x"]), d.get(key) or 0) for d in data]
            names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            out["weekday_average"] = {names[i]: round(sum(v for d, v in days if d.weekday() == i) / max(1, sum(1 for d, _ in days if d.weekday() == i)), 1) for i in range(7)}
            out["top_days"] = [{"x": d.isoformat(), key: v} for d, v in sorted(days, key=lambda x: -x[1])[:5]]
        if len(data) > 40:
            step = len(data) / 40
            data = [data[int(i * step)] for i in range(40)] + [data[-1]]
        out["data"] = data
        if block.get("total"):
            out["total"] = block["total"]
    return out


def biggest_mover(data, keys, x):
    """The series whose value changes most at period x: where a marker belongs."""
    i = next((j for j, d in enumerate(data) if str(d.get("x")) == x), None)
    if i is None or not keys:
        return keys[0] if keys else ""
    def move(k):
        v = data[i].get(k) or 0
        prev = data[i - 1].get(k) if i > 0 else None
        nxt = data[i + 1].get(k) if i + 1 < len(data) else None
        ref = [r for r in (prev, nxt) if r is not None]
        return max((abs(v - r) for r in ref), default=abs(v))
    return max(keys, key=move)


def apply_narrative(spec, nar):
    by_id = {b["id"]: b for b in spec["blocks"]}
    for nb in nar.get("blocks", []):
        b = by_id.get(nb.get("id"))
        if not b or b["type"] == "kpi":
            if b is not None:
                b["caption"] = nb.get("caption", "")
                if (nb.get("insight") or "").strip():
                    b["insight"] = nb["insight"].strip()
            continue
        xs = {str(d.get("x")) for d in b.get("data", [])}
        b["caption"] = nb.get("caption", "")
        if (nb.get("insight") or "").strip():
            b["insight"] = nb["insight"].strip()
        b["highlight"] = [h for h in nb.get("highlight", []) if h in xs][:6]
        if b["type"] == "line":
            b["annotations"] = [a for a in nb.get("annotations", []) if a.get("x") in xs][:3]
            keys = [s["key"] for s in b["series"] if not s.get("muted")]
            hinted = {a["x"]: a.get("series") for a in b["annotations"]}
            b["points"] = []
            for x in dict.fromkeys(b["highlight"] + [a["x"] for a in b["annotations"]]):
                s = hinted.get(x)
                b["points"].append({"x": x, "series": s if s in keys else biggest_mover(b["data"], keys, x)})
    callouts = [c for c in nar.get("callouts", []) if c.get("tone") in ("critical", "warning", "positive", "info")][:5]
    for c in callouts:
        if c.get("block_id") not in by_id:
            c["block_id"] = ""
    spec["headline"] = nar.get("headline", "")
    spec["summary"] = nar.get("summary", "")
    spec["follow_ups"] = nar.get("follow_ups", [])[:3]
    return callouts



# --------------------------------------------------------------------------
# Logging: every run, model call, insight and viewer event, in "<DB>".main
# --------------------------------------------------------------------------

LOG_DDL = [
    f"""CREATE TABLE IF NOT EXISTS "{DB}".main.gen_runs (
        run_id VARCHAR, request_id VARCHAR, mode VARCHAR, kind VARCHAR, parent VARCHAR, prompt VARCHAR,
        as_of DATE, model VARCHAR, prompt_version VARCHAR, created_at TIMESTAMP, status VARCHAR, error VARCHAR,
        duration_s DOUBLE, steps VARCHAR, plan VARCHAR, probes VARCHAR, narrative VARCHAR, checks VARCHAR,
        context VARCHAR)""",
    f"""CREATE TABLE IF NOT EXISTS "{DB}".main.llm_calls (
        run_id VARCHAR, step VARCHAR, model VARCHAR, prompt_version VARCHAR, input_chars INTEGER,
        output_chars INTEGER, latency_ms INTEGER, ok BOOLEAN, error VARCHAR, created_at TIMESTAMP)""",
    f"""CREATE TABLE IF NOT EXISTS "{DB}".main.insights (
        insight_id VARCHAR, run_id VARCHAR, kind VARCHAR, as_of DATE, created_at TIMESTAMP, rank INTEGER,
        tone VARCHAR, title VARCHAR, body VARCHAR, question VARCHAR, block_id VARCHAR, continuity VARCHAR,
        refers_to VARCHAR, first_seen DATE, times_seen INTEGER)""",
    f"""CREATE TABLE IF NOT EXISTS "{DB}".main.evals (
        run_at TIMESTAMP, prompt_version VARCHAR, model VARCHAR, narrate BOOLEAN, question VARCHAR,
        plan_score DOUBLE, grounded DOUBLE, seconds DOUBLE, detail VARCHAR)""",
    f"""CREATE TABLE IF NOT EXISTS "{DB}".main.feedback (
        created_at TIMESTAMP, recorded_by VARCHAR, spec_id VARCHAR, event VARCHAR, target VARCHAR,
        value VARCHAR, detail VARCHAR)""",
    f"""CREATE TABLE IF NOT EXISTS "{DB}".main.annotations (
        annotation_id VARCHAR, created_at TIMESTAMP, recorded_by VARCHAR, spec_id VARCHAR, kind VARCHAR, x VARCHAR,
        grain VARCHAR, dimension VARCHAR, value VARCHAR, category VARCHAR, note VARCHAR, question VARCHAR, active BOOLEAN)""",
]

FEEDBACK_EVENTS = {"rate_page", "rate_insight", "variant", "hide", "show", "layout", "investigate", "ask", "open", "theme", "annotate", "unannotate"}
NOTE_CATEGORIES = {"event", "release", "campaign", "data_issue", "expected", "noise"}
VIEWER_QUESTIONS = int(globals().get("VIEWER_QUESTIONS", 2))
ANALYST_PROBES = int(globals().get("ANALYST_PROBES", 3))
GUIDE_TOPICS = list(globals().get("GUIDE_TOPICS", []))
MEMORY_GUIDE = globals().get("MEMORY_GUIDE", True)
MEMORY_TOPIC = f"briefings/{DB}"
MEMORY_TITLE = f"What the {DB} briefing has learned"
DEFAULT_MODEL = globals().get("DEFAULT_MODEL", "gpt-6-luna")

# Changes when any instruction or schema changes, so results can be compared per prompt version.
PROMPT_VERSION = hashlib.sha1(
    json.dumps([PLAN_INSTRUCTIONS, INVESTIGATE_INSTRUCTIONS, NARRATE_INSTRUCTIONS, PROBE_INSTRUCTIONS, PLAN_SCHEMA, NARRATE_SCHEMA, PROBE_SCHEMA], sort_keys=True).encode()
).hexdigest()[:10]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


class Run:
    """Collects what one generation did: step timings and model calls."""

    def __init__(self, run_id, model):
        self.run_id, self.model = run_id, model
        self.steps, self.calls = {}, []
        self._t = {}

    def start(self, step):
        self._t[step] = time.time()
        mark(step, "running")

    def done(self, step, **fields):
        self.steps[step] = int((time.time() - self._t.get(step, time.time())) * 1000)
        mark(step, "ok", **fields)

    def llm(self, con, step, instructions, payload, name, schema):
        text = instructions + "\n\n" + payload
        t0 = time.time()
        try:
            row = con.execute("SELECT prompt(?, model := ?, json_schema := ?)::VARCHAR", [text, self.model, schema_arg(name, schema)]).fetchone()
            if not row or row[0] is None:
                raise RuntimeError(f"prompt() returned NULL for {name}")
            out = row[0]
            self.calls.append((step, len(text), len(out), int((time.time() - t0) * 1000), True, None))
            return json.loads(out)
        except Exception as e:
            self.calls.append((step, len(text), 0, int((time.time() - t0) * 1000), False, str(e)[:300]))
            raise


def llm(con, model, instructions, payload, name, schema):
    """One prompt() call without logging (kept for recipes and scripts that call it)."""
    return Run("adhoc", model).llm(con, name, instructions, payload, name, schema)


def clean_note(e):
    """A viewer note, validated: [kind, x, grain, dimension, value, category, note, question], or None."""
    kind, cat = e.get("k"), e.get("cat") or "event"
    if kind not in ("date", "topic") or cat not in NOTE_CATEGORIES:
        return None
    text = lambda v, n: re.sub(r"\s+", " ", str(v or "")).strip()[:n]
    x, dim, val = text(e.get("x"), 32), text(e.get("dim"), 64), text(e.get("val"), 200)
    if kind == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x[:10]):
            return None
        x, dim, val = x[:10], "", ""
    elif dim not in ALL_DIMS or not val:
        return None
    grain = e.get("g") if e.get("g") in ("day", "week", "month") else ("day" if kind == "date" else "")
    return [kind, x, grain, dim, val, cat, text(e.get("note"), 240), text(e.get("q"), 300)]


def record_feedback(con, raw, recorded_by):
    """Viewer events the Dive collected. A Dive can't write, so it sends them with a Flight run."""
    try:
        events = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return 0
    rows, notes = [], []
    for e in events[:60] if isinstance(events, list) else []:
        if not isinstance(e, dict) or e.get("e") not in FEEDBACK_EVENTS:
            continue
        spec_id = str(e.get("s") or "")
        if spec_id and not re.fullmatch(r"[0-9a-f-]{36}", spec_id):
            continue
        if e["e"] == "annotate":
            note = clean_note(e)
            if not note:
                continue
            notes.append([str(uuid.uuid4()), utc_now(), recorded_by, spec_id or None, *note])
            e = dict(e, t=note[1] or note[4], v=note[5], d=note[6])
        elif e["e"] == "unannotate":
            if not re.fullmatch(r"[0-9a-f-]{36}", str(e.get("t") or "")):
                continue
            con.execute(f'UPDATE "{DB}".main.annotations SET active = false WHERE annotation_id = ?', [e["t"]])
        rows.append([utc_now(), recorded_by, spec_id, e["e"], str(e.get("t") or "")[:120], str(e.get("v") or "")[:200], str(e.get("d") or "")[:300]])
    if notes:
        con.executemany(f'INSERT INTO "{DB}".main.annotations VALUES (?, ?::TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, true)', notes)
    if rows:
        con.executemany(f'INSERT INTO "{DB}".main.feedback VALUES (?::TIMESTAMP, ?, ?, ?, ?, ?, ?)', rows)
    return len(rows)


# --------------------------------------------------------------------------
# Memory: earlier briefings, viewer feedback and questions, read back into prompts
# --------------------------------------------------------------------------


def load_memory(con, as_of):
    m = {"previous_insights": [], "rated_useful": [], "rated_not_useful": [], "frequent_questions": [], "viewer_preferences": [], "briefings_remembered": 0, "viewer_notes": [], "open_questions": []}
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    briefings = q(f"""SELECT run_id, as_of FROM (
            SELECT run_id, as_of, row_number() OVER (PARTITION BY as_of ORDER BY created_at DESC) AS n
            FROM "{DB}".main.gen_runs WHERE kind = 'briefing' AND status = 'ready' AND as_of < ?)
        WHERE n = 1 ORDER BY as_of DESC LIMIT 3""", [as_of.isoformat()])
    m["briefings_remembered"] = len(briefings)
    for run_id, b_as_of in briefings:
        for title, body, tone, cont, first, times in q(
            f'SELECT title, body, tone, continuity, first_seen, times_seen FROM "{DB}".main.insights WHERE run_id = ? ORDER BY rank', [run_id]
        ):
            m["previous_insights"].append({"briefing_as_of": str(b_as_of), "title": title, "body": body, "tone": tone, "continuity": cont, "first_seen": str(first), "times_seen": times})
    for title, value in q(f"""SELECT i.title, f.value FROM "{DB}".main.feedback f JOIN "{DB}".main.insights i
            ON i.run_id = f.spec_id AND i.title = f.target WHERE f.event = 'rate_insight' ORDER BY f.created_at DESC LIMIT 20"""):
        m["rated_useful" if value == "up" else "rated_not_useful"].append(title)
    m["frequent_questions"] = [r[0] for r in q(f"""SELECT prompt FROM "{DB}".main.gen_runs WHERE kind = 'question' AND status = 'ready'
            GROUP BY prompt ORDER BY count(*) DESC, max(created_at) DESC LIMIT 8""")]
    for event, target, value, n in q(f"""SELECT event, target, value, count(*) AS n FROM "{DB}".main.feedback
            WHERE event IN ('variant', 'hide') GROUP BY ALL HAVING count(*) >= 2 ORDER BY n DESC LIMIT 8"""):
        m["viewer_preferences"].append(f"viewers switched {target} blocks to {value} {n} times" if event == "variant" else f"viewers hid {target} blocks {n} times")
    for kind, x, grain, dim, val, cat, note, created in q(f"""SELECT kind, x, grain, dimension, value, category, note, strftime(created_at, '%Y-%m-%d')
            FROM "{DB}".main.annotations WHERE active ORDER BY created_at DESC LIMIT 40"""):
        about = f"{grain or 'day'} of {x}" if kind == "date" else f"{dim} = {val}"
        m["viewer_notes"].append({"about": about, "kind": kind, "x": x, "grain": grain, "dimension": dim, "value": val, "category": cat, "note": note, "noted_on": created})
    # Questions the last briefings asked that nobody has answered yet stay open.
    seen = set()
    for (qs,) in q(f"""SELECT json_extract(narrative, '$.viewer_questions') FROM "{DB}".main.gen_runs
            WHERE kind = 'briefing' AND status = 'ready' AND as_of <= ? ORDER BY created_at DESC LIMIT 3""", [as_of.isoformat()]):
        try:
            items = [v for v in json.loads(qs or "[]") if isinstance(v, dict) and v.get("question") and v.get("kind") in ("date", "topic")]
        except (TypeError, ValueError):
            continue
        for v in items:
            if question_key(v) not in seen and not note_covers(m["viewer_notes"], v):
                seen.add(question_key(v))
                m["open_questions"].append(v)
    return m


def question_key(v):
    return f"date {v.get('x', '')[:10]}" if v.get("kind") == "date" else f"topic {v.get('dimension')}={v.get('value')}"


def period_end(day, grain):
    if grain == "week":
        return day + dt.timedelta(days=7)
    if grain == "month":
        return (day.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return day + dt.timedelta(days=1)


def note_covers(notes, v):
    """True when a viewer note answers question v: a date note inside its period, or a note on the same value."""
    for n in notes:
        if n["kind"] != v.get("kind"):
            continue
        if n["kind"] == "topic" and n["dimension"] == v.get("dimension") and n["value"] == v.get("value"):
            return True
        if n["kind"] == "date":
            try:
                start, day = dt.date.fromisoformat(v["x"][:10]), dt.date.fromisoformat(n["x"][:10])
            except (KeyError, ValueError):
                continue
            if start <= day < period_end(start, v.get("grain") or "day"):
                return True
    return False


def notes_for(block, notes):
    """The viewer notes that apply to a block: dates inside one of its periods, values it shows."""
    data = block.get("data") or []
    if not notes or not data or not isinstance(data, list):
        return []
    out = []
    time_axis = (block.get("x") or {}).get("kind") == "time"
    grain = (block.get("x") or {}).get("grain") or "day"
    dim = (block.get("source") or {}).get("dimension") or ""
    xs = sorted(str(d.get("x")) for d in data if isinstance(d, dict))
    for n in notes:
        if n["kind"] == "date" and time_axis:
            try:
                day = dt.date.fromisoformat(n["x"][:10])
            except ValueError:
                continue
            for x in xs:
                try:
                    start = dt.date.fromisoformat(x[:10])
                except ValueError:
                    break
                if start <= day < period_end(start, grain):
                    out.append({"period": x[:10], "category": n["category"], "note": n["note"]})
                    break
        elif n["kind"] == "topic":
            if any((dim == n["dimension"] and str(d.get("x")) == n["value"]) or str(d.get("value")) == n["value"] for d in data if isinstance(d, dict)):
                out.append({"value": n["value"], "category": n["category"], "note": n["note"]})
    return out[:5]


def read_guides(con):
    """Business context the recipe's Guides hold: its own topic plus GUIDE_TOPICS."""
    out, used, budget = [], [], 9000
    for topic in [MEMORY_TOPIC] + GUIDE_TOPICS:
        try:
            rows = con.execute("SELECT id, title FROM MD_LIST_GUIDES(topic = ?) LIMIT 6", [topic]).fetchall()
        except Exception:
            return "", []
        for gid, title in rows:
            if budget <= 0:
                break
            content = con.execute("SELECT content FROM MD_GET_GUIDE(id = ?::UUID)", [str(gid)]).fetchone()[0] or ""
            content = content[: min(3500, budget)]
            budget -= len(content)
            out.append(f"### {title}\n{content}")
            used.append(title)
    return "\n\n".join(out), used


def write_memory_guide(con, memory, insights, as_of):
    """After a briefing, keep a readable Guide of what the briefing has learned. People, the
    Dive's AI Chat and other agents read it; the next briefing reads it back as context."""
    if not MEMORY_GUIDE:
        return None
    lines = [f"# {MEMORY_TITLE}", "", f"Updated by the briefing Flight for data as of {as_of}. Edit freely: the Flight reads this Guide before every analysis.", ""]
    lines.append("## Current insights")
    for i in insights:
        since = f", first seen {i['first_seen']}, seen {i['times_seen']} times" if i["times_seen"] > 1 else ""
        lines.append(f"- **{i['title']}** ({i['continuity']}{since}): {i['body']}")
    resolved = [p for p in memory["previous_insights"] if p["title"] not in {i.get("refers_to") for i in insights}]
    if resolved:
        lines += ["", "## Earlier insights not repeated"]
        lines += [f"- {p['title']} (briefing of {p['briefing_as_of']})" for p in resolved[:8]]
    if memory["viewer_notes"]:
        lines += ["", "## Notes from viewers", "What people who know the business said about dates and segments. The briefing uses them to explain movements."]
        lines += [f"- **{n['about']}** ({n['category'].replace('_', ' ')}, noted {n['noted_on']}): {n['note'] or '(no text)'}" for n in memory["viewer_notes"][:20]]
    if memory["frequent_questions"]:
        lines += ["", "## What viewers ask"] + [f"- {q}" for q in memory["frequent_questions"]]
    if memory["viewer_preferences"] or memory["rated_not_useful"]:
        lines += ["", "## Viewer preferences"] + [f"- {p}" for p in memory["viewer_preferences"]]
        lines += [f"- Rated not useful: {t}" for t in memory["rated_not_useful"][:5]]
    content = "\n".join(lines)
    try:
        row = con.execute("SELECT id FROM MD_LIST_GUIDES(topic = ?) WHERE title = ? LIMIT 1", [MEMORY_TOPIC, MEMORY_TITLE]).fetchone()
        if row:
            con.execute("SELECT * FROM MD_UPDATE_GUIDE(id = ?::UUID, content = ?, change_comment = ?)", [str(row[0]), content, f"Briefing as of {as_of}"])
        else:
            con.execute(
                "SELECT * FROM MD_CREATE_GUIDE(topic = ?, title = ?, content = ?, description = ?, access = 'user')",
                [MEMORY_TOPIC, MEMORY_TITLE, content, f"Recurring insights, viewer questions and preferences the {DB} briefing has learned"],
            )
        return MEMORY_TITLE
    except Exception as e:  # Guides are optional: the run still publishes
        print(f"memory guide not written: {e}")
        return None


# --------------------------------------------------------------------------
# Number check: every number in the narrative must come from the data
# --------------------------------------------------------------------------

TIME_WORD = re.compile(r"\s*\+?\s*-?(days?|weeks?|months?|years?|hours?|briefings?|times|straight)\b", re.I)
NUM = re.compile(r"(?<![\w.-])(-?\d[\d,]*(?:\.\d+)?)\s*(%|K\b|k\b|M\b|million|thousand|percentage points|pp\b)?")


def data_numbers(spec, extra=()):
    vals = []
    def add(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        if f == f:
            vals.append(f)
    def pair(a, b):
        add(a), add(b)
        try:
            a, b = float(a), float(b)
            add(a - b)
            if b:
                add((a - b) / abs(b))
        except (TypeError, ValueError):
            pass
    for b in list(spec.get("blocks", [])) + list(extra):
        for it in b.get("items", []) or []:
            if isinstance(it, dict) and "value" in it:
                pair(it.get("value"), it.get("prior"))
        for row in b.get("data", []) or []:
            for k, v in row.items():
                if k.endswith("_prior"):
                    pair(row.get(k[: -len("_prior")]), v)
                elif not k.startswith("_"):
                    add(v)
            if "before" in row and "now" in row:
                pair(row["now"], row["before"])
            if "baseline" in row and "latest" in row:
                pair(row["latest"], row["baseline"])
        t = b.get("total") or {}
        if t:
            pair(t.get("now"), t.get("before"))
        for link in b.get("links", []) or []:
            add(link.get("value"))
        for node in (b.get("tree") or {}).get("children", []) or []:
            for leaf in node.get("children", []) or []:
                add(leaf.get("value"))
    return vals


def text_numbers(text):
    """Numbers stated in Guides and earlier insights, which the narrative may quote back (targets, last week's figures)."""
    out = []
    for raw, unit in NUM.findall(text or ""):
        n = float(raw.replace(",", "")) * {"K": 1e3, "k": 1e3, "thousand": 1e3, "M": 1e6, "million": 1e6}.get(unit or "", 1)
        out += [n, n / 100] if unit == "%" else [n]
    return out


def check_numbers(spec, texts, extra=(), allowed=()):
    """Share of numbers in the narrative that match a value, a difference or a change in the data,
    or a number the Guides or earlier insights state."""
    vals = data_numbers(spec, extra) + list(allowed)
    pcts = [v * 100 for v in vals]
    checked, missing = 0, []
    for text in texts:
        text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text or "")  # dates are not claims
        for m in NUM.finditer(text):
            raw, unit = m.group(1), m.group(2)
            n = float(raw.replace(",", ""))
            if (unit or "") == "" and (abs(n) <= 12 or 1990 <= n <= 2100):
                continue  # small counts and years are not claims about the data
            if not unit and TIME_WORD.match(text, m.end()):
                continue  # "the last 28 days", "7+ days" describe a window, not a value
            mult = {"K": 1e3, "k": 1e3, "thousand": 1e3, "M": 1e6, "million": 1e6}.get(unit or "", 1)
            target = n * mult
            checked += 1
            if unit in ("%", "percentage points", "pp"):
                ok = any(abs(abs(p) - abs(n)) <= max(0.06, abs(n) * 0.006) for p in pcts) or any(abs(abs(v) - abs(n)) <= 0.06 for v in vals)
            else:
                tol = 0.051 if abs(target) < 100 and mult == 1 else abs(target) * (0.006 if mult == 1 else 0.051)
                ok = any(abs(abs(v) - abs(target)) <= tol for v in vals)
            if not ok:
                missing.append(raw + (unit or ""))
    return {"checked": checked, "grounded": checked - len(missing), "ungrounded": missing[:12]}


def narrative_texts(spec, callouts):
    texts = [spec.get("headline", ""), spec.get("summary", "")]
    texts += [c.get("title", "") + " " + c.get("body", "") for c in callouts]
    texts += [b.get("caption", "") + " " + b.get("insight", "") for b in spec.get("blocks", [])]
    return texts


# --------------------------------------------------------------------------
# One generation: plan -> compile -> probe -> narrate (also used by eval.py)
# --------------------------------------------------------------------------


def connect():
    # Product analytics only: which agent harness and model built this Flight.
    agent_harness = os.environ.get("MOTHERDUCK_AGENT_HARNESS") or "unknown"
    agent_llm = os.environ.get("MOTHERDUCK_AGENT_LLM") or "unknown"
    con = duckdb.connect("md:", config={"custom_user_agent": f"agent-skills/3.0.0(harness-{agent_harness};llm-{agent_llm})"})
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{DB}"')
    for alias, path in ATTACH:
        con.execute(f"ATTACH IF NOT EXISTS '{path}' AS {alias}")
    con.execute(SPECS_DDL)
    for ddl in LOG_DDL:
        con.execute(ddl)
    return con


def resolve_as_of(con, as_of_env=""):
    if as_of_env:
        return dt.date.fromisoformat(as_of_env)
    if AS_OF_DEFAULT == "yesterday":
        return dt.date.today() - dt.timedelta(days=1)
    return con.execute(f'SELECT max({ANCHOR[1]}) FROM "{DB}".main.{ANCHOR[0]}').fetchone()[0]


def memory_text(memory):
    parts = []
    if memory["previous_insights"]:
        parts.append("Recent briefing insights: " + "; ".join(f"{p['title']} ({p['briefing_as_of']})" for p in memory["previous_insights"][:8]))
    if memory["viewer_preferences"]:
        parts.append("Viewer preferences: " + "; ".join(memory["viewer_preferences"]))
    if memory["frequent_questions"]:
        parts.append("Questions viewers asked before: " + "; ".join(memory["frequent_questions"][:5]))
    return "\n".join(parts)


def make_plan(con, run, mode, question, parent, memory, guides_text, as_of):
    if mode not in ("ask", "investigate"):
        return BRIEFING_PLAN
    template = INVESTIGATE_INSTRUCTIONS if mode == "investigate" else PLAN_INSTRUCTIONS
    payload = f"Question: {question}"
    if parent and parent.get("insight"):
        ins = parent["insight"]
        payload = f"Insight to investigate: {ins.get('title')}. {ins.get('body')}\n{payload}"
    extra = memory_text(memory)
    if guides_text:
        extra += "\n\nBusiness context from Guides:\n" + guides_text[:4000]
    if extra:
        payload += "\n\nContext (use it to choose what to show; do not repeat it):\n" + extra
    return run.llm(
        con, "plan",
        template.format(component_rules=COMPONENT_RULES, audience=AUDIENCE, domain=DOMAIN_DESCRIPTION, default_compare=DEFAULT_COMPARE, hints=PLANNER_HINTS, as_of=as_of.isoformat(), catalog=catalog_text(con)),
        payload, "dive_plan", PLAN_SCHEMA,
    )


def compile_plan(compiler, plan, mode):
    blocks, problems, seen = [], [], set()
    for i, pb in enumerate(plan["blocks"][: 10 if mode in ("briefing", "refresh") else 7]):
        try:
            blk = compiler.compile(dict(pb), i + 1)
            src = blk["source"]
            sig = json.dumps([blk["type"] == "kpi", src.get("measures"), src.get("dimension"), src.get("filters"), src.get("window")], sort_keys=True, default=str)
            if sig in seen:
                problems.append(f"{pb.get('title')}: duplicate of an earlier component, dropped")
                continue
            seen.add(sig)
            blocks.append(blk)
        except Exception as e:  # keep the page if one block fails
            problems.append(f"{pb.get('title')}: {e}")
    return blocks, problems


def about(block):
    """Definitions for the measures and dimension a block shows, from the catalog."""
    src = block.get("source") or {}
    defs = []
    for qm in src.get("measures") or []:
        if "." in qm and qm in ALL_MEASURES:
            ds, m, _, fmt, desc = measure_info(qm)
            defs.append({"term": pretty(qm), "text": desc + (" Lower is better." if good_direction(qm) == "down" else "")})
    ds = src.get("dataset")
    dim = src.get("dimension")
    if ds in CATALOG and dim in CATALOG[ds]["dims"]:
        defs.append({"term": dim.replace("_", " ").capitalize(), "text": CATALOG[ds]["dims"][dim][1]})
    return {"definitions": defs, "why": block.get("why", "")}


def run_probes(con, run, compiler, blocks, changes, memory, guides_text, n_existing):
    if ANALYST_PROBES <= 0:
        return [], []
    payload = json.dumps({"components": [with_notes(compact(b), b, memory) for b in blocks], "detected_shifts": shifts_for_model(changes), "earlier_insights": memory["previous_insights"][:8], "viewer_notes": memory["viewer_notes"][:15]}, default=str)
    out = run.llm(con, "probe", PROBE_INSTRUCTIONS.format(audience=AUDIENCE, n=ANALYST_PROBES, guides=guides_text[:3000] or "(none)", catalog=catalog_text(con)), payload, "dive_probes", PROBE_SCHEMA)
    probes, results = out.get("probes", [])[:ANALYST_PROBES], []
    for i, p in enumerate(probes):
        pb = {"type": p["type"], "title": p["hypothesis"], "measures": [p["measure"]], "dimension": p.get("dimension", ""), "filters": p.get("filters", []), "window": p.get("window"), "compare": p.get("compare"), "limit": 8, "span": 6, "why": p["hypothesis"]}
        try:
            blk = compiler.compile(pb, n_existing + i + 1)
            blk.update(id=f"p{i + 1}", hypothesis=p["hypothesis"], probe=True)
            results.append(blk)
        except Exception as e:
            p["error"] = str(e)[:200]
    return probes, results


def narrate(con, run, spec, blocks, probe_blocks, changes, memory, guides_text, question, as_of):
    payload = json.dumps({
        "components": [with_notes(compact(b), b, memory) for b in blocks],
        "probes": [compact(b) for b in probe_blocks],
        "detected_shifts": shifts_for_model(changes),
        "memory": {k: memory[k] for k in ("previous_insights", "rated_useful", "rated_not_useful", "viewer_preferences", "viewer_notes")},
        "open_questions": [v["question"] for v in memory["open_questions"]],
    }, default=str)
    instructions = NARRATE_INSTRUCTIONS.format(audience=AUDIENCE, domain=DOMAIN_DESCRIPTION, context=DOMAIN_CONTEXT, guides=guides_text[:5000] or "(none)", question=question, as_of=as_of.isoformat())
    return run.llm(con, "narrate", instructions, payload, "dive_narrative", NARRATE_SCHEMA), instructions, payload


def build_questions(spec, nar, memory):
    """The narrator's questions for viewers, kept only when they point at a real x in a
    block and no viewer note or recent briefing already covers them."""
    by_id = {b["id"]: b for b in spec["blocks"]}
    carried = memory["open_questions"]
    open_keys = {question_key(v) for v in carried}
    out = []
    for v in nar.get("viewer_questions") or []:
        b = by_id.get(v.get("block_id"))
        q = (v.get("question") or "").strip()
        if not b or not q or not b.get("data"):
            continue
        x = str(v.get("x") or "")
        row = next((d for d in b["data"] if str(d.get("x")) == x), None)
        if row is None:
            continue
        item = {"id": f"q{len(out) + 1}", "kind": v.get("kind"), "question": q[:200], "options": [str(o)[:40] for o in (v.get("options") or [])][:4], "block_id": b["id"]}
        if v.get("kind") == "date" and (b.get("x") or {}).get("kind") == "time":
            grain = b["x"].get("grain") or "day"
            day = dt.date.fromisoformat(x[:10])
            item.update(x=x[:10], grain=grain, label={"week": f"the week of {day.day} {day:%b %Y}", "month": f"{day:%B %Y}"}.get(grain, f"{day.day} {day:%b %Y}"))
            about = f"{grain} of {x[:10]}"
        elif v.get("kind") == "topic":
            dim = (b.get("source") or {}).get("dimension") or ""
            value = x
            if row.get("value") is not None and row.get("dimension"):  # a drivers row: "referrer: example.org"
                dim, value = str(row["dimension"]).replace(" ", "_"), str(row["value"])
            if dim not in ALL_DIMS:
                continue
            item.update(x=value, dimension=dim, value=value, label=f"{dim.replace('_', ' ')} {value}")
            about = f"{dim} = {value}"
        else:
            continue
        if note_covers(memory["viewer_notes"], item) or question_key(item) in open_keys:
            continue
        out.append(item)
    # Unanswered questions from earlier briefings stay on the page; a new one may join them.
    keep = carried[: max(1, VIEWER_QUESTIONS - len(out))] if out else carried
    merged = [dict(v, id=f"q{i + 1}", carried=True) for i, v in enumerate(keep)]
    merged += [dict(v, id=f"q{len(merged) + i + 1}") for i, v in enumerate(out)]
    return merged[:VIEWER_QUESTIONS]


def link_insights(callouts, memory, as_of):
    """Carry first_seen and times_seen forward from the earlier insight a callout continues."""
    prev = {p["title"].strip().lower(): p for p in memory["previous_insights"]}
    for c in callouts:
        p = prev.get((c.get("refers_to") or "").strip().lower())
        if c.get("continuity") not in ("new", "ongoing", "worsening", "improving", "resolved"):
            c["continuity"] = "new"
        if p and c["continuity"] != "new":
            c["first_seen"] = p.get("first_seen") if p.get("first_seen") not in (None, "None") else p["briefing_as_of"]
            c["times_seen"] = int(p.get("times_seen") or 1) + 1
        else:
            c["first_seen"], c["times_seen"] = as_of.isoformat(), 1
            if c["continuity"] != "resolved":
                c["continuity"], c["refers_to"] = "new", ""
    return callouts


def generate(con, run, mode, question, parent, as_of):
    """Everything but publishing: returns spec, plan, probes, narrative, checks, context."""
    compiler = Compiler(con, as_of)
    memory = load_memory(con, as_of)
    guides_text, guides_used = read_guides(con)

    run.start("plan")
    plan = make_plan(con, run, mode, question, parent, memory, guides_text, as_of)
    run.done("plan", rows=len(plan["blocks"]))

    run.start("compile")
    blocks, problems = compile_plan(compiler, plan, mode)
    changes = detect_changes(con, as_of)
    if mode in ("briefing", "refresh"):
        blocks.insert(1, changes_block(changes, len(blocks) + 1))
    if not blocks:
        raise RuntimeError("no component compiled: " + "; ".join(problems))
    for b in blocks:
        b["about"] = about(b)
    run.done("compile", rows=len(blocks))

    run.start("probe")
    try:
        probes, probe_blocks = run_probes(con, run, compiler, blocks, changes, memory, guides_text, len(blocks))
    except Exception as e:  # the page stands without probes
        probes, probe_blocks = [{"error": str(e)[:200]}], []
    run.done("probe", rows=len(probe_blocks))

    spec = {
        "version": SPEC_VERSION, "id": run.run_id, "kind": {"ask": "question", "investigate": "investigation"}.get(mode, "briefing"),
        "prompt": question, "as_of": as_of.isoformat(), "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "model": run.model, "prompt_version": PROMPT_VERSION,
        "title": plan.get("title") or "Generated Dive", "subtitle": plan.get("subtitle") or "", "blocks": blocks, "problems": problems,
    }
    if parent:
        spec.update(parent=parent["id"], parent_title=parent["title"], insight=parent.get("insight"))

    run.start("narrate")
    brief = question
    if parent and parent.get("insight"):
        brief = f"{question} (investigating: {parent['insight'].get('title')}. {parent['insight'].get('body')})"
    nar, _, _ = narrate(con, run, spec, blocks, probe_blocks, changes, memory, guides_text, brief, as_of)
    # Promote the probes the narrator chose, then check every number against the data.
    promoted = [b for b in probe_blocks if b["id"] in (nar.get("promote") or [])[:2]]
    for b in promoted:
        b["about"] = about(b)
    spec["blocks"] = blocks + promoted
    callouts = apply_narrative(spec, nar)
    allowed = text_numbers(guides_text) + text_numbers(" ".join(p["body"] or "" for p in memory["previous_insights"])) + text_numbers(" ".join(n["note"] or "" for n in memory["viewer_notes"]))
    checks = check_numbers(spec, narrative_texts(spec, callouts), probe_blocks, allowed)
    if checks["checked"] and checks["grounded"] / checks["checked"] < 0.85:
        # One retry with the ungrounded numbers named; keep whichever result checks better.
        retry_q = brief + f"\nThe previous draft used numbers that are not in the data: {', '.join(checks['ungrounded'])}. Use only numbers from the data."
        nar2, _, _ = narrate(con, run, spec, blocks, probe_blocks, changes, memory, guides_text, retry_q, as_of)
        spec2 = dict(spec, blocks=[dict(b) for b in blocks + [b for b in probe_blocks if b["id"] in (nar2.get("promote") or [])[:2]]])
        callouts2 = apply_narrative(spec2, nar2)
        checks2 = check_numbers(spec2, narrative_texts(spec2, callouts2), probe_blocks, allowed)
        if checks2["checked"] == 0 or checks2["grounded"] / max(1, checks2["checked"]) >= checks["grounded"] / checks["checked"]:
            spec, nar, callouts, checks = spec2, nar2, callouts2, dict(checks2, retried=True)
    callouts = link_insights(callouts, memory, as_of)
    if callouts:
        spec["blocks"].insert(1, {"id": "callouts", "type": "callouts", "title": "Insights" if spec["kind"] == "briefing" else "Findings", "span": 12, "items": callouts})
    questions = build_questions(spec, nar, memory) if VIEWER_QUESTIONS > 0 else []
    nar["viewer_questions"] = questions  # what was asked, so later briefings do not ask again
    if questions:
        spec["blocks"].insert(2 if callouts else 1, {"id": "questions", "type": "questions", "title": "Questions for you", "span": 12, "items": questions})
    context = {
        "briefings_remembered": memory["briefings_remembered"], "earlier_insights": len(memory["previous_insights"]),
        "feedback_used": len(memory["rated_useful"]) + len(memory["rated_not_useful"]) + len(memory["viewer_preferences"]),
        "guides": guides_used, "probes_run": len(probe_blocks), "probes_shown": len(promoted),
        "viewer_notes": len(memory["viewer_notes"]),
    }
    spec["checks"], spec["context"] = checks, context
    run.done("narrate", rows=len(callouts))
    return spec, plan, probes, nar, callouts, memory


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main():
    t0 = time.time()
    mode = (os.environ.get("MODE") or "briefing").strip().lower()
    prompt_text = (os.environ.get("PROMPT") or "").strip()[:600]
    request_id = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("REQUEST_ID") or "")[:64] or str(uuid.uuid4())
    model = os.environ.get("MODEL") or DEFAULT_MODEL
    parent_id = (os.environ.get("PARENT") or "").strip()
    if mode not in ("briefing", "ask", "investigate", "refresh", "feedback"):
        raise SystemExit(f"unknown MODE {mode!r}")
    if mode in ("ask", "investigate") and not prompt_text:
        raise SystemExit(f"MODE={mode} needs a PROMPT")
    if parent_id and not re.fullmatch(r"[0-9a-f-]{36}", parent_id):
        raise SystemExit("PARENT must be a spec id")

    mark("@flight", "running")
    con = connect()
    spec_id = str(uuid.uuid4())
    events = record_feedback(con, os.environ.get("FEEDBACK"), spec_id)
    if mode == "feedback":
        mark("publish", "ok", rows=events, effect=f"table:{DB}.main.feedback")
        print(json.dumps({"request_id": request_id, "status": "recorded", "events": events}))
        mark("@flight", "ok")
        return

    mark("marts", "running")
    built = build_marts(con, force=mode == "refresh", refresh_ok=mode in ("briefing", "refresh"))
    mark("marts", "ok", rows=built)
    as_of = resolve_as_of(con, (os.environ.get("AS_OF") or "").strip())

    kind = {"ask": "question", "investigate": "investigation"}.get(mode, "briefing")
    question = prompt_text if mode in ("ask", "investigate") else BRIEFING_QUESTION
    parent = None
    if parent_id:
        row = con.execute(f'SELECT spec FROM "{DB}".main.dive_specs WHERE id = ?', [parent_id]).fetchone()
        if row:
            ps = json.loads(row[0])
            items = [c for b in ps.get("blocks", []) if b.get("type") == "callouts" for c in b.get("items", [])]
            hit = next((c for c in items if c.get("question") == prompt_text), None)
            parent = {"id": parent_id, "title": ps.get("prompt") if ps.get("kind") != "briefing" else ps.get("title"), "insight": hit}

    run = Run(spec_id, model)
    plan = probes = nar = None
    callouts, memory = [], None
    try:
        spec, plan, probes, nar, callouts, memory = generate(con, run, mode, question, parent, as_of)
        status, error = "ready", None
    except Exception as e:
        traceback.print_exc()
        spec = {"version": SPEC_VERSION, "id": spec_id, "kind": kind, "prompt": question, "as_of": as_of.isoformat(), "title": "Generation failed", "blocks": [], **({"parent": parent["id"], "parent_title": parent["title"]} if parent else {})}
        status, error = "failed", str(e)[:500]

    mark("publish", "running")
    now = utc_now()
    duration = round(time.time() - t0, 1)
    con.execute(
        f'INSERT INTO "{DB}".main.dive_specs VALUES (?, ?, ?, ?, ?, ?::TIMESTAMP, ?, ?, ?, ?, ?, ?)',
        [spec_id, request_id, kind, question, spec.get("title"), now, as_of, model, status, error, duration, json.dumps(spec, default=str)],
    )
    con.execute(
        f'INSERT INTO "{DB}".main.gen_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [spec_id, request_id, mode, kind, parent_id or None, question, as_of, model, PROMPT_VERSION, now, status, error, duration,
         json.dumps(run.steps), json.dumps(plan, default=str), json.dumps(probes, default=str), json.dumps(nar, default=str),
         json.dumps(spec.get("checks"), default=str), json.dumps(spec.get("context"), default=str)],
    )
    if run.calls:
        con.executemany(
            f'INSERT INTO "{DB}".main.llm_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::TIMESTAMP)',
            [[spec_id, step, model, PROMPT_VERSION, i, o, ms, ok, err, now] for step, i, o, ms, ok, err in run.calls],
        )
    if callouts:
        con.executemany(
            f'INSERT INTO "{DB}".main.insights VALUES (?, ?, ?, ?, ?::TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [[str(uuid.uuid4()), spec_id, kind, as_of, now, r, c.get("tone"), c.get("title"), c.get("body"), c.get("question"), c.get("block_id"),
              c.get("continuity"), c.get("refers_to") or None, c.get("first_seen"), c.get("times_seen")] for r, c in enumerate(callouts)],
        )
    mark("publish", "ok", effect=f"table:{DB}.main.dive_specs")
    if status == "ready" and kind == "briefing" and memory is not None:
        mark("learn", "running")
        guide = write_memory_guide(con, memory, callouts, as_of)
        mark("learn", "ok", effect=f"guide:{MEMORY_TOPIC}" if guide else "")
    print(json.dumps({"spec_id": spec_id, "request_id": request_id, "status": status, "error": error, "seconds": duration, "prompt_version": PROMPT_VERSION, "checks": spec.get("checks"), "context": spec.get("context")}, default=str))
    if status != "ready":
        mark("@flight", "failed")
        raise SystemExit(1)
    mark("@flight", "ok")


if __name__ == "__main__":
    main()
