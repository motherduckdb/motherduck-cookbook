"""Deploy the interview-insights Flight and its Dive to MotherDuck.

Resolves the Flight by name through MD_LIST_FLIGHTS() and the Dive by title
through MD_LIST_DIVES(), creating each the first time and updating it after, so
no id is pinned in the repo. The Dive gets the Flight id and the database name
written into its source on upload.

    export MOTHERDUCK_TOKEN=...
    uv run --with-requirements flight/requirements.txt deploy.py
    uv run --with-requirements flight/requirements.txt deploy.py --database my_interviews --run load

Flags:
    --database NAME      database the Flight writes and the Dive reads (default: interview_insights)
    --flight-name NAME   Flight name (default: interview-insights)
    --dive-title TITLE   Dive title (default: Interview insights)
    --config KEY=VALUE   override a Flight config default (repeatable)
    --run PHASE          after deploying, run one phase and print its logs
                         (load, describe, discover, classify, edit, reset)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
PLACEHOLDER_ID = "00000000-0000-0000-0000-000000000000"

# EDITS, CONTEXT and SUMMARY are sent by the Dive per run; they are listed so every
# key the Flight reads has a visible default.
DEFAULT_CONFIG = {
    "PHASE": "load",
    "MODEL": "gpt-6-luna",
    "SUMMARY_MODEL": "gpt-5-mini",
    "SAMPLE_PER_COHORT": "20",
    "MIN_CHARS": "80",
    "LIMIT_TURNS": "0",
    "CONF": "0.4",
    "SENTIMENT_QUESTION": "How does the participant feel about AI in their work, based on this answer?",
    "EDITS": "",
    "CONTEXT": "",
    "SUMMARY": "",
}


def sql_map(mapping: dict[str, str]) -> str:
    def s(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    return "MAP {" + ", ".join(f"{s(k)}: {s(v)}" for k, v in mapping.items()) + "}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default="interview_insights")
    parser.add_argument("--flight-name", default="interview-insights")
    parser.add_argument("--dive-title", default="Interview insights")
    parser.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--run", choices=["load", "describe", "discover", "classify", "edit", "reset"])
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.database):
        parser.error(f"--database must be a plain identifier, got {args.database!r}")
    args.config_map = {**DEFAULT_CONFIG, "DATABASE": args.database}
    for item in args.config:
        if "=" not in item:
            parser.error(f"--config expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        args.config_map[key.strip()] = value
    return args


def deploy_flight(con: duckdb.DuckDBPyConnection, args: argparse.Namespace) -> str:
    existing = con.execute(
        "SELECT flight_id FROM MD_LIST_FLIGHTS(owner_only := true) WHERE flight_name = ?", [args.flight_name]
    ).fetchall()
    if len(existing) > 1:
        raise SystemExit(f"{len(existing)} Flights are named {args.flight_name!r}; expected 0 or 1")

    fields = (
        f"name := ?, source_code := ?, requirements_txt := ?, config := {sql_map(args.config_map)}, "
        "instance_type := 'F16', max_runtime_sec := 3600"
    )
    params = [
        args.flight_name,
        (HERE / "flight" / "flight.py").read_text(),
        (HERE / "flight" / "requirements.txt").read_text(),
    ]
    if existing:
        flight_id = str(existing[0][0])
        con.execute(f"FROM MD_UPDATE_FLIGHT(flight_id := ?::UUID, {fields})", [flight_id, *params])
        print(f"updated Flight {args.flight_name} ({flight_id})")
    else:
        flight_id = str(con.execute(f"FROM MD_CREATE_FLIGHT({fields})", params).fetchone()[0])
        print(f"created Flight {args.flight_name} ({flight_id})")
    return flight_id


def deploy_dive(con: duckdb.DuckDBPyConnection, args: argparse.Namespace, flight_id: str) -> str:
    # The checked-in source reads interview_insights and carries a placeholder Flight id.
    source = (HERE / "dive" / "src" / "dive.tsx").read_text()
    if PLACEHOLDER_ID not in source:
        raise SystemExit("dive.tsx no longer contains the placeholder Flight id")
    content = source.replace(PLACEHOLDER_ID, flight_id).replace("interview_insights", args.database)
    resources = [{"url": f"md:{args.database}", "alias": args.database}]

    existing = con.execute("SELECT id FROM MD_LIST_DIVES() WHERE title = ?", [args.dive_title]).fetchall()
    if len(existing) > 1:
        raise SystemExit(f"{len(existing)} Dives are titled {args.dive_title!r}; expected 0 or 1")
    if existing:
        dive_id = str(existing[0][0])
        con.execute(
            "FROM MD_UPDATE_DIVE_CONTENT(id := ?::UUID, content := ?, required_resources := ?, api_version := 1)",
            [dive_id, content, resources],
        )
        print(f"updated Dive {args.dive_title!r} ({dive_id})")
    else:
        dive_id = str(con.execute(
            "SELECT id FROM MD_CREATE_DIVE(title := ?, content := ?, required_resources := ?, api_version := 1)",
            [args.dive_title, content, resources],
        ).fetchone()[0])
        print(f"created Dive {args.dive_title!r} ({dive_id})")
    return dive_id


def run_and_wait(con: duckdb.DuckDBPyConnection, flight_id: str, phase: str) -> None:
    row = con.execute(
        "SELECT run_number, status FROM MD_RUN_FLIGHT(flight_id := ?::UUID, config := MAP {'PHASE': ?})",
        [flight_id, phase],
    ).fetchone()
    run_number, status = int(row[0]), row[1]
    print(f"started run #{run_number} (PHASE={phase})")
    while status in ("PENDING", "RUNNING"):
        time.sleep(10)
        status = con.execute(
            "SELECT status FROM MD_GET_FLIGHT_RUN(flight_id := ?::UUID, run_number := ?)", [flight_id, run_number]
        ).fetchone()[0]
        print(f"  {status}")
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
    # The Dive attaches the database at load time, so it has to exist before the first view.
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{args.database}"')
    flight_id = deploy_flight(con, args)
    dive_id = deploy_dive(con, args, flight_id)
    print(f"https://app.motherduck.com/dives/{dive_id}")
    if args.run:
        run_and_wait(con, flight_id, args.run)


if __name__ == "__main__":
    main(sys.argv[1:])
