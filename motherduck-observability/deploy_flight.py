#!/usr/bin/env python3
"""Register or update a Flight from a local source file + requirements.txt.

Resolves the Flight by name through MD_LIST_FLIGHTS(), creating it with
MD_CREATE_FLIGHT the first time and patching it with MD_UPDATE_FLIGHT after.
No Flight id is pinned in the repo. Run it with the token of the identity that
should own the Flight (a service account, see the README).

Usage:
    export MOTHERDUCK_TOKEN=<service account token>
    # the collector, every 5 minutes
    uv run --with-requirements requirements.txt deploy_flight.py \
        --source flight.py --name md-observability-collector \
        --schedule "*/5 * * * *" --max-runtime 240 --run
    # the alert rules, two minutes after each collection
    uv run --with-requirements requirements.txt deploy_flight.py \
        --source alerts.py --name md-observability-alerts --secret slack \
        --schedule "2-59/5 * * * *" --max-runtime 120 --config DIVE_URL=https://app.motherduck.com/dives/<id> --run

Flags:
    --source FILE        Python file to deploy (default: flight.py)
    --name NAME          Flight name (default: derived from --source)
    --secret NAME        Flights secret to attach (repeatable; default: none)
    --secret-from-env NAME=VAR[,VAR...]
                         create or replace Flights secret NAME from environment variables
                         (e.g. slack=SLACK_WEBHOOK_URL, read from a local .env) and attach it
    --schedule CRON      5-field UTC cron; "" clears an existing schedule; omit to keep as is
    --config KEY=VALUE   non-secret env var for the Flight (repeatable; replaces the stored map)
    --token-name NAME    label of a MotherDuck access token to run as (default: the Flights default token)
    --max-runtime SEC    per-run timeout in seconds (0 = none)
    --instance-type SIZE runner size: F4 (default here; both Flights do their work inside
                         MotherDuck), F16 (plan default) or F32
    --run                trigger a run after deploying and stream its status + logs
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DEFAULT_NAMES = {"flight.py": "md-observability-collector", "alerts.py": "md-observability-alerts"}


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
    parser.add_argument("--source", default="flight.py", metavar="FILE")
    parser.add_argument("--name", default=None)
    parser.add_argument("--secret", action="append", default=[], metavar="NAME")
    parser.add_argument("--secret-from-env", action="append", default=[], metavar="NAME=VAR[,VAR]")
    parser.add_argument("--schedule", default=None, metavar="CRON")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--token-name", default=None)
    parser.add_argument("--max-runtime", type=int, default=None, metavar="SEC")
    parser.add_argument("--instance-type", default="F4", choices=["F4", "F16", "F32"], metavar="SIZE")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.name is None:
        args.name = DEFAULT_NAMES.get(Path(args.source).name, Path(args.source).stem)
    config: dict[str, str] = {}
    for item in args.config:
        if "=" not in item:
            parser.error(f"--config expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        config[key.strip()] = value
    args.config_map = config
    args.secrets_from_env: dict[str, list[str]] = {}
    for item in args.secret_from_env:
        if "=" not in item or not IDENTIFIER_RE.fullmatch(item.split("=", 1)[0]):
            parser.error(f"--secret-from-env expects NAME=VAR[,VAR], got {item!r}")
        name, variables = item.split("=", 1)
        args.secrets_from_env[name] = [v.strip() for v in variables.split(",") if v.strip()]
        if name not in args.secret:
            args.secret.append(name)
    return args


def create_secrets(con: duckdb.DuckDBPyConnection, secrets: dict[str, list[str]]) -> None:
    """CREATE OR REPLACE a Flights secret per entry, with values read from the environment, never printed."""
    import os
    for name, variables in secrets.items():
        missing = [v for v in variables if not os.environ.get(v)]
        if missing:
            raise SystemExit(f"--secret-from-env {name}: {', '.join(missing)} not set in the environment")
        if not all(IDENTIFIER_RE.fullmatch(v) for v in variables):
            raise SystemExit(f"--secret-from-env {name}: param names must be plain identifiers")
        params = ", ".join(f"{sql_str(v)}: {sql_str(os.environ[v])}" for v in variables)
        con.execute(f"CREATE OR REPLACE SECRET {name} IN motherduck (TYPE flights, PARAMS MAP {{{params}}})")
        print(f"secret {name} created/replaced with params {', '.join(variables)}")


def deploy(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> str:
    source = (HERE / args.source).read_text()
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
    if args.instance_type:
        fragments.append("instance_type := ?")
        params.append(args.instance_type)

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
    create_secrets(con, args.secrets_from_env)
    flight_id = deploy(con, args)
    if args.run:
        run_and_wait(con, flight_id)


if __name__ == "__main__":
    main(sys.argv[1:])
