#!/usr/bin/env python3
"""Register or update the StatsBomb Flights from their flight.toml + main.py.

Resolves each flight by name via MD_FLIGHTS() -- creating it the first time and
updating it on later runs -- using the MD_CREATE_FLIGHT / MD_UPDATE_FLIGHT SQL
functions, so nothing in the repo pins a flight id. The full single-file
entrypoint in flights/<name>/main.py is registered as the flight's source_code.

Usage:
    export MOTHERDUCK_TOKEN=<token with read+write on your target database>
    uv run scripts/deploy_flights.py                   # deploy all three
    uv run scripts/deploy_flights.py statsbomb-marts   # deploy one by name

Then run them in order from the MotherDuck UI (or with MD_RUN_FLIGHT):
    statsbomb-raw-load -> statsbomb-core-transform -> statsbomb-marts

The Flight runtime injects a MotherDuck token automatically, so no token name is
registered here. Pin duckdb to 1.5.3.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import duckdb

FLIGHTS_DIR = Path(__file__).resolve().parent.parent / "flights"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_map(mapping: dict) -> str:
    if not mapping:
        return "MAP {}::MAP(VARCHAR, VARCHAR)"
    entries = ", ".join(f"{_sql_str(k)}: {_sql_str(str(v))}" for k, v in mapping.items())
    return "MAP {" + entries + "}"


def load_flight(flight_dir: Path) -> dict:
    """Read a flight's flight.toml + main.py into the deploy arguments."""
    cfg = tomllib.loads((flight_dir / "flight.toml").read_text())["flight"]
    return {
        "name": cfg["name"],
        "source_code": (flight_dir / "main.py").read_text(),
        "requirements_txt": "\n".join(cfg.get("extra_requirements", [])),
        "config": cfg.get("config", {}),
        # Empty / absent means an on-demand-only flight (no schedule).
        "schedule_cron": cfg.get("schedule_cron", ""),
    }


def _build_args(flight: dict) -> tuple[list[str], list[object]]:
    """Big strings bind as `?` placeholders; the MAP literal is inlined from our
    own vetted, single-quote-escaped toml. schedule_cron is omitted when empty
    so the flight registers as on-demand-only."""
    fragments = [
        "name := ?",
        "source_code := ?",
        "requirements_txt := ?",
        f"config := {_sql_map(flight['config'])}",
    ]
    params: list[object] = [flight["name"], flight["source_code"], flight["requirements_txt"]]
    if flight["schedule_cron"]:
        fragments.append("schedule_cron := ?")
        params.append(flight["schedule_cron"])
    return fragments, params


def deploy(con: duckdb.DuckDBPyConnection, flight: dict) -> None:
    existing = con.execute(
        "SELECT flight_id FROM MD_FLIGHTS() WHERE flight_name = ?", [flight["name"]]
    ).fetchall()
    if len(existing) > 1:
        raise SystemExit(f"{flight['name']}: {len(existing)} flights with this name; expected 0 or 1")

    fragments, params = _build_args(flight)
    if existing:
        flight_id = existing[0][0]
        con.execute(
            f"FROM MD_UPDATE_FLIGHT(flight_id := ?::UUID, {', '.join(fragments)})",
            [flight_id, *params],
        )
        print(f"updated {flight['name']} ({flight_id})")
    else:
        con.execute(f"FROM MD_CREATE_FLIGHT({', '.join(fragments)})", params)
        print(f"created {flight['name']}")


def main(argv: list[str]) -> None:
    wanted = set(argv)
    flight_dirs = sorted(d for d in FLIGHTS_DIR.iterdir() if (d / "flight.toml").exists())
    if wanted:
        unknown = wanted - {d.name for d in flight_dirs}
        if unknown:
            raise SystemExit(f"unknown flight(s): {', '.join(sorted(unknown))}")
        flight_dirs = [d for d in flight_dirs if d.name in wanted]

    con = duckdb.connect("md:")
    for flight_dir in flight_dirs:
        deploy(con, load_flight(flight_dir))


if __name__ == "__main__":
    main(sys.argv[1:])
