"""
Borough briefs — MotherDuck Flight (Claude Agent SDK, parallel fan-out).

One run discovers each NYC borough present in the public 311 service-request
sample, then fans out one Claude Agent SDK agent per borough (bounded
concurrency) to produce a "notable things" brief of recent activity. Each brief
is written to `flights_demo.main.borough_briefs`; the run logs a batch summary.
A single borough's failure does not abort the batch.

This is the agentic-analysis pattern: instead of hand-writing the SQL for each
brief, the run hands a borough to a Claude agent that explores the warehouse and
grounds every claim in real data. The agent's tools are the **read-only** tools
of the hosted MotherDuck MCP server (`query`, `list_tables`, `list_columns`,
`search_catalog`, `query_context_layer`, ...), exposed to the agent as in-process
SDK tools. It cannot shell out, read files, or run any write/DDL tool.

Why mirror the hosted MCP server through in-process tools instead of pointing the
agent's SDK at the remote MCP server directly: the Agent SDK's bundled CLI cannot
talk to the hosted endpoint (its HTTP-MCP client fails with "Connection closed"),
but it *does* run in-process SDK tools reliably. So a tiny JSON-RPC client here
(`MotherDuckMCPClient`) calls the hosted server over HTTP — which works fine from
plain Python — and each hosted read-only tool is wrapped as an in-process tool
that forwards to it. The hosted tool's own JSON Schema is reused verbatim, so new
read-only tools the server adds appear automatically with no code change. See the
README "Caveats" for the full story.

Agent concurrency: each `query()` spawns its own bundled-CLI subprocess, so
simultaneous agents are capped by a semaphore (CONCURRENCY) to fit the
2-CPU / 16 GB Flight runtime and stay under Anthropic API rate limits. The MCP
client is stateless (independent POSTs), so all agents share one safely.

The sample data is a frozen snapshot (it ends in 2023), so the lookback window
is anchored to MAX(created_date) in the table, not to now(). Against a live
warehouse you would anchor to now() instead.

Runtime inputs:
  ANTHROPIC_API_KEY  - remapped from a Flights secret (any `*_ANTHROPIC_API_KEY`)
  MOTHERDUCK_TOKEN   - auto-injected by the Flights runtime; used for both the
                       duckdb infra calls and the hosted MCP server (the injected
                       token is a PAT, which the MCP server accepts)
  MD_MCP_URL         - hosted MCP endpoint (default the MotherDuck public server)
  BRIEF_WINDOW_DAYS  - lookback window in days (default "7")
  CONCURRENCY        - max simultaneous agents (default "3")
  MODEL              - Claude model id (default "claude-opus-4-8")
  MAX_BOROUGHS       - cap borough count for testing; "0" = all (default "0")
  BOROUGHS           - optional comma-separated override; skips discovery
"""

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import duckdb
import requests
from claude_agent_sdk import query, ClaudeAgentOptions, create_sdk_mcp_server, tool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# duckdb.connect("md:") and the hosted MCP server both authenticate with
# MOTHERDUCK_TOKEN from the environment (auto-injected by the Flights runtime).
# Fail fast and clearly if it is missing.
MOTHERDUCK_TOKEN = os.environ.get("MOTHERDUCK_TOKEN", "").strip()
if not MOTHERDUCK_TOKEN:
    raise SystemExit("MOTHERDUCK_TOKEN is required (the Flights runtime injects it).")

MCP_URL = os.environ.get("MD_MCP_URL", "https://api.motherduck.com/mcp").strip()
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


# ---- The agent's tools: the hosted MCP server's read-only tools --------------
# The hosted MotherDuck MCP server exposes read AND write tools. We mirror only
# the read-only ones into the agent. The strongest backstop is still the token's
# permissions (give the Flight a read-scoped token); this name filter is the
# in-code layer. Tune the denylist if you want a narrower or wider surface.
MUTATING_PREFIXES = ("save_", "update_", "delete_", "edit_", "create_",
                     "run_", "cancel_", "share_", "mint_", "log_")


def is_read_only_tool(name: str) -> bool:
    return name != "query_rw" and not name.startswith(MUTATING_PREFIXES)


