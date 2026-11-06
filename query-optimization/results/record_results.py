"""Record optimization results in MotherDuck and publish the results Dive.

The agent appends one JSON record per fact to the campaign ledger (`<ledger>/results.jsonl`) and
reloads the campaign into the results database after every run, so the Dive is current while the
campaign is still going. The ledger stays the source of truth: `load` can rebuild everything.

  setup  create the results database, tables, views and share; create or update the Dive
  log    append one record (campaign | experiment | run | event) and, by default, load
  load   replace the campaign's rows in the results database with the ledger's records

Records (later records with the same key replace earlier ones, field by field):

  {"type": "campaign",   "campaign_id": ..., ...}                     key: campaign_id
  {"type": "experiment", "experiment_id": ..., ...}                   key: experiment_id
  {"type": "run",        "run_id": ..., "experiment_id": ..., ...,     key: run_id
                         "steps": [{"name": ..., "started_at": ..., "finished_at": ...}]}
  {"type": "event",      "event_id": ..., "kind": ..., ...}           key: event_id

`campaign_id` defaults to the ledger's campaign. Field reference: README.md in this folder.

Credentials: an observer token with write access to the results database, read from
$MOTHERDUCK_TOKEN (override with --token-env). Never the experiment token, never in a record.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"
DIVE_SOURCE = HERE / "dive" / "optimization_results_dive.tsx"
DIVE_DEFAULT_DB = "optimization_results"  # the database name written into the Dive source
DIVE_TITLE = "Optimization results"
DIVE_URL = "https://app.motherduck.com/dives/{id}"
QUOTE_TAG = "$mdopt$"

COLUMNS = {
    "campaigns": ["campaign_id", "title", "goal", "workload_kind", "workload_ref", "source_snapshot", "region", "headline_subject",
                  "headline_size", "headline_threads", "noise_pct", "status", "agent", "envelope", "created_at", "updated_at"],
    "experiments": ["campaign_id", "experiment_id", "title", "hypothesis", "change_kind", "change_summary", "targets",
                    "status", "decision", "reason", "validation_status", "diff_path", "diff_text", "baseline_experiment_id",
                    "baseline_variant", "created_at", "updated_at"],
    "runs": ["campaign_id", "experiment_id", "run_id", "variant", "label", "subject", "scope", "duckling_size",
             "threads", "repetition", "purpose", "status", "triggered_at", "started_at", "finished_at", "wall_s",
             "server_s", "wait_s", "spill_bytes", "rows_out", "query_ids", "service_account", "databases", "command",
             "error", "notes"],
    "run_steps": ["campaign_id", "run_id", "step_name", "step_kind", "status", "started_at", "finished_at",
                  "duration_s", "rows_out", "spill_bytes", "query_ids"],
    "agent_events": ["campaign_id", "event_id", "experiment_id", "run_id", "kind", "summary", "started_at",
                     "finished_at"],
}
TIMESTAMPS = {"created_at", "updated_at", "triggered_at", "started_at", "finished_at"}
KEYS = {"campaign": "campaign_id", "experiment": "experiment_id", "run": "run_id", "event": "event_id"}
SIZES = {"pulse", "standard", "jumbo", "mega", "giga"}
RUN_STATUSES = {"succeeded", "failed", "oom", "timeout", "cancelled", "invalid"}


# ── helpers ──────────────────────────────────────────────────────────────────

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def utc(value):
    """ISO-8601 string (any offset, or naive = UTC) -> naive UTC datetime for a TIMESTAMP column."""
    if value in (None, ""):
        return None
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def connect(token_env: str):
    import duckdb  # imported here so `log --no-load` works without duckdb installed

    token = os.environ.get(token_env)
    if not token:
        sys.exit(f"Set ${token_env} to a MotherDuck token that can write the results database.")
    return duckdb.connect("md:", config={"motherduck_token": token})


def ledger_file(ledger: Path) -> Path:
    return ledger / "results.jsonl"


def default_campaign_id(ledger: Path) -> str | None:
    campaign = ledger / "campaign.json"
    if campaign.exists():
        try:
            return json.loads(campaign.read_text(encoding="utf-8")).get("campaign_id")
        except json.JSONDecodeError:
            return None
    return None


# ── reading the ledger ───────────────────────────────────────────────────────

def read_records(ledger: Path) -> list[dict]:
    path = ledger_file(ledger)
    if not path.exists():
        sys.exit(f"No results ledger at {path}; record the campaign first with `log`.")
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                sys.exit(f"{path}:{number}: not valid JSON ({error})")
    return records


def merge(records: list[dict], campaign_id: str | None) -> dict[str, dict[str, dict]]:
    """Latest record per key wins, field by field, so an agent can append partial updates."""
    merged: dict[str, dict[str, dict]] = {kind: {} for kind in KEYS}
    for record in records:
        kind = record.get("type")
        if kind not in KEYS:
            sys.exit(f"Unknown record type {kind!r}; expected one of {sorted(KEYS)}")
        record = {k: v for k, v in record.items() if k != "type"}
        record.setdefault("campaign_id", campaign_id)
        key = record.get(KEYS[kind])
        if not key:
            sys.exit(f"{kind} record without {KEYS[kind]}: {json.dumps(record)[:200]}")
        merged[kind].setdefault(key, {}).update({k: v for k, v in record.items() if v is not None})
    return merged


MAX_DIFF_CHARS = 200_000


def rows_for(merged: dict[str, dict[str, dict]], campaign_id: str, ledger: Path | None = None,
             diffs: bool = True) -> dict[str, list[tuple]]:
    def row(table, record):
        values = []
        for column in COLUMNS[table]:
            value = record.get(column)
            if column in TIMESTAMPS:
                value = utc(value)
            elif column == "envelope" and value is not None:
                value = json.dumps(value)
            elif column in ("targets", "query_ids", "databases") and isinstance(value, str):
                value = [value]
            values.append(value)
        return tuple(values)

    out: dict[str, list[tuple]] = {table: [] for table in COLUMNS}
    camps = [c for c in merged["campaign"].values() if c.get("campaign_id") == campaign_id]
    if not camps:
        sys.exit(f"No campaign record for {campaign_id!r}; log one before loading.")
    out["campaigns"] = [row("campaigns", c) for c in camps]
    for e in merged["experiment"].values():
        if e.get("campaign_id") != campaign_id:
            continue
        e = dict(e)
        if not diffs:
            e["diff_text"] = None
        elif not e.get("diff_text") and e.get("diff_path") and ledger is not None:
            path = (ledger / e["diff_path"]).resolve()
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                e["diff_text"] = text if len(text) <= MAX_DIFF_CHARS else text[:MAX_DIFF_CHARS] + "\n... (truncated)\n"
        out["experiments"].append(row("experiments", e))
    for r in merged["run"].values():
        if r.get("campaign_id") != campaign_id:
            continue
        r = dict(r)
        if r.pop("is_warmup", False):
            r.setdefault("purpose", "warmup")
        r.setdefault("purpose", "measure")
        if r.get("duckling_size"):
            r["duckling_size"] = str(r["duckling_size"]).lower()
        if r.get("status") and r["status"] not in RUN_STATUSES:
            print(f"warning: run {r['run_id']} has status {r['status']!r}; the views only count 'succeeded'",
                  file=sys.stderr)
        if r.get("wall_s") is None and r.get("started_at") and r.get("finished_at"):
            r["wall_s"] = (utc(r["finished_at"]) - utc(r["started_at"])).total_seconds()
        out["runs"].append(row("runs", r))
        for step in r.get("steps") or []:
            s = dict(step, campaign_id=campaign_id, run_id=r["run_id"], step_name=step.get("name"),
                     step_kind=step.get("kind"))
            if s.get("duration_s") is None and s.get("started_at") and s.get("finished_at"):
                s["duration_s"] = (utc(s["finished_at"]) - utc(s["started_at"])).total_seconds()
            out["run_steps"].append(row("run_steps", s))
    for e in merged["event"].values():
        if e.get("campaign_id") == campaign_id:
            out["agent_events"].append(row("agent_events", e))
    return out


# ── importers for helper outputs ─────────────────────────────────────────────

def steps_from_dbt(path: Path) -> tuple[list[dict], dict]:
    """Steps and run-level fields from a dbt target/run_results.json."""
    data = json.loads(path.read_text(encoding="utf-8"))
    steps, starts, ends = [], [], []
    for result in data.get("results", []):
        timing = {t.get("name"): t for t in result.get("timing") or []}
        start = (timing.get("compile") or timing.get("execute") or {}).get("started_at")
        end = (timing.get("execute") or timing.get("compile") or {}).get("completed_at")
        unique_id = result.get("unique_id", "")
        response = result.get("adapter_response") or {}
        steps.append({
            "name": unique_id.split(".")[-1] or unique_id,
            "kind": unique_id.split(".")[0] if "." in unique_id else "model",
            "status": result.get("status"),
            "started_at": start, "finished_at": end,
            "duration_s": result.get("execution_time"),
            "rows_out": response.get("rows_affected"),
        })
        starts += [start] if start else []
        ends += [end] if end else []
    failed = any(s["status"] not in ("success", "pass", "skipped") for s in steps)
    run = {"wall_s": data.get("elapsed_time"), "status": "failed" if failed else "succeeded",
           "started_at": min(starts) if starts else None, "finished_at": max(ends) if ends else None}
    return steps, run


def runs_from_benchmark(path: Path, base: dict, baseline_is_original: bool = False) -> list[dict]:
    """One run record per warm-up and measured run of a scripts/benchmark.py checkpoint."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for warmup, items in ((True, data.get("warmup") or []), (False, data.get("runs") or [])):
        for item in items:
            directory = path.parent / item.get("directory", "")
            detail = {}
            if (directory / "run.json").exists():
                detail = json.loads((directory / "run.json").read_text(encoding="utf-8"))
            measured = detail.get("measurement") or {}
            conditions = detail.get("conditions") or {}
            out.append({
                **base,
                "type": "run",
                "run_id": f"{base.get('experiment_id', 'exp')}-{path.parent.name}-{item.get('directory', '').replace('/', '-')}",
                "variant": "original" if baseline_is_original and item.get("variant") == "baseline" else item.get("variant"),
                "purpose": "warmup" if warmup else "measure",
                "repetition": None if warmup else int(item.get("directory", "pair-00").split("/")[0].split("-")[-1] or 0),
                "status": "succeeded" if item.get("status") == "complete" else "failed",
                "duckling_size": base.get("duckling_size") or conditions.get("duckling_size"),
                "threads": base.get("threads") or conditions.get("dbt_threads"),
                "started_at": measured.get("started_at") or detail.get("started_at"),
                "finished_at": detail.get("finished_at"),
                "wall_s": item.get("client_elapsed_seconds") or measured.get("client_elapsed_seconds"),
                "query_ids": [q for q in [item.get("query_id") or measured.get("query_id")] if q],
            })
    return out


