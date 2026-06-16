#!/usr/bin/env python3
"""Register or update the NBA Flights from their flight.toml + main.py.

Resolves each flight by name via MD_FLIGHTS() — creating it the first time and
updating it on later runs — using the MD_CREATE_FLIGHT / MD_UPDATE_FLIGHT SQL
functions. Nothing pins a flight id in the repo.

The registered source_code is the thin bootstrapper in flights/<name>/main.py,
which clones this repo and runs the real entrypoint at run time. So you only
need this command for the *first* registration (or when the bootstrapper, the
token, the schedule, config, or requirements change) — shipping new pipeline
code afterwards is just a `git push` to the branch the bootstrapper clones.

Usage:
    export MOTHERDUCK_TOKEN=<token that can manage flights>
    uv run scripts/deploy_flights.py               # deploy every flight
    uv run scripts/deploy_flights.py nba_nightly   # deploy one by name

The pinned duckdb dependency (<1.5.3) is already a MotherDuck-compatible client,
so there's no separate CLI version to manage.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import duckdb

FLIGHTS_DIR = Path(__file__).resolve().parent.parent / "flights"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_list(values: list[str]) -> str:
    if not values:
        return "[]::VARCHAR[]"
    return "[" + ", ".join(_sql_str(v) for v in values) + "]"


def _sql_map(mapping: dict[str, str]) -> str:
    if not mapping:
        return "MAP {}::MAP(VARCHAR, VARCHAR)"
    entries = ", ".join(f"{_sql_str(k)}: {_sql_str(v)}" for k, v in mapping.items())
    return "MAP {" + entries + "}"


def load_flight(flight_dir: Path) -> dict:
    """Read a flight's flight.toml + main.py into the deploy arguments."""
    cfg = tomllib.loads((flight_dir / "flight.toml").read_text())["flight"]
    return {
        "name": cfg["name"],
        "source_code": (flight_dir / "main.py").read_text(),
        "requirements_txt": "\n".join(cfg.get("extra_requirements", [])),
        "access_token_name": cfg["md_token_name"],
        "flight_secret_names": cfg.get("md_secret_names", []),
        "config": cfg.get("config", {}),
        # Empty / absent means an on-demand-only flight (no schedule).
        "schedule_cron": cfg.get("schedule_cron", ""),
    }


def _build_args(flight: dict) -> tuple[list[str], list[object]]:
    """Return (named-arg fragments, bind params) for a flight.

    Big strings bind as `?` placeholders; the MAP/LIST literals are inlined from
    our own toml (vetted, single-quote-escaped). schedule_cron is omitted when
    empty so the flight is registered as on-demand-only.
    """
    fragments = [
        "name := ?",
        "source_code := ?",
        "requirements_txt := ?",
        "access_token_name := ?",
        f"flight_secret_names := {_sql_list(flight['flight_secret_names'])}",
        f"config := {_sql_map(flight['config'])}",
    ]
    params: list[object] = [
        flight["name"],
        flight["source_code"],
        flight["requirements_txt"],
        flight["access_token_name"],
    ]
    if flight["schedule_cron"]:
        fragments.append("schedule_cron := ?")
        params.append(flight["schedule_cron"])
    return fragments, params


def deploy(con: duckdb.DuckDBPyConnection, flight: dict) -> None:
    existing = con.execute(
        "SELECT flight_id FROM MD_FLIGHTS() WHERE flight_name = ?", [flight["name"]]
    ).fetchall()
    if len(existing) > 1:
        raise SystemExit(
            f"{flight['name']}: found {len(existing)} flights with this name; expected 0 or 1"
        )

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
    flight_dirs = sorted(
        d for d in FLIGHTS_DIR.iterdir() if (d / "flight.toml").exists()
    )
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
