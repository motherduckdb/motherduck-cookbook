"""
Build your own analysis agent, then run it on a schedule (MotherDuck Flight).

An agent is just a loop: call a model, let it call tools, feed results back,
repeat until it answers. You do not hand-write that loop here, Pydantic AI runs
it. What makes the agent strong is what you compose around the loop:

  1. a model, via OpenRouter, so the agent is not locked to one provider
     (swap the MODEL slug for any tool-capable model),
  2. a skill: the inline SKILL constant below, your company/domain context,
  3. tools you build yourself: explore_warehouse (read-only SQL over
     sample_data) and get_weather (the Open-Meteo archive API).

The Flight discovers the active NYC boroughs in a recent window of the public
311 dataset, then fans out one agent per borough to write a "notable things"
brief, grounded in real query results and enriched with weather where it
explains activity. Briefs are written to flights_demo.main.borough_briefs. One
borough failing never aborts the batch.

The sample data is a frozen snapshot (it ends in 2023), so the lookback window
is anchored to MAX(created_date), not now(). Against a live table you would
anchor to now() instead.

Runtime inputs:
  OPENROUTER_API_KEY  remapped from a Flights secret (any *_OPENROUTER_API_KEY)
  MOTHERDUCK_TOKEN    auto-injected by the Flights runtime; used by
                      duckdb.connect for discovery, persistence, and the
                      warehouse tool
  BRIEF_WINDOW_DAYS   lookback window in days (default "7")
  CONCURRENCY         max simultaneous agents (default "3")
  MODEL               OpenRouter model slug (default "anthropic/claude-sonnet-4.6")
  MAX_BOROUGHS        cap borough count for testing; "0" = all (default "0")
  BOROUGHS            optional comma-separated override; skips discovery
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import duckdb
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


WINDOW_DAYS = max(1, int(os.environ.get("BRIEF_WINDOW_DAYS", "7")))
CONCURRENCY = max(1, int(os.environ.get("CONCURRENCY", "3")))
MODEL = os.environ.get("MODEL", "anthropic/claude-sonnet-4.6").strip()
MAX_BOROUGHS = int(os.environ.get("MAX_BOROUGHS", "0"))
BOROUGHS_OVERRIDE = os.environ.get("BOROUGHS", "").strip()

# The warehouse tool connects to a single database (md:sample_data). Discovery
# and persistence use md: because they also write to flights_demo.
SOURCE_TABLE = "sample_data.nyc.service_requests"
RESULTS_TABLE = "flights_demo.main.borough_briefs"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"


def resolve_openrouter_key() -> str:
    """Return the OpenRouter API key.

    A local run sets OPENROUTER_API_KEY directly. Deployed as a Flight, the key
    comes from a `TYPE flights` secret, which MotherDuck injects under the env
    var `<secret_name>_<PARAM>`, not the bare param name. So a secret named
    `openrouter` with an OPENROUTER_API_KEY param arrives as
    `openrouter_OPENROUTER_API_KEY`. Accept the exact name first (local), then
    any var ending in the suffix (the secret, whatever you named it).
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    for name, value in os.environ.items():
        if name.endswith("_OPENROUTER_API_KEY") and value.strip():
            log(f"Using OPENROUTER_API_KEY from secret env var {name!r}")
            return value.strip()
    raise SystemExit(
        "OPENROUTER_API_KEY is required (set it locally or add a Flights secret)."
    )


# ---- Tool 1: a read-only warehouse tool you build yourself -------------------
# json_serialize_sql() is a DuckDB scalar that parses a SQL string to its AST as
# JSON, but ONLY for SELECT statements. Anything that mutates state
# (INSERT/UPDATE/DELETE/CREATE/ATTACH/...) comes back with error=true instead of
# an AST. So the read-only check is: serialize the query, and only run it if
# error is false. This is the same primitive motherduck-wasm and Dive use.
MAX_ROWS = 200


def is_read_only(con: duckdb.DuckDBPyConnection, sql: str) -> bool:
    row = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    payload = json.loads(row[0])
    return payload.get("error") is False


def run_select(sql: str) -> str:
    # A fresh connection per call keeps concurrent tool calls independent. It is
    # scoped to a single database (md:sample_data), one layer of least privilege.
    con = duckdb.connect("md:sample_data")
    try:
        if not is_read_only(con, sql):
            return (
                "ERROR: only SELECT statements are allowed. For schema, query "
                "information_schema (e.g. SELECT column_name, data_type FROM "
                "information_schema.columns WHERE table_name = 'service_requests') "
                "or SELECT * FROM <table> LIMIT 0."
            )
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
        header = " | ".join(columns)
        body = "\n".join(
            " | ".join("" if v is None else str(v) for v in r) for r in rows
        )
        note = f"\n... (truncated to {MAX_ROWS} rows)" if len(rows) == MAX_ROWS else ""
        return f"{header}\n{body}{note}" if rows else f"{header}\n(no rows)"
    except Exception as e:
        return f"ERROR: {e!r}"
    finally:
        con.close()


async def explore_warehouse(sql: str) -> str:
    """Run a read-only DuckDB SELECT against the sample_data database and return rows.

    Only SELECT is supported: statements that write or change state are refused.
    To explore the schema, use SELECT against information_schema (columns,
    tables) or SELECT * FROM <table> LIMIT 0, not PRAGMA. The 311 data is in the
    table sample_data.nyc.service_requests.
    """
    return await asyncio.to_thread(run_select, sql)


# ---- Tool 2: an external-API tool you build yourself -------------------------
# A thin wrapper over the Open-Meteo historical archive. It turns a location and
# date range into daily weather, so the agent can explain 311 activity (a rain
# day driving flooding complaints, a heat spike driving others). The archive API
# needs no key. Borough coordinates live in the SKILL, so this stays a thin,
# honest wrapper.
def fetch_weather(latitude: float, longitude: float, start_date: str, end_date: str) -> dict:
    params = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum",
        "timezone": "America/New_York",
    })
    with urllib.request.urlopen(f"{WEATHER_URL}?{params}", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize_weather(payload: dict) -> str:
    daily = payload.get("daily", {})
    days = daily.get("time", [])
    if not days:
        return "No weather data for that range."
    lines = []
    for i, day in enumerate(days):
        tmax = daily["temperature_2m_max"][i]
        tmin = daily["temperature_2m_min"][i]
        precip = daily["precipitation_sum"][i] or 0.0
        snow = daily["snowfall_sum"][i] or 0.0
        if snow > 0:
            cond = "snow"
        elif precip >= 1.0:
            cond = "rain"
        else:
            cond = "dry"
        lines.append(
            f"{day}: {tmin:.0f} to {tmax:.0f} C, precip {precip:.1f}mm, "
            f"snow {snow:.1f}cm ({cond})"
        )
    return "\n".join(lines)


async def get_weather(latitude: float, longitude: float,
                      start_date: str, end_date: str) -> str:
    """Daily historical weather for a location and date range (dates as YYYY-MM-DD).

    Returns one line per day: min/max temperature, precipitation, snowfall, and a
    rain/dry/snow label. Use the borough coordinates listed in your instructions.
    """
    payload = await asyncio.to_thread(fetch_weather, latitude, longitude, start_date, end_date)
    return summarize_weather(payload)
