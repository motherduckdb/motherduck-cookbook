"""Pipeline Flight: load the next 24 hours of NWS hourly temperatures into heal_demo.weather.hourly."""
import json
import os

import duckdb
import requests

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
    con.execute("CREATE DATABASE IF NOT EXISTS heal_demo")
    con.execute("CREATE SCHEMA IF NOT EXISTS heal_demo.weather")
    con.execute("CREATE OR REPLACE TEMP TABLE stg (station VARCHAR, hour TIMESTAMPTZ, temp_f DOUBLE)")
    con.executemany("INSERT INTO stg VALUES (?, ?, ?)", hours)
    con.execute("CREATE OR REPLACE TABLE heal_demo.weather.hourly AS SELECT *, now() AS loaded_at FROM stg")
    print(f"hourly: loaded {len(hours)} hours", flush=True)
except Exception as e:
    # One line the healer reads from the run's log. That is the pipeline's whole contract with the healer.
    print(f"HEAL_EVENT {json.dumps({'error_type': type(e).__name__, 'message': f'{type(e).__name__}: {e}'[:2000]})}", flush=True)
    raise
