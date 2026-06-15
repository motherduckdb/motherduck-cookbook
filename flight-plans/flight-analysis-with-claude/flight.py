"""
Borough briefs — MotherDuck Flight (Claude Agent SDK, parallel fan-out).

One run discovers each NYC borough present in the public 311 service-request
sample, then fans out one Claude Agent SDK agent per borough (bounded
concurrency) to produce a "notable things" brief of recent activity. Each brief
is written to `flights_demo.main.borough_briefs`; the run logs a batch summary.
A single borough's failure does not abort the batch.

This is the agentic-analysis pattern: instead of hand-writing the SQL for each
brief, the run hands a borough to a Claude agent that explores the warehouse and
grounds every claim in real data. The agent's ONLY capability is a single
in-process, READ-ONLY `query` tool defined here with the Claude Agent SDK
(`create_sdk_mcp_server` + `@tool`). It cannot shell out, read files, or write to
the warehouse — the tool rejects anything that is not a read-only statement.

Why an in-process tool and not Bash or a remote MCP server: the agent already
runs inside MotherDuck compute with `MOTHERDUCK_TOKEN` in its environment, so a
remote MCP server adds a network dependency without adding capability, and raw
Bash would give the agent an unrestricted shell and a write-capable token. A
scoped read-only tool is the least-privilege option for an unattended job.

Agent concurrency: each `query()` spawns its own bundled-CLI subprocess, so
simultaneous agents are capped by a semaphore (CONCURRENCY) to fit the
2-CPU / 16 GB Flight runtime and stay under Anthropic API rate limits.

The sample data is a frozen snapshot (it ends in 2023), so the lookback window
is anchored to MAX(created_date) in the table, not to now(). Against a live
warehouse you would anchor to now() instead.

Runtime inputs:
  ANTHROPIC_API_KEY  - remapped from a Flights secret (any `*_ANTHROPIC_API_KEY`)
  MOTHERDUCK_TOKEN   - auto-injected by the Flights runtime
  BRIEF_WINDOW_DAYS  - lookback window in days (default "7")
  CONCURRENCY        - max simultaneous agents (default "3")
  MODEL              - Claude model id (default "claude-opus-4-8")
  MAX_BOROUGHS       - cap borough count for testing; "0" = all (default "0")
  BOROUGHS           - optional comma-separated override; skips discovery
"""

import asyncio
import os
import re
import sys
import time
from datetime import datetime, timedelta

import duckdb
from claude_agent_sdk import query, ClaudeAgentOptions, create_sdk_mcp_server, tool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# duckdb.connect("md:") authenticates with MOTHERDUCK_TOKEN from the environment
# (auto-injected by the Flights runtime). Fail fast and clearly if it is missing.
if not os.environ.get("MOTHERDUCK_TOKEN", "").strip():
    raise SystemExit("MOTHERDUCK_TOKEN is required (the Flights runtime injects it).")

WINDOW_DAYS = max(1, int(os.environ.get("BRIEF_WINDOW_DAYS", "7")))
CONCURRENCY = max(1, int(os.environ.get("CONCURRENCY", "3")))
MODEL = os.environ.get("MODEL", "claude-opus-4-8").strip()
MAX_BOROUGHS = int(os.environ.get("MAX_BOROUGHS", "0"))
BOROUGHS_OVERRIDE = os.environ.get("BOROUGHS", "").strip()

SOURCE_TABLE = "sample_data.nyc.service_requests"
RESULTS_TABLE = "flights_demo.main.borough_briefs"

# The bundled CLI wants a writable HOME; /tmp is the Flight scratch disk.
os.environ.setdefault("HOME", "/tmp")
# The Flight container runs as root; the bundled Claude Code CLI guards against
# running as root. IS_SANDBOX=1 tells it the environment is already sandboxed.
os.environ["IS_SANDBOX"] = "1"


