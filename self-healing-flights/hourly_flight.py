"""Pipeline Flight: load the next 24 hours of NWS hourly temperatures into heal_demo.weather.hourly."""
import json
import os

import duckdb
import requests

TARGET_DB = os.environ.get("TARGET_DB", "heal_demo")  # the fixer points this at a sandbox clone
SANDBOX = os.environ.get("HEAL_SANDBOX") == "1"
STATIONS = {"seattle": "SEW/125,68", "denver": "BOU/62,60"}
HEADERS = {"User-Agent": "motherduck-self-healing-demo/1.0 (https://motherduck.com)"}
# Simulates the NWS rolling out a new payload shape (set NWS_FEATURE_FLAGS=forecast_temperature_qv in the Flight config).
if os.environ.get("NWS_FEATURE_FLAGS"):
    HEADERS["Feature-Flags"] = os.environ["NWS_FEATURE_FLAGS"]

con = duckdb.connect("md:")
con.execute("SET TimeZone = 'UTC'")
try:
    hours = []
    for station, grid in STATIONS.items():
        periods = requests.get(f"https://api.weather.gov/gridpoints/{grid}/forecast/hourly", headers=HEADERS, timeout=30).json()["properties"]["periods"]
        for p in periods[:24]:
            temp_f = float(p["temperature"]) if p["temperatureUnit"] == "F" else float(p["temperature"]) * 9 / 5 + 32
            hours.append((station, p["startTime"], round(temp_f, 1)))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {TARGET_DB}.weather")
    con.execute(f"CREATE OR REPLACE TABLE {TARGET_DB}.weather.hourly (station VARCHAR, hour TIMESTAMPTZ, temp_f DOUBLE)")
    con.executemany(f"INSERT INTO {TARGET_DB}.weather.hourly VALUES (?, ?, ?)", hours)
    print(f"hourly: loaded {len(hours)} hours", flush=True)
except Exception as e:
    # Tell the checker what went wrong (sandbox runs stay quiet so they never look like real failures).
    msg = f"{type(e).__name__}: {e}"
    print(f"HEAL_EVENT {json.dumps({'error_type': type(e).__name__, 'message': msg})}", flush=True)
    if not SANDBOX:
        con.execute("""INSERT INTO heal.main.pipeline_events (pipeline, flight_id, run_number, severity, error_type, message)
                       VALUES ('hourly', ?::UUID, ?, 'error', ?, ?)""",
                    [os.environ.get("MOTHERDUCK_FLIGHT_ID"), os.environ.get("MOTHERDUCK_FLIGHT_RUN_NUMBER"), type(e).__name__, msg[:2000]])
    raise
