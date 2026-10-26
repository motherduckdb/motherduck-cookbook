"""
Tool or API -> MotherDuck with dlt (MotherDuck Flight starter).

Loads an HTTP API into MotherDuck with dlt's declarative `rest_api` source and
`motherduck` destination. dlt handles pagination, retries (429/5xx, honouring
Retry-After), schema inference and evolution, and merge/upsert on a primary key.
To use a dlt verified source or your own @dlt.resource instead, edit
build_source().

Flight secret (type: flights), optional, default name "api", holding:
  API_KEY        the token or key (never put it in config or code)
  SOURCES__...   optional dlt-style credentials for a verified source

Config (non-secret):
  BASE_URL       required, e.g. https://api.example.com/v1
  RESOURCE       required, endpoint path under BASE_URL, e.g. tickets
  PARAMS         query string sent on every request, e.g. per_page=100&state=all
  RECORDS_PATH   JSONPath to the records in the response, e.g. data.items ("" = auto-detect)
  AUTH           none | bearer | header:<Header-Name> | query:<param>  (default: bearer if API_KEY is set)
  PAGINATION     auto | single_page | page_number | header_link | json_link   (default auto)
  PAGE_PARAM / BASE_PAGE   page_number only, default "page" / 1
  NEXT_URL_PATH  json_link only, JSONPath to the next-page URL, e.g. next or links.next
  MAX_PAGES      safety cap on pages per run, default 1000 (0 = no cap)
  PRIMARY_KEY    upsert key; set = write_disposition merge, empty = append
  INCREMENTAL_CURSOR  field that grows on change, e.g. updated_at ("" = full sync each run)
  INCREMENTAL_PARAM   query param that takes the last cursor, e.g. since ("" = filter client-side)
  INCREMENTAL_START   cursor value for the first run, e.g. 2024-01-01T00:00:00Z
  DESTINATION_DATABASE / DATASET_NAME / TABLE_NAME   default api_sync / raw / last RESOURCE segment
  PIPELINE_NAME  dlt pipeline (and state) name, default api_sync_<TABLE_NAME>
  SECRET_NAME    default "api"
  MOTHERDUCK_HOST  only for MotherDuck staging accounts
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qsl

import dlt
import duckdb
from dlt.sources.rest_api import rest_api_source


def cfg(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def ident(name: str, value: str) -> str:
    # The database name reaches a CREATE DATABASE statement that can't be parameterized.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def load_secrets(secret: str) -> str:
    """Return API_KEY, and expose dlt-style secret params (SOURCES__...) under their bare names.

    A Flights secret injects each param as `<secret>_<PARAM>` (and, when safe, bare).
    dlt resolves credentials from bare names such as SOURCES__GITHUB__ACCESS_TOKEN.
    """
    prefix = f"{secret}_"
    for name, value in list(os.environ.items()):
        bare = name[len(prefix):]
        if name.startswith(prefix) and bare.startswith(("SOURCES__", "DESTINATION__")):
            os.environ.setdefault(bare, value)
    return cfg(f"{secret}_API_KEY") or cfg("API_KEY")


def auth_config(key: str) -> dict | None:
    auth = cfg("AUTH", "bearer" if key else "none").lower()
    if auth == "none":
        return None
    if not key:
        raise RuntimeError(f"AUTH={auth} needs API_KEY in the Flights secret (or the environment)")
    if auth == "bearer":
        return {"type": "bearer", "token": key}
    kind, _, name = auth.partition(":")
    if kind in ("header", "query") and name:
        return {"type": "api_key", "name": name.strip(), "api_key": key, "location": kind}
    raise RuntimeError(f"Unknown AUTH {auth!r}: use none, bearer, header:<Name> or query:<param>")


def paginator_config() -> dict | str:
    kind = cfg("PAGINATION", "auto").lower()
    if kind == "page_number":
        # Stops at the first empty page; set total_path if the API returns a page count.
        return {"type": "page_number", "page_param": cfg("PAGE_PARAM", "page"),
                "base_page": int(cfg("BASE_PAGE", "1")), "total_path": None}
    if kind == "json_link":
        return {"type": "json_link", "next_url_path": cfg("NEXT_URL_PATH", "next")}
    # auto, single_page, header_link (follow `Link: rel="next"`). For offset or cursor
    # pagination see https://dlthub.com/docs/dlt-ecosystem/verified-sources/rest_api/basic#pagination
    return kind


def build_source(key: str, table: str, primary_key: str):
    endpoint: dict = {"path": cfg("RESOURCE"), "params": dict(parse_qsl(cfg("PARAMS"))),
                      "paginator": paginator_config()}
    if cfg("RECORDS_PATH"):
        endpoint["data_selector"] = cfg("RECORDS_PATH")
    if cfg("INCREMENTAL_CURSOR"):
        # dlt stores the highest cursor seen in the destination, so every run (even on a
        # fresh Flight machine) resumes from there instead of re-reading the whole history.
        endpoint["incremental"] = {"cursor_path": cfg("INCREMENTAL_CURSOR"),
                                   "initial_value": cfg("INCREMENTAL_START") or None}
        if cfg("INCREMENTAL_PARAM"):
            endpoint["incremental"]["start_param"] = cfg("INCREMENTAL_PARAM")

    resource: dict = {"name": table, "endpoint": endpoint,
                      "write_disposition": "merge" if primary_key else "append"}
    if primary_key:
        resource["primary_key"] = primary_key

    client: dict = {"base_url": cfg("BASE_URL")}
    if auth := auth_config(key):
        client["auth"] = auth

    # --- Swap the source here -------------------------------------------------
    # (a) dlt verified source: pin the verified-sources archive in requirements.txt
    #     (see README) and return the source instead, e.g.
    #       from sources.github import github_reactions
    #       return github_reactions("duckdb", "duckdb")   # token from SOURCES__GITHUB__ACCESS_TOKEN
    # (c) custom resource for anything rest_api can't express:
    #       @dlt.resource(name=table, primary_key="id", write_disposition="merge")
    #       def items():
    #           yield from my_client.list_items()          # plain dicts or lists of dicts
    #       return items
    # ---------------------------------------------------------------------------
    return rest_api_source({"client": client, "resources": [resource]})


def run(key: str) -> None:
    for name in ("BASE_URL", "RESOURCE"):
        if not cfg(name):
            raise RuntimeError(f"Set config {name} (see the docstring at the top of flight.py)")
    database = ident("DESTINATION_DATABASE", cfg("DESTINATION_DATABASE", "api_sync"))
    dataset = cfg("DATASET_NAME", "raw")
    table = cfg("TABLE_NAME", cfg("RESOURCE").strip("/").split("/")[-1])
    primary_key = cfg("PRIMARY_KEY")

    os.environ.setdefault("HOME", "/tmp")  # dlt keeps working files under HOME; /tmp is writable
    if host := cfg("MOTHERDUCK_HOST"):
        os.environ["motherduck_host"] = host
    # dlt reads MOTHERDUCK_TOKEN (injected by the Flight runtime) itself; only the database is set.
    os.environ["DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE"] = database
    # dlt creates the dataset (schema) and tables, but not the database.
    with duckdb.connect("md:") as con:
        con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")

    source = build_source(key, table, primary_key)
    if max_pages := int(cfg("MAX_PAGES", "1000")):
        source.add_limit(max_pages)

    pipeline = dlt.pipeline(pipeline_name=cfg("PIPELINE_NAME", f"api_sync_{table}"),
                            destination="motherduck", dataset_name=dataset)
    # Parquet loader files keep larger loads on MotherDuck's bulk-loading path.
    load_info = pipeline.run(source, loader_file_format="parquet")
    load_info.raise_on_failed_jobs()

    rows = pipeline.last_trace.last_normalize_info.row_counts
    for name, count in sorted(rows.items()):
        if not name.startswith("_dlt"):
            print(f"  {database}.{dataset}.{name}: {count:,} row(s) loaded", flush=True)
    mode = f"merged on {primary_key}" if primary_key else "appended"
    print(f"Loaded {sum(c for n, c in rows.items() if not n.startswith('_dlt')):,} row(s) ({mode})", flush=True)
    print(load_info, flush=True)


def main() -> None:
    key = load_secrets(cfg("SECRET_NAME", "api"))
    try:
        run(key)
    except Exception as exc:
        # Errors can echo request URLs; never let a key in the query string reach the log.
        message = f"{type(exc).__name__}: {exc}"
        print("Load failed: " + (message.replace(key, "***") if key else message), file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