def enrich_from_history(con, records: list[dict]) -> None:
    """Fill server_s / wait_s / spill_bytes of runs and their steps from query_history (admin tokens)."""
    steps = [st for r in records for st in (r.get("steps") or [])]
    ids = sorted({q for r in records + steps for q in (r.get("query_ids") or [])})
    if not ids:
        return
    try:
        found = con.execute(
            """SELECT query_id::VARCHAR, epoch(execution_time), epoch(wait_time), bytes_spilled_to_disk
               FROM md_information_schema.query_history WHERE query_id::VARCHAR IN (SELECT unnest(?))""",
            [ids]).fetchall()
    except Exception as error:  # noqa: BLE001 - optional enrichment
        print(f"note: query_history not readable with this token ({str(error)[:120]}); skipping enrichment",
              file=sys.stderr)
        return
    by_id = {row[0]: row[1:] for row in found}
    for r in records:
        hits = [by_id[q] for q in r.get("query_ids") or [] if q in by_id]
        if hits:
            r.setdefault("server_s", sum(h[0] or 0 for h in hits))
            r.setdefault("wait_s", sum(h[1] or 0 for h in hits))
            r.setdefault("spill_bytes", sum(h[2] or 0 for h in hits))
    for st in steps:
        hits = [by_id[q] for q in st.get("query_ids") or [] if q in by_id]
        if hits:
            st.setdefault("spill_bytes", sum(h[2] or 0 for h in hits))


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_log(args) -> int:
    ledger = Path(args.ledger)
    ledger.mkdir(parents=True, exist_ok=True)
    record = json.loads(Path(args.file).read_text(encoding="utf-8") if args.file else args.json or "{}")
    if args.type:
        record["type"] = args.type
    kind = record.get("type")
    if kind not in KEYS:
        sys.exit(f"--type (or a 'type' field) must be one of {sorted(KEYS)}")
    record.setdefault("campaign_id", args.campaign_id or default_campaign_id(ledger))
    if not record["campaign_id"]:
        sys.exit("No campaign_id: pass --campaign-id, put it in the record, or keep campaign.json in the ledger.")
    if kind == "event":
        record.setdefault("event_id", uuid.uuid4().hex[:12])
        record.setdefault("started_at", now())
    if kind in ("campaign", "experiment"):
        record.setdefault("updated_at", now())

    new = [record]
    if kind == "run" and args.dbt_run_results:
        steps, fields = steps_from_dbt(Path(args.dbt_run_results))
        record.setdefault("steps", steps)
        for key, value in fields.items():
            record.setdefault(key, value)
    if kind == "run" and args.benchmark:
        base = {k: v for k, v in record.items() if k not in ("type", "run_id")}
        new = runs_from_benchmark(Path(args.benchmark), base, args.baseline_is_original)
    if kind == "run":
        for r in new:
            if not r.get("run_id"):
                sys.exit("run records need a run_id (stable, unique within the campaign)")
            if r.get("duckling_size") and str(r["duckling_size"]).lower() not in SIZES:
                print(f"warning: unknown Duckling size {r['duckling_size']!r}", file=sys.stderr)

    con = None
    if not args.no_load:
        con = connect(args.token_env)
        if kind == "run" and not args.no_history:
            enrich_from_history(con, new)
    with ledger_file(ledger).open("a", encoding="utf-8") as handle:
        for r in new:
            handle.write(json.dumps(r, separators=(",", ":"), default=str) + "\n")
    print(f"logged {len(new)} {kind} record(s) to {ledger_file(ledger)}")
    if con is not None:
        return load(con, ledger, args.database, record["campaign_id"], not args.no_diffs)
    return 0


