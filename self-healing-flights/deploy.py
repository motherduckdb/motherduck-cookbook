"""Create or update the schema, the three Flights and the Dive (all resolved by name, so it is safe to re-run).

    MOTHERDUCK_TOKEN=... uv run --with duckdb==1.5.6 --with pytz deploy.py [--no-schedules] [--reset-pipelines]

The account that runs this owns everything and must be an Admin: on its first run the healer creates the agent service
accounts and the role they share.
"""
import os
import pathlib
import sys

import duckdb

HERE = pathlib.Path(__file__).parent
REQS = "duckdb==1.5.6\npytz\nrequests\n"
SCHEDULES = "--no-schedules" not in sys.argv
HEALER_CONFIG = {
    # The recipe's own pipelines are a demo, so they run fully autonomous. Leave AUTONOMY out (or set L3) for real
    # pipelines: then every fix waits for a human to approve it in the Dive.
    "AUTONOMY": "L1",
    "AGENT_POOL_SIZE": "3",  # agent service accounts, created once as heal_agent_<owner>_1..n: the most fixes at once
    "MODEL": "~anthropic/claude-opus-latest",  # the fixing agent
    "REVIEW_MODEL": "~openai/gpt-sol-latest",  # the approver and the test-reviewer agent (another vendor on purpose)
    "BUDGET_USD": "10",  # per incident
    "DAILY_BUDGET_USD": "20",  # all fixes together, per UTC day
    "ACTION": "", "INCIDENT_ID": "",  # set per run by the Dive's Approve, Reject, Retry and test-alert buttons
}
FLIGHTS = {
    "heal-demo-weather": ("pipeline_flight.py", "0 * * * *", {"NWS_FEATURE_FLAGS": ""}, 600),
    "heal-demo-hourly": ("hourly_flight.py", "30 * * * *", {"NWS_FEATURE_FLAGS": ""}, 600),
    "heal-demo-healer": ("healer_flight.py", "*/5 * * * *", HEALER_CONFIG, 2700),
}
PIPELINES = {"heal-demo-weather", "heal-demo-hourly"}

# DuckDB prefers a lowercase motherduck_token over MOTHERDUCK_TOKEN, so refuse to guess which account to deploy to.
if os.environ.get("motherduck_token") and os.environ.get("MOTHERDUCK_TOKEN") and os.environ["motherduck_token"] != os.environ["MOTHERDUCK_TOKEN"]:
    sys.exit("motherduck_token and MOTHERDUCK_TOKEN are both set to different tokens; unset one so it's clear which account to deploy to.")
con = duckdb.connect("md:")
con.execute("SET TimeZone = 'UTC'")
for stmt in (HERE / "schema.sql").read_text().split(";\n"):
    if stmt.strip() and not all(l.strip().startswith("--") for l in stmt.strip().splitlines()):
        con.execute(stmt)
secrets = {r[0] for r in con.execute("SELECT name FROM duckdb_secrets() WHERE type = 'flights'").fetchall()}
if "openrouter" not in secrets:
    sys.exit("Create the openrouter Flight secret first (see the README).")
existing = {r[1]: (str(r[0]), r[2], r[3]) for r in con.execute(
    "SELECT flight_id, flight_name, schedule_cron, schedule_status FROM MD_LIST_FLIGHTS(owner_only := true)").fetchall()}
ids = {}
for name, (file, cron, config, max_runtime) in FLIGHTS.items():
    cron = cron if SCHEDULES else None
    healer = name not in PIPELINES
    args = dict(source_code=(HERE / file).read_text(), requirements_txt=REQS, config=config, max_runtime_sec=max_runtime,
                # Only the healer gets the LLM key (and the optional alert webhook); pipelines need no secrets.
                flight_secret_names=(["openrouter"] + (["heal_webhook"] if "heal_webhook" in secrets else [])) if healer else [])
    if name not in existing:
        if cron:
            args["schedule_cron"] = cron
        ids[name] = str(con.execute(f"SELECT flight_id FROM MD_CREATE_FLIGHT(name := ?, {', '.join(f'{k} := ?' for k in args)})", [name, *args.values()]).fetchone()[0])
        print(f"created {name}")
        continue
    fid, old_cron, status = existing[name]
    ids[name] = fid
    # Pipeline Flights keep their code and config: the healer ships fixes as new versions of them.
    if name in PIPELINES and "--reset-pipelines" not in sys.argv:
        args = {}
    # An empty schedule_cron disables a schedule; setting a cron (re-)enables it.
    if cron and (cron != old_cron or status != "ACTIVE"):
        args["schedule_cron"] = cron
    elif not cron and status == "ACTIVE":
        args["schedule_cron"] = ""
    if args:
        con.execute(f"SELECT * FROM MD_UPDATE_FLIGHT(flight_id := ?::UUID, {', '.join(f'{k} := ?' for k in args)})", [fid, *args.values()]).fetchall()
    print(f"{'updated' if 'source_code' in args else 'kept'} {name} (schedule {'on: ' + cron if cron else 'off'})")

# A pipeline that has never succeeded has no production data to clone, so run each new one once now.
for name in PIPELINES:
    if not con.execute("SELECT count(*) FROM MD_LIST_FLIGHT_RUNS(flight_id := ?::UUID)", [ids[name]]).fetchone()[0]:
        con.execute("SELECT * FROM MD_RUN_FLIGHT(flight_id := ?::UUID)", [ids[name]]).fetchall()
        print(f"started a first run of {name}")

title = "Self-healing Flights"
# The Dive's Approve / Reject buttons run the healer Flight, whose id is written into the Dive's code.
content = (HERE / "dive.tsx").read_text().replace("__HEALER_FLIGHT_ID__", ids["heal-demo-healer"])
# Dives must list their databases explicitly; the REQUIRED_DATABASES export in the code is not enough.
resources = "[{'url': 'md:heal', 'alias': 'heal_board'}]"
dive = con.execute("SELECT id FROM MD_LIST_DIVES() WHERE title = ?", [title]).fetchone()
if dive:
    con.execute(f"SELECT * FROM MD_UPDATE_DIVE_CONTENT(id := ?::UUID, content := ?, required_resources := {resources})", [str(dive[0]), content]).fetchall()
    print(f"updated Dive https://app.motherduck.com/dives/{dive[0]}")
else:
    new = con.execute(f"SELECT id FROM MD_CREATE_DIVE(title := ?, content := ?, description := ?, api_version := 1, required_resources := {resources})",
                      [title, content, "Pipelines, runs, incidents, fixes waiting for approval, alerts, and the healing DAG of each automated fix"]).fetchone()[0]
    print(f"created Dive https://app.motherduck.com/dives/{new}")