def resolve_anthropic_key() -> None:
    # A local run sets ANTHROPIC_API_KEY directly. Deployed as a Flight, the key
    # comes from a `TYPE flights` secret, and MotherDuck injects each secret
    # param under the env var `<secret_name>_<PARAM>`, NOT the bare param name.
    # So a secret named `claude` with an `ANTHROPIC_API_KEY` param arrives as
    # `claude_ANTHROPIC_API_KEY`. Accept the exact name first (local), then any
    # var ending in the suffix (the secret, whatever you named it), and finally
    # fall back to matching by the `sk-ant-` key shape.
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return
    for key, value in os.environ.items():
        if key.endswith("_ANTHROPIC_API_KEY") and value.strip():
            os.environ["ANTHROPIC_API_KEY"] = value.strip()
            log(f"Mapped ANTHROPIC_API_KEY from secret env var {key!r}")
            return
    for key, value in os.environ.items():
        if isinstance(value, str) and value.startswith("sk-ant-"):
            os.environ["ANTHROPIC_API_KEY"] = value
            log(f"Mapped ANTHROPIC_API_KEY from env var {key!r}")
            return
    log("WARNING: no ANTHROPIC_API_KEY found in the environment.")


resolve_anthropic_key()


# ---- The agent's only tool: a single in-process, read-only SQL query ---------
# Read-only is enforced in code, not just asked for in the prompt: the statement
# must begin with a read-only keyword, be a single statement, and contain no
# write/DDL keyword. This is defense-in-depth — the strongest guarantee is to
# give the Flight a read-scoped MotherDuck token (see the README "Security"
# section), so even a bug here cannot mutate data.
READONLY_FIRST_KEYWORDS = (
    "select", "with", "from", "describe", "desc", "show", "summarize",
    "explain", "pragma", "table", "values",
)
FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|install|"
    r"load|replace|truncate|grant|revoke|export|import|checkpoint|vacuum|call)\b",
    re.IGNORECASE,
)
MAX_RESULT_ROWS = 200


def check_read_only(sql: str) -> tuple[bool, str]:
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return False, "empty query"
    # One statement only: reject anything with an embedded ';'.
    if ";" in stripped:
        return False, "only a single statement is allowed"
    first = re.match(r"[a-zA-Z]+", stripped)
    if not first or first.group(0).lower() not in READONLY_FIRST_KEYWORDS:
        return False, "query must start with a read-only keyword (SELECT, WITH, DESCRIBE, ...)"
    hit = FORBIDDEN_KEYWORD_RE.search(stripped)
    if hit:
        return False, f"write/DDL keyword not allowed: {hit.group(0).upper()}"
    return True, ""


def run_sql_blocking(sql: str) -> str:
    # A fresh connection per call keeps concurrent agents isolated; we run this
    # under asyncio.to_thread so a blocking query does not stall the event loop.
    con = duckdb.connect("md:")
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    finally:
        con.close()
    if not rows:
        return "(0 rows)"
    lines = [" | ".join(columns)]
    for row in rows[:MAX_RESULT_ROWS]:
        lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
    if len(rows) > MAX_RESULT_ROWS:
        lines.append(f"... ({len(rows) - MAX_RESULT_ROWS} more rows truncated)")
    return "\n".join(lines)


@tool(
    "query",
    "Run a single READ-ONLY SQL statement against MotherDuck (DuckDB SQL) and "
    "return the result rows as text. Only SELECT/WITH/DESCRIBE/SHOW/SUMMARIZE/"
    "EXPLAIN-style statements are allowed; writes and DDL are rejected.",
    {"sql": str},
)
async def query_tool(args: dict) -> dict:
    sql = (args or {}).get("sql", "")
    ok, reason = check_read_only(sql)
    if not ok:
        return {"content": [{"type": "text", "text": f"ERROR: query rejected ({reason})."}]}
    try:
        text = await asyncio.to_thread(run_sql_blocking, sql)
    except Exception as e:  # surface the DB error to the agent so it can adjust
        return {"content": [{"type": "text", "text": f"ERROR: {e}"}]}
    return {"content": [{"type": "text", "text": text}]}