def load(con, ledger: Path, database: str, campaign_id: str | None, diffs: bool = True) -> int:
    campaign_id = campaign_id or default_campaign_id(ledger)
    merged = merge(read_records(ledger), campaign_id)
    if not campaign_id:
        ids = list(merged["campaign"])
        if len(ids) != 1:
            sys.exit(f"Pass --campaign-id; the ledger has {len(ids)} campaigns.")
        campaign_id = ids[0]
    rows = rows_for(merged, campaign_id, ledger, diffs)
    con.execute(f'USE "{database}"')
    # Stage rows in local temp tables (row-by-row inserts are cheap there), then copy each table to
    # MotherDuck with one statement inside one transaction.
    for table, columns in COLUMNS.items():
        con.execute(f'CREATE OR REPLACE TEMP TABLE "_stage_{table}" AS '
                    f'SELECT {", ".join(columns)} FROM "{database}".main.{table} LIMIT 0')
        if rows[table]:
            con.executemany(
                f'INSERT INTO "_stage_{table}" VALUES ({", ".join("?" * len(columns))})', rows[table])
    con.execute("BEGIN TRANSACTION")
    try:
        for table, columns in COLUMNS.items():
            con.execute(f"DELETE FROM {table} WHERE campaign_id = ?", [campaign_id])
            if rows[table]:
                con.execute(f'INSERT INTO {table} ({", ".join(columns)}) SELECT * FROM "_stage_{table}"')
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    counts = ", ".join(f"{t} {len(r)}" for t, r in rows.items())
    print(f"loaded campaign {campaign_id} into md:{database} ({counts})")
    for warning in check_decisions(con, campaign_id):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def check_decisions(con, campaign_id: str) -> list[str]:
    """Flag decisions that disagree with the measured effects (rules in README.md)."""
    found = con.execute(
        """SELECT e.experiment_id, e.status, e.decision, e.reason, e.validation_status, e.change_kind,
                  count(c.effect) FILTER (WHERE c.effect = 'improved') AS improved
           FROM experiments e LEFT JOIN v_comparisons c USING (campaign_id, experiment_id)
           WHERE e.campaign_id = ? GROUP BY ALL ORDER BY 1""", [campaign_id]).fetchall()
    out = []
    for eid, status, decision, reason, validation, kind, improved in found:
        if decision == "accepted" and validation != "passed":
            out.append(f"{eid} is accepted but validation is {validation or 'not recorded'}")
        if decision == "accepted" and not improved:
            out.append(f"{eid} is accepted but no comparison is 'improved'")
        if decision in ("rejected", "inconclusive") and not reason:
            out.append(f"{eid} is {decision} without a reason")
        if decision == "informational" and improved and kind not in ("baseline", "duckling_size", "threads"):
            out.append(f"{eid} is informational but a candidate improved; should it be accepted?")
        if (decision in (None, "pending")) and status == "complete":
            out.append(f"{eid} is complete but has no decision")
    return out


