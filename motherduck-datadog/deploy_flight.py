#!/usr/bin/env python3
"""Register or update the Datadog exporter Flight from flight.py + requirements.txt.

Resolves the Flight by name through MD_LIST_FLIGHTS(), creating it with
MD_CREATE_FLIGHT the first time and patching it with MD_UPDATE_FLIGHT after.
No Flight id is pinned in the repo.

Usage:
    export MOTHERDUCK_TOKEN=<token that can manage Flights>
    uv run --with-requirements requirements.txt deploy_flight.py \
        --secret datadog --schedule "*/5 * * * *" --config DD_SITE=datadoghq.com

    # smoke test without a Datadog key, then watch the run finish:
    uv run --with-requirements requirements.txt deploy_flight.py --config DRY_RUN=true --run

Flags:
    --name NAME          Flight name (default: datadog-exporter)
    --secret NAME        Flights secret holding DD_API_KEY (repeatable; default: none)
    --schedule CRON      5-field UTC cron; "" clears an existing schedule; omit to keep as is
    --config KEY=VALUE   non-secret env var for the Flight (repeatable; replaces the stored map)
    --token-name NAME    MotherDuck access token label to attach (default: the Flights default token)
    --max-runtime SEC    per-run timeout in seconds (0 = none)
    --run                trigger a run after deploying and stream its status + logs
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_list(values: list[str]) -> str:
    return "[" + ", ".join(sql_str(v) for v in values) + "]::VARCHAR[]"


def sql_map(mapping: dict[str, str]) -> str:
    if not mapping:
        return "MAP {}::MAP(VARCHAR, VARCHAR)"
    return "MAP {" + ", ".join(f"{sql_str(k)}: {sql_str(v)}" for k, v in mapping.items()) + "}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="datadog-exporter")
    parser.add_argument("--secret", action="append", default=[], metavar="NAME")
    parser.add_argument("--schedule", default=None, metavar="CRON")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--token-name", default=None)
    parser.add_argument("--max-runtime", type=int, default=None, metavar="SEC")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    config: dict[str, str] = {}
    for item in args.config:
        if "=" not in item:
            parser.error(f"--config expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        config[key.strip()] = value
    args.config_map = config
    return args


def deploy(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> str:
    source = (HERE / "flight.py").read_text()
    requirements = (HERE / "requirements.txt").read_text()

    existing = con.execute(
        "SELECT flight_id FROM MD_LIST_FLIGHTS(owner_only := true) WHERE flight_name = ?", [args.name]
    ).fetchall()
    if len(existing) > 1:
        raise SystemExit(f"{args.name}: {len(existing)} Flights share this name; expected 0 or 1")

    # Large strings bind as parameters; MAP/LIST literals are built from our
    # own, single-quote-escaped CLI values.
    fragments = ["name := ?", "source_code := ?", "requirements_txt := ?",
                 f"flight_secret_names := {sql_list(args.secret)}",
                 f"config := {sql_map(args.config_map)}"]
    params: list[object] = [args.name, source, requirements]
    if args.schedule is not None and (args.schedule or existing):
        fragments.append("schedule_cron := ?")
        params.append(args.schedule)
    if args.token_name:
        fragments.append("access_token_name := ?")
        params.append(args.token_name)
    if args.max_runtime is not None:
        fragments.append(f"max_runtime_sec := {int(args.max_runtime)}")

    if existing:
        flight_id = str(existing[0][0])
        con.execute(f"FROM MD_UPDATE_FLIGHT(flight_id := ?::UUID, {', '.join(fragments)})", [flight_id, *params])
        print(f"updated {args.name} ({flight_id})")
    else:
        row = con.execute(f"FROM MD_CREATE_FLIGHT({', '.join(fragments)})", params).fetchone()
        flight_id = str(row[0])
        print(f"created {args.name} ({flight_id})")
    return flight_id


def run_and_wait(con: duckdb.DuckDBPyConnection, flight_id: str) -> None:
    row = con.execute("FROM MD_RUN_FLIGHT(flight_id := ?::UUID)", [flight_id]).fetchone()
    columns = [d[0] for d in con.description]
    run = dict(zip(columns, row))
    run_number = int(run["run_number"])
    print(f"started run #{run_number} ({run.get('status')})")
    status = run.get("status")
    while status in ("PENDING", "RUNNING"):
        time.sleep(5)
        row = con.execute(
            "FROM MD_GET_FLIGHT_RUN(flight_id := ?::UUID, run_number := ?)", [flight_id, run_number]
        ).fetchone()
        status = dict(zip([d[0] for d in con.description], row))["status"]
        print(f"  {status}")
    print("--- logs ---")
    for (line,) in con.execute(
        "SELECT line FROM MD_GET_FLIGHT_LOGS(flight_id := ?::UUID, run_number := ?, \"order\" := 'asc', \"limit\" := 400)",
        [flight_id, run_number],
    ).fetchall():
        print(line)
    if status != "SUCCEEDED":
        raise SystemExit(f"run #{run_number} ended with status {status}")


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    con = duckdb.connect("md:")
    flight_id = deploy(con, args)
    if args.run:
        run_and_wait(con, flight_id)


if __name__ == "__main__":
    main(sys.argv[1:])