# Bundling the tool into an in-process SDK MCP server named "motherduck" makes it
# addressable to the agent as `mcp__motherduck__query`.
QUERY_SERVER = create_sdk_mcp_server(name="motherduck", version="1.0.0", tools=[query_tool])


def get_anchor(con: duckdb.DuckDBPyConnection) -> datetime:
    # The sample is frozen, so "recent" is measured from the newest row, not
    # today. Against a live table you would use now() instead.
    return con.execute(f"SELECT max(created_date) FROM {SOURCE_TABLE}").fetchone()[0]


def discover_boroughs(con: duckdb.DuckDBPyConnection, window_start: datetime,
                      anchor: datetime) -> list:
    if BOROUGHS_OVERRIDE:
        boroughs = [b.strip().upper() for b in BOROUGHS_OVERRIDE.split(",") if b.strip()]
        log(f"Using BOROUGHS override: {boroughs}")
    else:
        # Real boroughs only, busiest first. 'Unspecified' is a catch-all bucket
        # with no geography, the analog of excluding internal/test accounts.
        rows = con.execute(
            f"""
            SELECT borough, count(*) AS requests
            FROM {SOURCE_TABLE}
            WHERE created_date BETWEEN ? AND ?
              AND borough IS NOT NULL
              AND borough <> 'Unspecified'
            GROUP BY borough
            ORDER BY requests DESC
            """,
            [window_start, anchor],
        ).fetchall()
        boroughs = [r[0] for r in rows]
        log(f"Discovered {len(boroughs)} boroughs from {SOURCE_TABLE}")
    if MAX_BOROUGHS > 0:
        boroughs = boroughs[:MAX_BOROUGHS]
        log(f"MAX_BOROUGHS={MAX_BOROUGHS}; limiting to {len(boroughs)} boroughs")
    return boroughs


def build_prompt(borough: str, window_start: datetime, anchor: datetime) -> str:
    start_str = window_start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = anchor.strftime("%Y-%m-%d %H:%M:%S")
    return f"""\
You are preparing a brief of *notable things* in NYC 311 service requests for the
borough: {borough}.

You have one tool: `query`, which runs a single READ-ONLY SQL statement (DuckDB
SQL) against MotherDuck and returns the result rows. Use it to explore the
warehouse and ground every claim in real data. It only accepts read-only
statements (SELECT / WITH / DESCRIBE / SHOW / SUMMARIZE / EXPLAIN); writes and
DDL are rejected, so do not attempt them. Run one statement per call.

The data lives in the table `{SOURCE_TABLE}` (one row per 311 request). It is a
frozen snapshot, so your analysis window is fixed to the most recent
{WINDOW_DAYS} days present in the data:
  created_date from {start_str} to {end_str} (inclusive).
Filter on `borough = '{borough}'` and `created_date BETWEEN '{start_str}' AND
'{end_str}'` for every query about this window.

Steps:
1. DESCRIBE the table first to confirm the real column names before
   querying — do not assume them. Useful columns include created_date,
   closed_date, agency, agency_name, complaint_type, descriptor, status,
   incident_zip, community_board, open_data_channel_type, borough.
2. Profile this borough's activity in the window: total requests, the busiest
   complaint types and agencies, the open/closed status mix, and how request
   volume moved day over day inside the window.
3. Decide what is NOTABLE over the window: complaint-type or descriptor spikes,
   agencies with unusually slow or aging open requests (compare closed_date /
   resolution timing), zip or community-board hotspots, shifts in how requests
   arrive (open_data_channel_type), and similar.

Guardrails:
- Separate evidence from inference; never invent numbers. Every figure must come
  from a query you ran.
- If a query returns nothing, say so rather than guessing.

Output: a concise markdown brief, ranked by importance, one short section per
notable finding, led by a 2-3 sentence "Top of mind" summary. If nothing is
notable, say so plainly. Output ONLY the brief as your final message — it is
stored verbatim.
"""


