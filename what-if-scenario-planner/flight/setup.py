#!/usr/bin/env python3
"""Sets up (or updates) the supply planner backend in one MotherDuck organization.

  uv run --with duckdb==1.5.6 --with pytz python flight/setup.py <target> [--admin-token-env VAR] [--api URL] [--host HOST]

<target> is any name (prod, staging, devrel, ...); its settings are kept in
dive/deploy-targets.json. --admin-token-env names the environment variable that
holds YOUR token (an organization admin); --api / --host are only needed for a
non-production MotherDuck endpoint. Re-runs reuse the saved settings.

Idempotent steps:
  1. the service account scenario_planner_writer (REST API) and a read/write
     token for it, saved to ~/.config/scenario-planner/<target>-sa-token (0600)
     and reused on later runs;
  2. as the service account: the scenario_planner database (planner.Store.ensure_schema:
     static planning tables in main, writeback tables in supply, seed scenarios);
  3. an organization-wide, automatically updated share scenario_planner_org;
  4. the supply-planner-write Flight (build_flight.py output), created or
     updated, and GRANT RUN ... TO ORGANIZATION;
  5. dive/deploy-targets.json updated with the share URL and Flight id.

Then publish the Dive as yourself: dive/scripts/deploy-dive.sh <target>.
GRANT RUN ON FLIGHT needs DuckDB >= 1.5.6, hence the pinned duckdb above.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS_FILE = os.path.join(HERE, "..", "dive", "deploy-targets.json")
SA = "scenario_planner_writer"
DB = "scenario_planner"
SHARE = "scenario_planner_org"
FLIGHT = "supply-planner-write"


def log(msg):
    print(f"[setup] {msg}", flush=True)


def load_targets() -> dict:
    return json.load(open(TARGETS_FILE)) if os.path.exists(TARGETS_FILE) else {}


def save_targets(targets: dict):
    with open(TARGETS_FILE, "w") as f:
        json.dump(targets, f, indent=2)
        f.write("\n")


def rest(t, method, path, body=None):
    req = urllib.request.Request(
        t["api"] + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ[t['admin_token_env']]}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}


def service_account_token(target, t) -> str:
    path = os.path.expanduser(f"~/.config/scenario-planner/{target}-sa-token")
    if os.path.exists(path):
        log(f"using the saved {SA} token ({path})")
        return open(path).read().strip()
    status, body = rest(t, "POST", "/v1/users", {"username": SA})
    exists = status in (400, 409) and "unique" in str(body.get("error", ""))
    log(f"service account {SA}: " + ("created" if status < 300 else "already exists" if exists else f"HTTP {status} {body.get('error', '')[:120]}"))
    status, body = rest(t, "POST", f"/v1/users/{SA}/tokens", {"name": f"supply-planner-setup-{time.strftime('%Y%m%d-%H%M%S')}", "token_type": "read_write"})
    token = body.pop("token", None)
    if not token:
        sys.exit(f"could not create a token for {SA}: HTTP {status} {body}")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    log(f"saved a new {SA} token to {path}")
    return token


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target")
    ap.add_argument("--admin-token-env", help="environment variable holding your (org admin) token [motherduck_token]")
    ap.add_argument("--api", help="MotherDuck REST API base URL [https://api.motherduck.com]")
    ap.add_argument("--host", help="MotherDuck host for non-production endpoints, e.g. api.staging.motherduck.com")
    a = ap.parse_args()
    targets = load_targets()
    t = targets.setdefault(a.target, {})
    t["admin_token_env"] = a.admin_token_env or t.get("admin_token_env") or "motherduck_token"
    t["api"] = a.api or t.get("api") or "https://api.motherduck.com"
    t["host"] = a.host or t.get("host") or "api.motherduck.com"
    if not os.environ.get(t["admin_token_env"]):
        sys.exit(f"${t['admin_token_env']} is not set")
    token = service_account_token(a.target, t)

    # Everything below runs as the service account.
    os.environ.pop("MOTHERDUCK_TOKEN", None)
    os.environ.pop("motherduck_host", None)
    if t["host"] != "api.motherduck.com":
        os.environ["motherduck_host"] = t["host"]
    sys.path.insert(0, HERE)
    import planner  # noqa: E402

    store = planner.Store(token, DB)
    me = store.one("SELECT current_user() AS u")["u"]
    if me != SA:
        sys.exit(f"connected as {me}, expected {SA}")
    store.ensure_schema(log)
    store.q(f"CREATE SHARE IF NOT EXISTS {SHARE} FROM {DB} (ACCESS ORGANIZATION, VISIBILITY DISCOVERABLE, UPDATE AUTOMATIC)")
    share_url = store.one("SELECT url FROM md_information_schema.owned_shares WHERE name = ?", [SHARE])["url"]
    log(f"share {SHARE}: {share_url}")

    subprocess.run([sys.executable, os.path.join(HERE, "build_flight.py")], check=True)
    src_path = os.path.join(HERE, "build", "supply_planner_write.py")
    req_path = os.path.join(HERE, "requirements.txt")
    config = {"PLANNER_DB": DB, "OP": "", "ARGS": "", "ACTOR": "", "REQUEST_ID": ""}
    if t["host"] != "api.motherduck.com":
        config["motherduck_host"] = t["host"]
    cfg_sql = "MAP {" + ", ".join(f"'{k}': '{v}'" for k, v in config.items()) + "}"  # fixed keys/values, no quotes inside
    con = store.con
    row = con.execute("SELECT flight_id::VARCHAR FROM MD_LIST_FLIGHTS() WHERE flight_name = ?", [FLIGHT]).fetchone()
    con.execute(f"SET VARIABLE sp_src = (SELECT content FROM read_text('{src_path}'))")
    con.execute(f"SET VARIABLE sp_req = (SELECT content FROM read_text('{req_path}'))")
    con.execute(f"SET VARIABLE sp_cfg = {cfg_sql}")
    if row:
        flight_id = row[0]
        con.execute("CALL MD_UPDATE_FLIGHT(flight_id := ?::UUID, source_code := getvariable('sp_src'), "
                    "requirements_txt := getvariable('sp_req'), config := getvariable('sp_cfg'))", [flight_id])
        log(f"updated Flight {FLIGHT} {flight_id}")
    else:
        flight_id = con.execute("SELECT flight_id::VARCHAR FROM MD_CREATE_FLIGHT(name := ?, source_code := getvariable('sp_src'), "
                                "requirements_txt := getvariable('sp_req'), config := getvariable('sp_cfg'))", [FLIGHT]).fetchone()[0]
        log(f"created Flight {FLIGHT} {flight_id}")
    con.execute(f"GRANT RUN ON FLIGHT '{flight_id}' TO ORGANIZATION")

    t.update({"share_url": share_url, "write_flight_id": flight_id})
    t.setdefault("dive_id", "")
    save_targets(targets)
    log(f"dive/deploy-targets.json updated. Next: dive/scripts/deploy-dive.sh {a.target}")


if __name__ == "__main__":
    main()
