"""
HTTP JSON API -> MotherDuck table (MotherDuck Flight starter).

A starting point for syncing a custom tool or API. It fetches JSON records from
an HTTP endpoint (optionally paginated and authenticated), lands them in a
MotherDuck table, and upserts on a key so re-runs don't duplicate rows. Adapt
fetch_records() when an API needs something this doesn't cover.

Flight secret (type: flights), optional, default name "api", holding:
  API_KEY        the token or key (never put it in config or code)

Config (non-secret):
  API_URL        required. The endpoint returning JSON, e.g. https://api.example.com/v1/items
  RECORDS_PATH   dot path to the list of records in the response ("" = the response is the list)
  AUTH           none | bearer | header:<Header-Name> | query:<param>   (default: bearer if API_KEY is set)
  PAGINATION     none | page | link    (page: ?<PAGE_PARAM>=1,2,...; link: follow the Link rel="next" header)
  PAGE_PARAM     default "page"
  MAX_PAGES      default 100 (a safety cap)
  PRIMARY_KEY    column to upsert on; "" = append every run
  TARGET_DB      default "api_sync"
  TARGET_TABLE   default "records"
  SECRET_NAME    default "api"
  MOTHERDUCK_HOST  only for MotherDuck staging accounts

requirements.txt:
  duckdb==1.5.6
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import duckdb


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def cfg(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def connect_motherduck():
    host = os.environ.get("MOTHERDUCK_HOST")
    if host:
        os.environ["motherduck_host"] = host
        print(f"Targeting MotherDuck host {host}", flush=True)
    return duckdb.connect("md:")


def api_key(secret: str) -> str:
    return (os.environ.get(f"{secret}_API_KEY") or os.environ.get("API_KEY") or "").strip()


def authorize(url: str, headers: dict, key: str, auth: str) -> str:
    """Attach the key the way the API expects. Returns the (possibly updated) URL."""
    if not key or auth == "none":
        return url
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif auth.startswith("header:"):
        headers[auth.split(":", 1)[1].strip()] = key
    elif auth.startswith("query:"):
        param = auth.split(":", 1)[1].strip()
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode({param: key})}"
    else:
        raise RuntimeError(f"Unknown AUTH {auth!r}: use none, bearer, header:<Name> or query:<param>")
    return url


def get_json(url: str, headers: dict, attempts: int = 4):
    """GET with retries on 429/5xx and network errors. Returns (json, response headers)."""
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.headers
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == attempts:
                body = exc.read(300).decode("utf-8", "replace")
                raise RuntimeError(f"HTTP {exc.code} from {redact(url)}: {body}") from None
            wait = float(exc.headers.get("Retry-After") or 2**attempt)
        except urllib.error.URLError as exc:
            if attempt == attempts:
                raise RuntimeError(f"Could not reach {redact(url)}: {exc.reason}") from None
            wait = 2**attempt
        print(f"  retry {attempt}/{attempts - 1} in {wait:.0f}s", flush=True)
        time.sleep(min(wait, 60))


def redact(url: str) -> str:
    """Never log a key that travels in the query string."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    auth = cfg("AUTH")
    hidden = auth.split(":", 1)[1].strip() if auth.startswith("query:") else None
    query = [(k, "***" if k == hidden else v) for k, v in query]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def dig(payload, path: str):
    for part in [p for p in path.split(".") if p]:
        payload = payload[part] if isinstance(payload, dict) else payload[int(part)]
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise RuntimeError(f"RECORDS_PATH {path!r} does not point at a list of records")
    return payload


def next_link(headers) -> str | None:
    for part in (headers.get("Link") or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', part)
        if m:
            return m.group(1)
    return None


def fetch_records(base_url: str) -> list:
    secret = cfg("SECRET_NAME", "api")
    key = api_key(secret)
    auth = cfg("AUTH", "bearer" if key else "none").lower()
    pagination = cfg("PAGINATION", "none").lower()
    page_param = cfg("PAGE_PARAM", "page")
    max_pages = int(cfg("MAX_PAGES", "100"))
    records_path = cfg("RECORDS_PATH")

    records, url, page = [], base_url, 1
    for _ in range(max_pages):
        if pagination == "page":
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}{urllib.parse.urlencode({page_param: page})}"
        headers: dict = {}
        payload, resp_headers = get_json(authorize(url, headers, key, auth), headers)
        batch = dig(payload, records_path)
        records.extend(batch)
        print(f"  fetched {len(batch)} record(s) from {redact(url)}", flush=True)
        if pagination == "page":
            if not batch:
                break
            page += 1
        elif pagination == "link":
            url = next_link(resp_headers)
            if not url:
                break
        else:
            break
    else:
        print(f"  stopped at MAX_PAGES={max_pages}; raise it if there's more", flush=True)
    return records


def main():
    base_url = cfg("API_URL")
    if not base_url:
        raise RuntimeError("Set config API_URL to the endpoint to sync")
    tgt_db = cfg("TARGET_DB", "api_sync")
    tgt_table = cfg("TARGET_TABLE", "records")
    pk = cfg("PRIMARY_KEY")
    target = f"{q(tgt_db)}.main.{q(tgt_table)}"

    records = fetch_records(base_url)
    print(f"Fetched {len(records)} record(s)", flush=True)
    if not records:
        print("Nothing to load.", flush=True)
        return

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, dir="/tmp") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        staged_path = f.name

    con = connect_motherduck()
    con.execute(f"CREATE DATABASE IF NOT EXISTS {q(tgt_db)}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE staged AS SELECT * FROM read_json_auto('{staged_path}')")
    con.execute(f"CREATE TABLE IF NOT EXISTS {target} AS SELECT * FROM staged WHERE false")

    # New fields in the API become new columns instead of failing the load.
    have = {r[0] for r in con.execute(
        "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND schema_name = 'main' AND table_name = ?",
        [tgt_db, tgt_table]).fetchall()}
    for name, dtype in con.execute(
            "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = 'temp' AND table_name = 'staged'").fetchall():
        if name not in have:
            con.execute(f"ALTER TABLE {target} ADD COLUMN {q(name)} {dtype}")
            print(f"  added column {name} {dtype}", flush=True)

    if pk:
        con.execute(f"DELETE FROM {target} WHERE {q(pk)} IN (SELECT {q(pk)} FROM staged)")
    con.execute(f"INSERT INTO {target} BY NAME SELECT * FROM staged")
    total = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    os.remove(staged_path)
    mode = f"upserted on {pk}" if pk else "appended"
    print(f"Loaded {len(records)} record(s) into {tgt_db}.main.{tgt_table} ({mode}); table now has {total:,} rows", flush=True)


if __name__ == "__main__":
    main()
