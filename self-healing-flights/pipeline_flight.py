"""Pipeline Flight: load the NWS 7-day forecast into heal_demo.weather.forecast and test it.

NWS API notes: each period has `temperature` (int) and `temperatureUnit` ("F"/"C"). With the forecast_temperature_qv
format, `temperature` is {"unitCode": "wmoUnit:degC" | "wmoUnit:degF", "value": float} and `temperatureUnit` is null.
"""
import json
import os
import sys

import duckdb
import requests

TARGET_DB = os.environ.get("TARGET_DB", "heal_demo")  # the fixer points this at a sandbox clone
SANDBOX = os.environ.get("HEAL_SANDBOX") == "1"
GRIDS = {"seattle": "SEW/125,68", "new_york": "OKX/33,42", "los_angeles": "LOX/155,45"}
HEADERS = {"User-Agent": "motherduck-self-healing-demo/1.0 (https://motherduck.com)", "Accept": "application/geo+json"}
# Simulates the NWS rolling out a new payload shape (set NWS_FEATURE_FLAGS=forecast_temperature_qv in the Flight config).
if os.environ.get("NWS_FEATURE_FLAGS"):
    HEADERS["Feature-Flags"] = os.environ["NWS_FEATURE_FLAGS"]

con = duckdb.connect("md:")
con.execute("SET TimeZone = 'UTC'")


def report(error_type, message):
    # Tell the checker what went wrong (sandbox runs stay quiet so they never look like real failures).
    print(f"HEAL_EVENT {json.dumps({'error_type': error_type, 'message': message})}", flush=True)
    if not SANDBOX:
        con.execute("""INSERT INTO heal.main.pipeline_events (pipeline, flight_id, run_number, severity, error_type, message)
                       VALUES ('weather', ?::UUID, ?, 'error', ?, ?)""",
                    [os.environ.get("MOTHERDUCK_FLIGHT_ID"), os.environ.get("MOTHERDUCK_FLIGHT_RUN_NUMBER"), error_type, message[:2000]])


rows = []
try:
    for city, grid in GRIDS.items():
        r = requests.get(f"https://api.weather.gov/gridpoints/{grid}/forecast", headers=HEADERS, timeout=30)
        r.raise_for_status()
        for p in r.json()["properties"]["periods"]:
            rows.append((city, p["number"], p["name"], p["startTime"], p.get("temperature"), p.get("temperatureUnit"), p.get("shortForecast")))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {TARGET_DB}.weather")
    con.execute("CREATE OR REPLACE TEMP TABLE stg (city VARCHAR, number INT, name VARCHAR, start_time TIMESTAMPTZ, temperature VARCHAR, unit VARCHAR, forecast VARCHAR)")
    con.executemany("INSERT INTO stg VALUES (?, ?, ?, ?, ?, ?, ?)", [(*r[:4], json.dumps(r[4]), *r[5:]) for r in rows])
    con.execute(f"""CREATE OR REPLACE TABLE {TARGET_DB}.weather.forecast AS
        SELECT city, number, name, start_time, TRY_CAST(temperature AS DOUBLE) AS temperature, unit, forecast, now() AS loaded_at FROM stg""")
except Exception as e:
    report(type(e).__name__, f"{type(e).__name__}: {e}")
    raise

# Data test: every period needs a numeric temperature and a unit.
bad = con.execute(f"SELECT count(*) FROM {TARGET_DB}.weather.forecast WHERE temperature IS NULL OR unit IS NULL").fetchone()[0]
sample = con.execute(f"SELECT * FROM stg LIMIT 2").fetchall()
if bad:
    report("DataTestFailed", f"{bad} of {len(rows)} forecast rows have NULL temperature or unit; sample raw rows: {sample}")
    sys.exit(1)
print(f"weather: loaded {len(rows)} rows into {TARGET_DB}.weather.forecast, tests passed")
