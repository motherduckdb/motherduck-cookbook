#!/usr/bin/env python3
"""Publish the observability Dive and the share that lets the organization read its data.

Steps, all through the MotherDuck SQL surface:

1. Find (or create) a share of the metrics database so every member of the
   organization can open the Dive. A restricted share granted to the preset
   `explorer` role is the organization-wide default; pass `--grant <role>` to
   scope it to specific roles instead, or `--no-share` to point the Dive at the
   database directly (then only the owner can use it).
2. Substitute the share URL into `dive/dive.tsx` (the `REQUIRED_DATABASES`
   placeholder) and create the Dive with MD_CREATE_DIVE, or update it in place
   with MD_UPDATE_DIVE_CONTENT when a Dive with the same title already exists.
3. Optionally set its governance status (`--status ready`).

Run it with the same token that owns the metrics database (the service
account that runs the collector Flight).

Usage:
    export MOTHERDUCK_TOKEN=<service account token>
    uv run --with-requirements requirements.txt deploy_dive.py
    uv run --with-requirements requirements.txt deploy_dive.py --status ready
    uv run --with-requirements requirements.txt deploy_dive.py --grant explorer --grant data_platform
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "__MD_OBSERVABILITY_SHARE_URL__"
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default=os.environ.get("TARGET_DATABASE", "md_observability"),
                        help="metrics database the collector writes to (default: md_observability)")
    parser.add_argument("--share", default=None, help="share name (default: <database>_share)")
    parser.add_argument("--grant", action="append", default=[], metavar="ROLE",
                        help="role to grant READ on the share (repeatable; default: explorer = whole organization)")
    parser.add_argument("--no-share", action="store_true", help="skip the share; the Dive reads the database directly")
    parser.add_argument("--title", default=None, help="Dive title (default: from dive/dive-manifest.json)")
    parser.add_argument("--status", choices=["draft", "ready", "endorsed", "archived"], default=None)
    args = parser.parse_args(argv)
    if not IDENTIFIER_RE.fullmatch(args.database):
        parser.error(f"--database must be a plain SQL identifier, got {args.database!r}")
    args.share = args.share or f"{args.database}_share"
    if not IDENTIFIER_RE.fullmatch(args.share):
        parser.error(f"--share must be a plain SQL identifier, got {args.share!r}")
    for role in args.grant:
        if not IDENTIFIER_RE.fullmatch(role):
            parser.error(f"--grant must be a plain SQL identifier, got {role!r}")
    return args


def ensure_share(con: duckdb.DuckDBPyConnection, database: str, share: str, grants: list[str]) -> str:
    """Return the URL of a share of `database`, creating it (restricted + grants) if needed."""
    rows = con.execute(
        "SELECT name, url FROM md_information_schema.owned_shares WHERE source_db_name = ? ORDER BY created_ts",
        [database],
    ).fetchall()
    named = [r for r in rows if r[0] == share]
    if named:
        print(f"using existing share {share}: {named[0][1]}")
        return named[0][1]
    if rows:
        print(f"using existing share {rows[0][0]} of {database}: {rows[0][1]} (pass --share to pick another)")
        return rows[0][1]
    # The database must exist before it can be shared; the collector creates it
    # on its first run, so deploy and run the collector first.
    exists = con.execute("SELECT count(*) FROM md_information_schema.databases WHERE name = ?", [database]).fetchone()[0]
    if not exists:
        raise SystemExit(f"database {database} does not exist yet; run the collector Flight once first")
    con.execute(f'CREATE SHARE "{share}" FROM "{database}" (ACCESS RESTRICTED, VISIBILITY DISCOVERABLE, UPDATE AUTOMATIC)')
    for role in grants or ["explorer"]:
        con.execute(f'GRANT READ ON SHARE "{share}" TO ROLE "{role}"')
    url = con.execute("SELECT url FROM md_information_schema.owned_shares WHERE name = ?", [share]).fetchone()[0]
    print(f"created share {share} (READ granted to {', '.join(grants or ['explorer'])}): {url}")
    return url


def deploy_dive(con: duckdb.DuckDBPyConnection, title: str, description: str, content: str,
                resource_url: str, alias: str) -> str:
    rows = con.execute("SELECT id FROM MD_LIST_DIVES() WHERE title = ?", [title]).fetchall()
    if len(rows) > 1:
        raise SystemExit(f"{len(rows)} Dives are titled {title!r}; expected 0 or 1")
    resources = "[{'url': ?, 'alias': ?}]"
    if rows:
        dive_id = str(rows[0][0])
        con.execute(
            f"FROM MD_UPDATE_DIVE_CONTENT(id := ?::UUID, content := ?, api_version := 1, required_resources := {resources})",
            [dive_id, content, resource_url, alias])
        con.execute("FROM MD_UPDATE_DIVE_METADATA(id := ?::UUID, title := ?, description := ?)",
                    [dive_id, title, description])
        print(f"updated Dive {title!r} ({dive_id})")
    else:
        row = con.execute(
            f"SELECT id FROM MD_CREATE_DIVE(title := ?, content := ?, description := ?, api_version := 1, "
            f"required_resources := {resources})",
            [title, content, description, resource_url, alias]).fetchone()
        dive_id = str(row[0])
        print(f"created Dive {title!r} ({dive_id})")
    return dive_id


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    manifest = json.loads((HERE / "dive" / "dive-manifest.json").read_text())
    title = args.title or manifest["title"]
    description = manifest.get("description", "")
    alias = manifest.get("alias", "md_observability")
    source = (HERE / "dive" / manifest.get("source", "dive.tsx")).read_text()
    if PLACEHOLDER not in source:
        raise SystemExit(f"dive source has no {PLACEHOLDER} placeholder in REQUIRED_DATABASES")

    con = duckdb.connect("md:")
    if args.no_share:
        resource_url = f"md:{args.database}"
        source = source.replace("type: 'share'", "type: 'database'")
    else:
        resource_url = ensure_share(con, args.database, args.share, args.grant)
    content = source.replace(PLACEHOLDER, resource_url)
    if alias != "md_observability":
        content = content.replace('"md_observability".', f'"{alias}".').replace("alias: 'md_observability'", f"alias: '{alias}'")

    dive_id = deploy_dive(con, title, description, content, resource_url, alias)
    if args.status:
        try:
            con.execute("FROM MD_UPDATE_DIVE_STATUS(id := ?::UUID, status := ?)", [dive_id, args.status])
            print(f"status -> {args.status}")
        except duckdb.Error as exc:
            # 'endorsed' needs an organization admin; do not fail the deploy over it.
            print(f"WARNING could not set status {args.status!r}: {str(exc).splitlines()[0]}", file=sys.stderr)
    print(f"https://app.motherduck.com/dives/{dive_id}")


if __name__ == "__main__":
    main(sys.argv[1:])
