#!/usr/bin/env python3
"""Create the service account that owns this deployment, mint its token, grant it a role.

Run once, as an organization admin:

    uv run --with-requirements requirements.txt setup_service_account.py
    uv run --with-requirements requirements.txt setup_service_account.py --name obs_svc --role builder --no-env

The admin token comes from MOTHERDUCK_ADMIN_TOKEN, or from the MotherDuck CLI's
~/.motherduck/credentials.json when that variable is unset. Nothing secret is
printed: the new service account token is written to ./.env (mode 600) as
MOTHERDUCK_TOKEN for the deploy scripts to pick up.

Steps (Admin REST API + SQL):
  1. POST /v1/users                      create the service account (ok if it exists)
  2. POST /v1/users/<name>/tokens        create a read/write token labelled --token-name
  3. GRANT ROLE <role> TO USER <name>    over a SQL connection as the admin
  4. connect as the service account and read query_history (and storage_info)
     to prove the role took

A new service account is an Explorer, and Explorers cannot read
md_information_schema.query_history or recent_queries, which almost every
collector needs. Builder is the minimum role; admin additionally unlocks
storage_info and org-wide Flight counts. Pass --role none only if you grant a
role (or a custom role that inherits Builder) some other way.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

import duckdb
import httpx

API = "https://api.motherduck.com"
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def admin_token() -> str:
    token = os.environ.get("MOTHERDUCK_ADMIN_TOKEN", "").strip()
    if token:
        return token
    creds = pathlib.Path.home() / ".motherduck" / "credentials.json"
    if creds.exists():
        return json.loads(creds.read_text()).get("motherduck_token", "").strip()
    raise SystemExit("Set MOTHERDUCK_ADMIN_TOKEN (an admin's read/write token) or log in with the MotherDuck CLI.")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="md_observability_svc", help="service account username")
    parser.add_argument("--token-name", default="md-observability", help="label of the token to create")
    parser.add_argument("--role", default="admin",
                        help="role to grant: admin (default; unlocks storage_info and org-wide Flights), builder "
                             "(the minimum: query history), or none to skip the grant and assign a role yourself")
    parser.add_argument("--env-file", default=".env", help="where to write MOTHERDUCK_TOKEN")
    parser.add_argument("--no-env", action="store_true", help="do not write the token anywhere (it is then lost)")
    args = parser.parse_args(argv)
    for label, value in (("--name", args.name), ("--role", args.role)):
        if not IDENTIFIER_RE.fullmatch(value):
            parser.error(f"{label} must be a plain identifier, got {value!r}")
    if args.role.lower() == "none":
        args.role = ""
    elif args.role.lower() == "explorer":
        parser.error("--role explorer cannot read md_information_schema.query_history; use builder or admin")

    admin = admin_token()
    headers = {"Authorization": f"Bearer {admin}", "Content-Type": "application/json"}

    r = httpx.post(f"{API}/v1/users", headers=headers, json={"username": args.name}, timeout=30)
    if r.status_code < 300:
        print(f"created service account {args.name}")
    elif "exist" in r.text.lower():
        print(f"service account {args.name} already exists; reusing it")
    else:
        raise SystemExit(f"could not create the service account: {r.status_code} {r.text[:200]}")

    r = httpx.post(f"{API}/v1/users/{args.name}/tokens", headers=headers,
                   json={"name": args.token_name, "token_type": "read_write"}, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"could not create the token: {r.status_code} {r.text[:200]}")
    token = r.json()["token"]
    if args.no_env:
        print(f"created token {args.token_name!r} (not saved anywhere, as requested)")
    else:
        path = pathlib.Path(args.env_file)
        path.write_text(f"MOTHERDUCK_TOKEN={token}\n")
        os.chmod(path, 0o600)
        print(f"created token {args.token_name!r} and wrote it to {path} as MOTHERDUCK_TOKEN")

    if args.role:
        con = duckdb.connect("md:", config={"motherduck_token": admin})
        con.execute(f"GRANT ROLE {args.role} TO USER {args.name}")
        con.close()
        print(f"granted role {args.role} to {args.name}")
    else:
        print(f"no role granted: {args.name} stays an Explorer until you GRANT ROLE builder (or admin) TO USER {args.name}",
              file=sys.stderr)

    # The duckdb client cannot open md: twice with different tokens in one
    # process, so the check as the service account runs in a child process.
    check = subprocess.run(
        [sys.executable, "-c", VERIFY_SNIPPET], env={**os.environ, "MOTHERDUCK_TOKEN": token},
        capture_output=True, text=True, timeout=120)
    sys.stdout.write(check.stdout)
    if check.returncode != 0:
        print(check.stderr.strip().splitlines()[-1] if check.stderr.strip() else "verification failed", file=sys.stderr)


VERIFY_SNIPPET = """
import duckdb, sys
con = duckdb.connect("md:")
(who,) = con.execute("SELECT current_user()").fetchone()
try:
    con.execute("SELECT count(*) FROM md_information_schema.query_history WHERE start_time > now() - INTERVAL 1 HOUR").fetchone()
    print(f"connected as {who}; query_history readable, so the builder (or admin) role is active")
except duckdb.Error as exc:
    print(f"connected as {who}; query_history not readable yet ({str(exc).splitlines()[0]}). "
          "The collector needs it; a new role can take a few minutes to apply.", file=sys.stderr)
    sys.exit(1)
try:
    (n,) = con.execute("SELECT count(*) FROM md_information_schema.storage_info").fetchone()
    print(f"storage_info readable ({n} databases), so the admin role is active")
except duckdb.Error as exc:
    print(f"storage_info not readable ({str(exc).splitlines()[0]}): storage metrics will be skipped "
          "unless the account holds the admin role (a new role can take a few minutes to apply).", file=sys.stderr)
"""


if __name__ == "__main__":
    main(sys.argv[1:])