class MotherDuckMCPClient:
    """Minimal JSON-RPC client for the hosted MotherDuck MCP server.

    The server is stateless Streamable HTTP — each call is a self-contained POST,
    so there is no session to track and no SSE stream to hold open. Talking to it
    from plain Python like this works reliably (unlike the Agent SDK's bundled
    CLI, which cannot reach this endpoint). Stateless also means one client is
    safe to share across the concurrent agents; only the request-id counter needs
    a lock.
    """

    _HEADERS = {"Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"}

    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._id = 0
        self._id_lock = threading.Lock()

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def request(self, method: str, params: dict | None = None) -> dict:
        headers = dict(self._HEADERS)
        headers["Authorization"] = f"Bearer {self.token}"
        body = {"jsonrpc": "2.0", "method": method, "id": self._next_id()}
        if params is not None:
            body["params"] = params
        resp = requests.post(self.url, json=body, headers=headers, timeout=120)
        resp.raise_for_status()
        # The server may answer with a single JSON object or an SSE stream; the
        # last `data:` line of an SSE response carries the JSON-RPC message.
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            data: dict = {}
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
            return data
        return resp.json()

    def initialize(self) -> dict:
        return self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "borough-briefs-flight", "version": "1.0.0"},
        })

    def list_tools(self) -> list:
        return self.request("tools/list").get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def render_tool_result(resp: dict) -> str:
    # Turn a JSON-RPC tools/call response into the text the agent sees.
    if "error" in resp:
        return f"ERROR: {resp['error']}"
    result = resp.get("result", {})
    parts = result.get("content", [])
    text = "\n".join(p.get("text", "") for p in parts
                     if isinstance(p, dict) and p.get("type") == "text")
    return text or json.dumps(result)[:4000]


def build_motherduck_server(client: MotherDuckMCPClient):
    """Mirror the hosted server's READ-ONLY tools as in-process SDK tools.

    Each in-process tool reuses the hosted tool's own JSON Schema (the SDK passes
    a full schema dict through unchanged) and forwards the call through `client`.
    Returns the SDK MCP server plus the `allowed_tools` list naming each mirrored
    tool as `mcp__motherduck__<name>`.
    """
    specs = [s for s in client.list_tools() if is_read_only_tool(s.get("name", ""))]

    def make_tool(spec: dict):
        name = spec["name"]
        description = spec.get("description") or name
        schema = spec.get("inputSchema") or {"type": "object", "properties": {}}

        async def forward(args, _name=name):
            resp = await asyncio.to_thread(client.call_tool, _name, args or {})
            return {"content": [{"type": "text", "text": render_tool_result(resp)}]}

        return tool(name, description, schema)(forward)

    sdk_tools = [make_tool(s) for s in specs]
    names = [s["name"] for s in specs]
    server = create_sdk_mcp_server(name="motherduck", version="1.0.0", tools=sdk_tools)
    allowed = [f"mcp__motherduck__{n}" for n in names]
    return server, allowed, names


# Connect once at import and mirror the hosted read-only toolset. A failure here
# (unreachable server, rejected token) fails the run immediately and clearly.
MCP_CLIENT = MotherDuckMCPClient(MCP_URL, MOTHERDUCK_TOKEN)
MCP_CLIENT.initialize()
MOTHERDUCK_SERVER, ALLOWED_TOOLS, MIRRORED_TOOLS = build_motherduck_server(MCP_CLIENT)
log(f"Mirrored {len(MIRRORED_TOOLS)} read-only MotherDuck MCP tools: {MIRRORED_TOOLS}")


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

You have a set of READ-ONLY MotherDuck tools. The main one is `query`, which runs
DuckDB SQL and returns rows — call it with database="sample_data" and your `sql`.
Other tools (`list_tables`, `list_columns`, `search_catalog`, ...) help you
explore the schema. There are no write tools; do not attempt to modify anything.
Ground every claim in real query results.

The data lives in the table `{SOURCE_TABLE}` (one row per 311 request). It is a
frozen snapshot, so your analysis window is fixed to the most recent
{WINDOW_DAYS} days present in the data:
  created_date from {start_str} to {end_str} (inclusive).
Filter on `borough = '{borough}'` and `created_date BETWEEN '{start_str}' AND
'{end_str}'` for every query about this window.

Steps:
1. Confirm the real column names before querying (DESCRIBE the table, or use
   `list_columns`) — do not assume them. Useful columns include created_date,
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
        mcp_servers={"motherduck": MOTHERDUCK_SERVER},
        # The agent's only capabilities are the mirrored read-only MotherDuck
        # tools. `tools=[]` disables every built-in tool (no Bash, Read, Write,
        # ...), so the agent cannot shell out or touch the filesystem;
        # `allowed_tools` auto-approves exactly the mirrored tools; `dontAsk`
        # denies anything not pre-approved without ever prompting (headless);
        # `setting_sources=[]` ignores any local Claude settings.
        tools=[],
        allowed_tools=ALLOWED_TOOLS,
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