def cmd_load(args) -> int:
    return load(connect(args.token_env), Path(args.ledger), args.database, args.campaign_id, not args.no_diffs)


def ensure_share(con, database: str, access: str) -> str:
    share = f"{database}_share"
    found = con.execute("SELECT url FROM MD_INFORMATION_SCHEMA.OWNED_SHARES WHERE name = ? AND source_db_name = ?",
                        [share, database]).fetchone()
    if found:
        print(f"Using share {share}: {found[0]}")
        return str(found[0])
    visibility = "DISCOVERABLE" if access == "ORGANIZATION" else "HIDDEN"
    con.execute(f'CREATE SHARE IF NOT EXISTS "{share}" FROM "{database}" '
                f"(ACCESS {access}, VISIBILITY {visibility}, UPDATE AUTOMATIC)")
    url = con.execute("SELECT url FROM MD_INFORMATION_SCHEMA.OWNED_SHARES WHERE name = ? AND source_db_name = ?",
                      [share, database]).fetchone()[0]
    print(f"Created share {share} ({access.lower()} access, automatic updates): {url}")
    return str(url)


def dive_content(database: str, share_url: str | None) -> str:
    content = DIVE_SOURCE.read_text(encoding="utf-8")
    declared = f"{{ type: 'database', path: 'md:{DIVE_DEFAULT_DB}', alias: '{DIVE_DEFAULT_DB}' }}"
    if content.count(declared) != 1:
        sys.exit(f"Couldn't find the REQUIRED_DATABASES entry for {DIVE_DEFAULT_DB} in {DIVE_SOURCE}")
    if share_url:
        content = content.replace(declared, f"{{ type: 'share', path: '{share_url}', alias: '{DIVE_DEFAULT_DB}' }}")
    if database != DIVE_DEFAULT_DB:
        # The alias stays `optimization_results`; only the attached database changes.
        content = content.replace(f"'md:{DIVE_DEFAULT_DB}'", f"'md:{database}'")
    if QUOTE_TAG in content:
        sys.exit(f"Dive source contains the {QUOTE_TAG} quote tag")
    return content


