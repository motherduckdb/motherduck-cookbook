"""MotherDuck -> HubSpot static-list sync flight.

Runs a MotherDuck SQL query and reconciles the membership of a HubSpot static
(MANUAL/SNAPSHOT) contact list so it exactly matches the query output. The query
produces email addresses; the flight resolves them to HubSpot contact IDs, diffs
against the list's current members, and applies the minimal set of add/remove
operations via the Lists v3 `add-and-remove` endpoint.

Design notes
------------
* Reconcile (diff), not clear+re-add: the list is never emptied, and a re-run
  with unchanged data is a no-op (idempotent).
* Emails with no matching HubSpot contact are skipped and logged (run succeeds).
* Robust HTTP: retries with exponential backoff + jitter; honors 429 Retry-After.
* Optional audit-ledger row per run.
* DRY_RUN computes and logs the diff without applying it.

Inputs (env vars; `config` is non-secret, the token comes from a secret)
------------------------------------------------------------------------
  QUERY                 (required) MotherDuck SQL; must output the email column.
  HUBSPOT_LIST_ID       (required) Target static list ID to reconcile.
  EMAIL_COLUMN          (default 'email') Column in the result holding emails.
  OBJECT_TYPE_ID        (default '0-1') HubSpot list object type (contacts).
  OBJECT_NAME           (default 'contacts') CRM object path for batch read.
  ID_PROPERTY           (default 'email') Property used to resolve members.
  BATCH_READ_SIZE       (default 100) Emails per batch-read call (HubSpot cap 100).
  MEMBERSHIP_CHUNK_SIZE (default 1000) Record IDs per membership write call.
  MAX_RETRIES           (default 5) Retry attempts per HTTP op.
  RETRY_BASE_SECONDS    (default 2) Exponential backoff base.
  DRY_RUN               (default 'false') 'true' = log the diff, change nothing.
  AUDIT_TABLE           (default 'hubspot_list_sync.main.flight_tracker') '' to skip.

Secret (flight_secret_names=['hubspot'])
----------------------------------------
  ACCESS_TOKEN -> env HUBSPOT_ACCESS_TOKEN (a HubSpot Service Key or private app
  token). Locally you may instead set HUBSPOT_PRIVATE_APP_TOKEN.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

import duckdb
import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("hubspot-list-sync")

HUBSPOT_BASE = "https://api.hubapi.com"
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class RetryableHTTPError(Exception):
    """HTTP responses that should be retried (429 / 5xx)."""


# --------------------------------------------------------------------------- #
# Config / helpers
# --------------------------------------------------------------------------- #
def resolve_token() -> str:
    """Find the HubSpot token without ever logging it. Accepts the deployed
    secret env var (HUBSPOT_ACCESS_TOKEN), the common local name
    (HUBSPOT_PRIVATE_APP_TOKEN), or any '*_ACCESS_TOKEN' / '*_PRIVATE_APP_TOKEN'
    secret-injected var (excluding MOTHERDUCK_TOKEN)."""
    for name in ("HUBSPOT_ACCESS_TOKEN", "HUBSPOT_PRIVATE_APP_TOKEN"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    for key, value in os.environ.items():
        if key == "MOTHERDUCK_TOKEN":
            continue
        if (key.endswith("_ACCESS_TOKEN") or key.endswith("_PRIVATE_APP_TOKEN")) and value.strip():
            return value.strip()
    raise RuntimeError(
        "No HubSpot token found. Set HUBSPOT_ACCESS_TOKEN (local) or attach the "
        "'hubspot' flight secret with an ACCESS_TOKEN param."
    )


def quote_ident(ident: str) -> str:
    """Quote a SQL identifier, doubling any embedded double quote so the value can
    never break out of its quoted position (defense-in-depth alongside
    validate_table)."""
    return '"' + ident.replace('"', '""') + '"'


def validate_table(value: str) -> str:
    """Validate a 'database.schema.table' name so it is safe to interpolate into
    SQL that cannot be parameterized (the audit ledger target)."""
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError(f"AUDIT_TABLE must be 'database.schema.table', got {value!r}")
    for part in parts:
        if not IDENTIFIER_RE.fullmatch(part):
            raise ValueError(f"AUDIT_TABLE part must be a simple identifier, got {part!r}")
    return value


def chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


# --------------------------------------------------------------------------- #
# HubSpot client
# --------------------------------------------------------------------------- #
class HubSpot:
    """Thin HubSpot CRM v3 client with built-in retry/backoff. The token is held
    only on the httpx client headers and is never logged."""

    def __init__(self, token: str, max_retries: int, base_seconds: float):
        self._client = httpx.Client(
            base_url=HUBSPOT_BASE,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=30.0,
        )
        self._max_retries = max_retries
        self._base_seconds = base_seconds

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, *, json=None, params=None) -> httpx.Response:
        retryer = Retrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._base_seconds, max=60) + wait_random(0, 1),
            retry=retry_if_exception_type((RetryableHTTPError, httpx.TransportError)),
            reraise=True,
        )
        return retryer(self._do, method, path, json, params)

    def _do(self, method: str, path: str, json, params) -> httpx.Response:
        resp = self._client.request(method, path, json=json, params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    time.sleep(min(float(retry_after), 60))
                except ValueError:
                    pass
            raise RetryableHTTPError(f"{resp.status_code} {method} {path}: {resp.text[:200]}")
        resp.raise_for_status()
        return resp

    def get_list(self, list_id: str) -> dict:
        data = self._request("GET", f"/crm/v3/lists/{list_id}").json()
        return data.get("list", data)

    def get_current_member_ids(self, list_id: str) -> set[str]:
        ids: set[str] = set()
        after = None
        while True:
            params = {"limit": 250}
            if after:
                params["after"] = after
            data = self._request("GET", f"/crm/v3/lists/{list_id}/memberships", params=params).json()
            ids.update(str(r["recordId"]) for r in data.get("results", []))
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
        return ids

    def resolve_emails(
        self, emails: list[str], id_property: str, object_name: str, batch_size: int
    ) -> tuple[set[str], list[str]]:
        """Resolve a list of (normalized) emails to HubSpot record IDs. Returns
        (set of resolved record IDs, list of unmatched emails)."""
        resolved: set[str] = set()
        unmatched: list[str] = []
        for batch in chunked(emails, batch_size):
            body = {
                "idProperty": id_property,
                "properties": ["email"],
                "inputs": [{"id": e} for e in batch],
            }
            data = self._request(
                "POST", f"/crm/v3/objects/{object_name}/batch/read", json=body
            ).json()
            matched: set[str] = set()
            for r in data.get("results", []):
                resolved.add(str(r["id"]))
                email = (r.get("properties") or {}).get("email")
                if email:
                    matched.add(email.strip().lower())
            unmatched.extend(e for e in batch if e not in matched)
        return resolved, unmatched

    def add_and_remove(
        self, list_id: str, to_add: set[str], to_remove: set[str], chunk: int
    ) -> tuple[int, int]:
        add_list, rem_list = list(to_add), list(to_remove)
        added = removed = 0
        i = j = 0
        while i < len(add_list) or j < len(rem_list):
            a = add_list[i : i + chunk]
            r = rem_list[j : j + chunk]
            i += len(a)
            j += len(r)
            body = {"recordIdsToAdd": a, "recordIdsToRemove": r}
            data = self._request(
                "PUT", f"/crm/v3/lists/{list_id}/memberships/add-and-remove", json=body
            ).json()
            added += len(data.get("recordIdsAdded", a))
            removed += len(data.get("recordIdsRemoved", r))
        return added, removed


# --------------------------------------------------------------------------- #
# Audit ledger
# --------------------------------------------------------------------------- #
def write_audit(con: duckdb.DuckDBPyConnection, target: str, row: dict) -> None:
    validate_table(target)
    db, schema, table = target.split(".")
    qualified = f"{quote_ident(db)}.{quote_ident(schema)}.{quote_ident(table)}"
    con.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(db)}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(db)}.{quote_ident(schema)}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {qualified} (
            run_id VARCHAR, run_at TIMESTAMPTZ, list_id VARCHAR, query_sha256 VARCHAR,
            n_emails BIGINT, n_resolved BIGINT, n_unmatched BIGINT,
            n_current_before BIGINT, n_added BIGINT, n_removed BIGINT,
            n_final BIGINT, dry_run BOOLEAN, status VARCHAR, detail VARCHAR
        )
        """
    )
    con.execute(
        f"INSERT INTO {qualified} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            row["run_id"], row["run_at"], row["list_id"], row["query_sha256"],
            row["n_emails"], row["n_resolved"], row["n_unmatched"],
            row["n_current_before"], row["n_added"], row["n_removed"],
            row["n_final"], row["dry_run"], row["status"], row["detail"],
        ],
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    run_id = str(uuid.uuid4())
    query = os.environ.get("QUERY", "").strip()
    list_id = os.environ.get("HUBSPOT_LIST_ID", "").strip()
    if not query:
        raise RuntimeError("QUERY is required")
    if not list_id:
        raise RuntimeError("HUBSPOT_LIST_ID is required")

    email_column = os.environ.get("EMAIL_COLUMN", "email").strip()
    object_type_id = os.environ.get("OBJECT_TYPE_ID", "0-1").strip()
    object_name = os.environ.get("OBJECT_NAME", "contacts").strip()
    id_property = os.environ.get("ID_PROPERTY", "email").strip()
    batch_read_size = int(os.environ.get("BATCH_READ_SIZE", "100"))
    membership_chunk = int(os.environ.get("MEMBERSHIP_CHUNK_SIZE", "1000"))
    max_retries = int(os.environ.get("MAX_RETRIES", "5"))
    base_seconds = float(os.environ.get("RETRY_BASE_SECONDS", "2"))
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    audit_table = os.environ.get("AUDIT_TABLE", "hubspot_list_sync.main.flight_tracker").strip()

    token = resolve_token()
    run_at = datetime.now(timezone.utc)
    log.info("Run %s -> list %s (dry_run=%s)", run_id, list_id, dry_run)

    con = duckdb.connect("md:")

    # 1. Run the query and pull the email column (normalize + dedupe).
    result = con.execute(query)
    columns = [d[0] for d in result.description]
    if email_column not in columns:
        raise RuntimeError(f"QUERY result has no {email_column!r} column; columns={columns}")
    idx = columns.index(email_column)
    raw = [r[idx] for r in result.fetchall()]
    emails = sorted({str(e).strip().lower() for e in raw if e is not None and str(e).strip()})
    log.info("Query returned %d row(s); %d distinct non-empty email(s)", len(raw), len(emails))

    hs = HubSpot(token, max_retries, base_seconds)
    try:
        # 2. Guard: the list must be a writable (MANUAL/SNAPSHOT) static list.
        lst = hs.get_list(list_id)
        ptype = lst.get("processingType")
        if ptype not in ("MANUAL", "SNAPSHOT"):
            raise RuntimeError(
                f"List {list_id} processingType={ptype!r}; membership writes require "
                "MANUAL or SNAPSHOT (a static list). Point at a dedicated static list."
            )
        otype = lst.get("objectTypeId")
        if otype and otype != object_type_id:
            log.warning("List objectTypeId=%s differs from OBJECT_TYPE_ID=%s", otype, object_type_id)
        log.info("Target list %r processingType=%s objectTypeId=%s", lst.get("name"), ptype, otype)

        # 3. Resolve emails -> record IDs (skip + log unmatched).
        desired, unmatched = hs.resolve_emails(emails, id_property, object_name, batch_read_size)
        if unmatched:
            log.warning(
                "%d email(s) had no matching %s and were skipped. sample=%s",
                len(unmatched), object_name, unmatched[:10],
            )

        # 4. Diff against current membership.
        current = hs.get_current_member_ids(list_id)
        to_add = desired - current
        to_remove = current - desired
        log.info(
            "desired=%d current=%d add=%d remove=%d unmatched=%d",
            len(desired), len(current), len(to_add), len(to_remove), len(unmatched),
        )

        # 5. Apply (unless dry run).
        if dry_run:
            log.info("DRY_RUN=true; not applying changes.")
            added = removed = 0
            n_final = len(current)
            status = "DRY_RUN"
        else:
            added, removed = hs.add_and_remove(list_id, to_add, to_remove, membership_chunk)
            log.info("Applied: added=%d removed=%d", added, removed)
            n_final = len(desired)
            status = "SUCCEEDED"
    finally:
        hs.close()

    # 6. Audit ledger.
    if audit_table:
        write_audit(
            con,
            audit_table,
            {
                "run_id": run_id,
                "run_at": run_at,
                "list_id": list_id,
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "n_emails": len(emails),
                "n_resolved": len(desired),
                "n_unmatched": len(unmatched),
                "n_current_before": len(current),
                "n_added": added,
                "n_removed": removed,
                "n_final": n_final,
                "dry_run": dry_run,
                "status": status,
                "detail": "",
            },
        )
    con.close()
    log.info(
        "Summary: %s list=%s emails=%d resolved=%d unmatched=%d added=%d removed=%d final=%d",
        status, list_id, len(emails), len(desired), len(unmatched), added, removed, n_final,
    )


if __name__ == "__main__":
    main()
