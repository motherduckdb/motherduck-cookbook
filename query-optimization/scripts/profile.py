"""Execute one SELECT, optionally as a disposable CTAS, and save its evidence.

Use an experiment account and clone for MotherDuck. This helper does not
provision resources, change Duckling sizes, or edit a dbt project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

import duckdb


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def redact(error: BaseException, token_env: str) -> str:
    message = str(error)
    for name in {
        token_env,
        "MOTHERDUCK_TOKEN",
        "MOTHERDUCK_ADMIN_TOKEN",
        "MOTHERDUCK_OBSERVER_TOKEN",
    }:
        token = os.environ.get(name)
        if token:
            message = message.replace(token, "[redacted]")
    return re.sub(r"eyJ[A-Za-z0-9_.-]{30,}", "[redacted]", message)


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def warmup_needed(read_at: str | None) -> bool:
    if read_at is None:
        return True
    value = datetime.fromisoformat(read_at.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("--inputs-read-at must include a timezone")
    age = datetime.now(timezone.utc) - value
    if age < timedelta(0):
        raise ValueError("--inputs-read-at cannot be in the future")
    return age >= timedelta(hours=24)


def conditions(path: Path | None, remote: bool) -> dict:
    value = (
        json.loads(path.read_text(encoding="utf-8"))
        if path
        else {
            "batch_id": "local",
            "batch_concurrency": 1,
            "duckling_size": "local",
            "dbt_threads": None,
            "source_snapshot": "unspecified-local-inputs",
        }
    )
    if remote and path is None:
        raise ValueError(
            "MotherDuck runs require --conditions; see templates/conditions.json"
        )
    if not isinstance(value, dict):
        raise TypeError("conditions must be a JSON object")
    for key in ("batch_id", "batch_concurrency", "duckling_size", "source_snapshot"):
        if not value.get(key):
            raise ValueError(f"conditions require {key}")
    concurrency = value["batch_concurrency"]
    if (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or concurrency < 1
    ):
        raise ValueError("batch_concurrency must be a positive integer")
    return value


def drain(connection: duckdb.DuckDBPyConnection) -> int:
    rows = 0
    while batch := connection.fetchmany(8192):
        rows += len(batch)
    return rows


def query_id(connection: duckdb.DuckDBPyConnection, remote: bool) -> str | None:
    if not remote:
        return None
    try:
        row = connection.execute("SELECT md_last_query_id()").fetchone()
        return str(row[0]) if row else None
    except duckdb.Error:
        return None


def profile_summary(profile: dict) -> list[dict]:
    operators = []

    def visit(node: dict) -> None:
        if "operator_name" in node:
            operators.append(
                {
                    key: node.get(key)
                    for key in (
                        "operator_name",
                        "operator_type",
                        "operator_timing",
                        "operator_cardinality",
                        "operator_rows_scanned",
                        "extra_info",
                    )
                }
            )
        for child in node.get("children", []):
            visit(child)

    visit(profile)
    if not operators:
        raise ValueError(
            "profile has no operator nodes; capture a usable analyzed plan"
        )
    return sorted(
        operators, key=lambda node: node.get("operator_timing") or 0, reverse=True
    )


def run(args: argparse.Namespace) -> int:
    remote = args.database.startswith("md:")
    sql = args.sql.read_text(encoding="utf-8").strip().removesuffix(";")
    setup = args.setup.read_text(encoding="utf-8") if args.setup else None
    configuration = conditions(args.conditions, remote)
    needs_warmup = warmup_needed(args.inputs_read_at)
    credential_names = {
        args.token_env,
        "MOTHERDUCK_TOKEN",
        "MOTHERDUCK_ADMIN_TOKEN",
        "MOTHERDUCK_OBSERVER_TOKEN",
    }
    snapshot_text = (
        sql
        + (setup or "")
        + args.database
        + str(args.output)
        + json.dumps(configuration)
    )
    if any(
        os.environ.get(name) and os.environ[name] in snapshot_text
        for name in credential_names
    ):
        raise ValueError(
            "SQL, setup, or run settings contain a credential value; use environment-based authentication"
        )
    if args.keep_result and (not args.materialize or args.database == ":memory:"):
        raise ValueError(
            "--keep-result requires --materialize and a persistent experiment database"
        )
    if args.warmup_only and args.keep_result:
        raise ValueError("warm-up results cannot be retained")
    token = os.environ.get(args.token_env) if remote else None
    if remote and not token:
        raise ValueError(f"set the experiment token through {args.token_env}")
    # Parse before connecting to MotherDuck or executing any setup SQL.
    with duckdb.connect(":memory:") as parser:
        statements = parser.extract_statements(sql)
        if len(statements) != 1 or statements[0].type.name != "SELECT":
            raise ValueError(
                "--sql must contain exactly one SELECT; use --materialize for CTAS"
            )
    args.output.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    result_name = "__mdopt_" + run_id
    target = identifier("main") + "." + identifier(result_name)
    state = {
        "run_id": run_id,
        "process_id": os.getpid(),
        "status": "connecting",
        "started_at": now(),
        "database": args.database,
        "client_duckdb_version": duckdb.__version__,
        "python_version": sys.version,
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sql_sha256": hashlib.sha256((sql + ";\n").encode()).hexdigest(),
        "token_env": args.token_env if remote else None,
        "mode": args.mode,
        "conditions": configuration,
        "inputs_read_at": args.inputs_read_at,
        "warmup": {"required": needs_warmup, "status": "pending"},
        "measurement": None,
        "validation": "not_run",
        "materialize": args.materialize,
        "result_relation": None,
        "cleanup": "pending",
        "error": None,
    }
    checkpoint = args.output / "run.json"
    save(checkpoint, state)
    (args.output / "input.sql").write_text(sql + ";\n", encoding="utf-8")
    if setup:
        (args.output / "setup.sql").write_text(setup, encoding="utf-8")
    connection = None
    table_may_exist = False
    retained = False
    failed = False

    def execute(phase: str, capture: str) -> dict:
        nonlocal table_may_exist
        tag = f"/* md_opt_run:{run_id} md_opt_phase:{phase} */\n"
        statement = f"CREATE TABLE {target} AS\n{sql}" if args.materialize else sql
        statement = tag + statement
        (args.output / f"{phase}.sql").write_text(statement + ";\n", encoding="utf-8")
        state["status"] = phase
        if args.materialize:
            table_may_exist = True
        save(checkpoint, state)
        capture_path = (args.output / "profile.capture.json").resolve()
        if capture == "profiling":
            connection.execute("SET enable_profiling = 'json'")
            connection.execute("SET profiling_coverage = 'ALL'")
            connection.execute("SET profiling_output = ?", [str(capture_path)])
        started = now()
        clock = perf_counter()
        profile = None
        if capture == "explain":
            row = connection.execute(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + statement
            ).fetchone()
            if row is None or row[0] != "analyzed_plan":
                raise ValueError("EXPLAIN ANALYZE did not return an analyzed plan")
            profile = json.loads(row[1])
            result_rows = None
        else:
            result_rows = drain(connection.execute(statement))
        elapsed = perf_counter() - clock
        finished = now()
        if capture == "profiling":
            # Preserve the target profile before any metadata or cleanup statement.
            profile = json.loads(capture_path.read_text(encoding="utf-8"))
        captured_id = query_id(connection, remote)
        if capture == "profiling":
            connection.execute("PRAGMA disable_profiling")
        if profile is not None:
            operators = profile_summary(profile)
            save(args.output / "profile.json", profile)
            save(args.output / "operators.json", {"operators": operators})
        result = {
            "started_at": started,
            "finished_at": finished,
            "client_elapsed_seconds": elapsed,
            "query_id": captured_id,
            "result_rows_fetched": result_rows,
            "profile": "profile.json" if profile is not None else None,
        }
        # This is CTAS's status row, not its table cardinality. Use validation SQL
        # for row counts, outside the measurement.
        if args.materialize:
            result["result_rows_fetched"] = None
        return result

    try:
        connection = duckdb.connect(
            args.database, config={"motherduck_token": token} if remote else {}
        )
        connection.execute("PRAGMA disable_profiling")
        if setup:
            state["status"] = "setup"
            save(checkpoint, state)
            connection.execute(setup)
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        if args.materialize:
            state["result_relation"] = ".".join(
                map(identifier, (database_name, "main", result_name))
            )
        if needs_warmup or args.warmup_only:
            state["warmup"] = {"required": needs_warmup, "status": "running"}
            save(checkpoint, state)
            details = execute("warmup", "timing")
            state["warmup"].update(details, status="complete")
            save(checkpoint, state)
            if args.materialize:
                connection.execute(f"DROP TABLE {target}")
                table_may_exist = False
        else:
            state["warmup"]["status"] = "recent_read_recorded"
        if not args.warmup_only:
            state["measurement"] = execute("measure", args.mode)
            retained = args.keep_result
        state["status"] = "complete"
    except BaseException as error:  # noqa: BLE001 - persist evidence on interrupts too
        failed = True
        state.update(
            status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
            error=redact(error, args.token_env),
        )
    finally:
        if connection is not None:
            try:
                connection.execute("PRAGMA disable_profiling")
                if table_may_exist and not retained:
                    connection.execute(f"DROP TABLE IF EXISTS {target}")
                state["cleanup"] = "retained_for_validation" if retained else "complete"
            except BaseException as error:  # noqa: BLE001 - preserve failed cleanup
                failed = True
                state["cleanup"] = "failed_verify_relation"
                state["cleanup_error"] = redact(error, args.token_env)
            finally:
                try:
                    connection.close()
                except Exception as error:  # noqa: BLE001 - preserve close failures
                    failed = True
                    state["close_error"] = redact(error, args.token_env)
        else:
            state["cleanup"] = "connection_not_opened"
        state["finished_at"] = now()
        state["exit_code"] = 1 if failed else 0
        save(checkpoint, state)
    print(checkpoint)
    return state["exit_code"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        help=":memory:, local experiment file, or md:experiment_clone",
    )
    parser.add_argument("--sql", required=True, type=Path)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new directory; existing runs are never overwritten",
    )
    parser.add_argument(
        "--mode", choices=("explain", "profiling", "timing"), default="explain"
    )
    parser.add_argument(
        "--setup",
        type=Path,
        help="SQL to execute in the experiment database before warm-up",
    )
    parser.add_argument("--token-env", default="MOTHERDUCK_EXPERIMENT_TOKEN")
    parser.add_argument("--conditions", type=Path)
    parser.add_argument(
        "--inputs-read-at",
        help="UTC/offset timestamp of a verified read of ALL relevant inputs",
    )
    parser.add_argument("--warmup-only", action="store_true")
    parser.add_argument(
        "--materialize",
        action="store_true",
        help="measure a unique main.__mdopt_* CTAS; drop it afterward",
    )
    parser.add_argument(
        "--keep-result",
        action="store_true",
        help="retain the measured CTAS for validation; cleanup becomes the caller's responsibility",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, TypeError, OSError, duckdb.Error) as error:
        parser.exit(1, redact(error, args.token_env) + "\n")


if __name__ == "__main__":
    sys.exit(main())