def cmd_setup(args) -> int:
    con = connect(args.token_env)
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{args.database}"')
    con.execute(f'USE "{args.database}"')
    con.execute(SCHEMA.read_text(encoding="utf-8"))
    print(f"Schema applied to md:{args.database}")
    if args.no_share and not args.no_dive:
        print("note: without a share, only you can see the Dive's data", file=sys.stderr)
    share_url = None if args.no_share else ensure_share(con, args.database, args.share_access.upper())
    if args.no_dive:
        return 0
    content = dive_content(args.database, share_url)
    resources = (f"[{{'url': '{share_url or 'md:' + args.database}', 'alias': '{DIVE_DEFAULT_DB}'}}]")
    id_file = Path(args.dive_id_file)
    dive_id = args.dive_id or (id_file.read_text().strip() if id_file.exists() else None)
    if dive_id:
        con.execute(f"""SELECT id FROM MD_UPDATE_DIVE_CONTENT(id := '{dive_id}',
                        content := {QUOTE_TAG}{content}{QUOTE_TAG},
                        description := 'record_results.py setup', required_resources := {resources})""").fetchall()
    else:
        dive_id = str(con.execute(f"""SELECT id FROM MD_CREATE_DIVE(title := '{DIVE_TITLE}',
                        description := 'What an optimization loop tried, what it changed and what it gained',
                        content := {QUOTE_TAG}{content}{QUOTE_TAG}, required_resources := {resources})""").fetchone()[0])
        id_file.write_text(dive_id + "\n", encoding="utf-8")
    print(f"Dive: {DIVE_URL.format(id=dive_id)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default=DIVE_DEFAULT_DB, help="results database (default optimization_results)")
    parser.add_argument("--token-env", default="MOTHERDUCK_TOKEN", help="env var holding the observer token")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="create database, schema, share and Dive (idempotent)")
    p.add_argument("--share-access", choices=["organization", "unrestricted"], default="organization")
    p.add_argument("--no-share", action="store_true", help="keep the data private; the Dive reads the database")
    p.add_argument("--no-dive", action="store_true")
    p.add_argument("--dive-id", help="update this Dive instead of creating one")
    p.add_argument("--dive-id-file", default=str(HERE / ".dive_id"), help="where the Dive id is remembered")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("log", help="append a record to <ledger>/results.jsonl, then load the campaign")
    p.add_argument("--ledger", default="optimization")
    p.add_argument("--type", choices=sorted(KEYS))
    p.add_argument("--campaign-id")
    p.add_argument("--json", help="the record as a JSON object")
    p.add_argument("--file", help="read the record from a JSON file")
    p.add_argument("--dbt-run-results", help="run records: take steps, wall time and status from run_results.json")
    p.add_argument("--benchmark", help="run records: expand a scripts/benchmark.py benchmark.json into runs")
    p.add_argument("--baseline-is-original", action="store_true",
                   help="with --benchmark: the pair's baseline is the unmodified workload (variant 'original')")
    p.add_argument("--no-history", action="store_true", help="skip query_history enrichment")
    p.add_argument("--no-load", action="store_true", help="only append to the ledger")
    p.add_argument("--no-diffs", action="store_true", help="don't upload candidate diffs (they contain the workload's SQL)")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("load", help="replace the campaign's rows with the ledger's records")
    p.add_argument("--ledger", default="optimization")
    p.add_argument("--no-diffs", action="store_true", help="don't upload candidate diffs (they contain the workload's SQL)")
    p.add_argument("--campaign-id")
    p.set_defaults(func=cmd_load)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