async def generate_brief(borough: str, window_start: datetime, anchor: datetime) -> str:
    options = ClaudeAgentOptions(
        model=MODEL,
        mcp_servers={"motherduck": QUERY_SERVER},
        # The agent's ONLY capability is the in-process read-only `query` tool.
        # `tools=[]` disables every built-in tool (no Bash, Read, Write, ...), so
        # the agent cannot shell out or touch the filesystem; `allowed_tools`
        # auto-approves just our query tool; `dontAsk` denies anything not
        # pre-approved without ever prompting (this runs headless);
        # `setting_sources=[]` ignores any local Claude settings.
        tools=[],
        allowed_tools=["mcp__motherduck__query"],
        permission_mode="dontAsk",
        setting_sources=[],
    )
    final_text = ""
    async for message in query(prompt=build_prompt(borough, window_start, anchor),
                               options=options):
        result = getattr(message, "result", None)
        if result:
            final_text = result
    return final_text


def persist(borough: str, anchor: datetime, brief_md: str) -> None:
    con = duckdb.connect("md:")
    con.execute("CREATE DATABASE IF NOT EXISTS flights_demo")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RESULTS_TABLE} (
            run_ts      TIMESTAMPTZ,
            borough     VARCHAR,
            window_days INTEGER,
            window_end  TIMESTAMP,
            brief_md    VARCHAR
        )
        """
    )
    con.execute(
        f"INSERT INTO {RESULTS_TABLE} VALUES (now(), ?, ?, ?, ?)",
        [borough, WINDOW_DAYS, anchor, brief_md],
    )
    con.close()


async def run_one(borough: str, window_start: datetime, anchor: datetime,
                  sem: asyncio.Semaphore) -> tuple:
    async with sem:
        t = time.time()
        try:
            brief = await generate_brief(borough, window_start, anchor)
            if not brief.strip():
                log(f"[{borough}] EMPTY brief")
                return (borough, "empty", time.time() - t)
            persist(borough, anchor, brief)
            dt = time.time() - t
            log(f"[{borough}] ok ({len(brief)} chars, {dt:.0f}s)")
            return (borough, "ok", dt)
        except Exception as e:  # one borough's failure must not abort the batch
            log(f"[{borough}] ERROR: {e!r}")
            return (borough, "error", time.time() - t)


async def run_batch(boroughs: list, window_start: datetime, anchor: datetime) -> list:
    sem = asyncio.Semaphore(CONCURRENCY)
    return await asyncio.gather(*[run_one(b, window_start, anchor, sem) for b in boroughs])


def main() -> None:
    t0 = time.time()
    con = duckdb.connect("md:")
    anchor = get_anchor(con)
    window_start = anchor - timedelta(days=WINDOW_DAYS)
    boroughs = discover_boroughs(con, window_start, anchor)
    con.close()

    if not boroughs:
        log("No boroughs to brief; exiting.")
        return
    log(
        f"Briefing {len(boroughs)} boroughs at concurrency {CONCURRENCY}, "
        f"model {MODEL}, window {window_start:%Y-%m-%d}..{anchor:%Y-%m-%d} ..."
    )
    results = asyncio.run(run_batch(boroughs, window_start, anchor))

    ok = [r for r in results if r[1] == "ok"]
    bad = [r for r in results if r[1] != "ok"]
    log("---- BATCH SUMMARY ----")
    log(f"ok={len(ok)} failed/empty={len(bad)} total={len(results)} wall={time.time() - t0:.0f}s")
    for borough, status, dt in sorted(results, key=lambda r: (r[1] != "ok", r[0])):
        log(f"  {status:6} {borough} ({dt:.0f}s)")
    if bad:
        log(f"WARNING: {len(bad)} borough(s) did not produce a brief: {[r[0] for r in bad]}")


if __name__ == "__main__":
    main()
