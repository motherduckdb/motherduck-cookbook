import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import duckdb
from md_migration_assessment.publish import database_name_for


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The Snowflake credentials live in a MotherDuck TYPE flights secret. Each param
# is injected as <secret_name>_<PARAM> (and, when unambiguous, as bare <PARAM>);
# the namespaced form wins so a second secret cannot shadow these.
CREDENTIAL_PARAMS = (
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_PRIVATE_KEY",
    "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
)


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. Credentials are the
    # exception: they come from the Flights secret named by SECRET_NAME.
    profile = env("PROFILE", "standard").lower()
    if profile not in {"lite", "standard"}:
        raise ValueError(f"PROFILE must be 'lite' or 'standard', got {profile!r}")

    history_days = int(env("HISTORY_DAYS", "30") or "30")
    if not 1 <= history_days <= 365:
        raise ValueError(f"HISTORY_DAYS must be between 1 and 365, got {history_days}")

    # Comma-separated DB or DB.SCHEMA entries; md-assess validates each one.
    scope = [s.strip() for s in env("SCOPE", "").split(",") if s.strip()]

    target_db_raw = env("TARGET_DB", "")
    target_db = validate_identifier("TARGET_DB", target_db_raw) if target_db_raw else ""
    dive_title = env("DIVE_TITLE", "")
    replace = env_bool("REPLACE", True)

    resolve_snowflake_credentials(env("SECRET_NAME", "snowflake_creds"))

    # The private collection (which, unlike the handoff, keeps view and routine
    # bodies) only ever exists on this run's scratch disk and is deleted in the
    # finally block. Only the reduced handoff that `md-assess publish` builds
    # is uploaded to MotherDuck.
    workdir = Path(tempfile.mkdtemp(prefix="md-assess-", dir="/tmp"))
    try:
        collection = workdir / "assessment.duckdb"
        collect(collection, profile, history_days, scope)

        database = target_db or database_name_for(collection_deployment(collection))
        guard_existing_database(database, replace)
        publish(collection, database, dive_title, replace)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def collect(collection: Path, profile: str, history_days: int, scope: list[str]) -> None:
    # md-assess prints per-extractor progress to stderr and the fact summary to
    # stdout; both land in the Flight run log. Missing grants do not fail the
    # run: those extractors record an `unavailable` coverage row instead.
    args = [
        "md-assess", "collect",
        "--source", "snowflake",
        "--profile", profile,
        "--history-days", str(history_days),
        "--output", str(collection),
    ]
    for entry in scope:
        args += ["--scope", entry]
    print(f"collect: profile={profile} history_days={history_days} scope={scope or 'account-wide'}")
    subprocess.run(args, check=True)


def collection_deployment(collection: Path) -> str | None:
    con = duckdb.connect(str(collection), read_only=True)
    try:
        row = con.execute(
            "SELECT source_deployment FROM meta.collections ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def guard_existing_database(database: str, replace: bool) -> None:
    # REPLACE drops and re-uploads the target database, which is what a re-run
    # wants for a database this Flight created. Refuse to drop anything that is
    # not an earlier assessment, so a typo in TARGET_DB cannot destroy real data.
    con = duckdb.connect("md:")
    try:
        exists = con.execute(
            "SELECT count(*) FROM duckdb_databases() WHERE database_name = ?", [database]
        ).fetchone()[0]
        if not exists:
            return
        if not replace:
            raise RuntimeError(
                f"MotherDuck database {database!r} already exists and REPLACE=false; "
                "set REPLACE=true to refresh it or pick another TARGET_DB"
            )
        is_assessment = con.execute(
            "SELECT count(*) FROM duckdb_tables() "
            "WHERE database_name = ? AND schema_name = 'meta' AND table_name = 'collections'",
            [database],
        ).fetchone()[0]
        if not is_assessment:
            raise RuntimeError(
                f"MotherDuck database {database!r} exists but is not an md-assess handoff "
                "(no meta.collections table); refusing to replace it. Pick another TARGET_DB."
            )
        print(f"publish: replacing earlier assessment database {database!r}")
    finally:
        con.close()


def publish(collection: Path, database: str, dive_title: str, replace: bool) -> None:
    # `md-assess publish` builds the reduced handoff (no source bodies, no query
    # text), uploads it with CREATE DATABASE ... FROM, and creates or updates
    # the dashboard Dive over it. MOTHERDUCK_TOKEN is injected by the Flight.
    args = ["md-assess", "publish", "--db", str(collection), "--name", database, "--json"]
    if dive_title:
        args += ["--title", dive_title]
    if replace:
        args.append("--replace")
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    if result.stderr:
        print(result.stderr, end="")
    summary = json.loads(result.stdout)

    # Log the disclosure review, not the full per-column manifest.
    print(f"publish: database md:{summary['database']} "
          f"({summary['handoff_tables']} tables, {summary['handoff_rows']:,} rows)")
    print(f"publish: Dive {summary['dive_title']!r} "
          f"{'created' if summary['dive_created'] else 'updated'}: {summary['dive_url']}")
    for cls, cols in sorted(summary["sensitive_included"].items()):
        print(f"publish: disclosed {cls}: {len(cols)} column(s)")
    if summary["excluded_columns"]:
        print(f"publish: excluded columns: {', '.join(summary['excluded_columns'])}")
    # Unclassified columns are almost all counts, bytes, timestamps, and
    # statuses, so log how many rather than every name.
    if summary["unclassified_included"]:
        print(f"publish: unclassified columns included: {len(summary['unclassified_included'])}")
    for key in ("dropped_unexpected", "skipped_raw_tables"):
        if summary[key]:
            print(f"publish: WARNING {key}: {', '.join(summary[key])}")


def resolve_snowflake_credentials(secret_name: str) -> None:
    # md-assess reads SNOWFLAKE_* env vars. Copy each credential from the
    # namespaced secret param onto the bare name it expects, so a raw alias from
    # some other secret on the Flight cannot win.
    for param in CREDENTIAL_PARAMS:
        value = os.environ.get(f"{secret_name}_{param}")
        if value:
            os.environ[param] = value

    if os.environ.get("SNOWFLAKE_AUTHENTICATOR", "").lower() == "externalbrowser":
        raise ValueError(
            "SNOWFLAKE_AUTHENTICATOR=externalbrowser needs a local browser; a Flight "
            "cannot complete SSO. Use key-pair (SNOWFLAKE_PRIVATE_KEY) or a password."
        )

    # The connector wants key-pair auth as a file path. A secret holds the PEM
    # text, so write it to a 0600 file on the run's scratch disk.
    private_key = os.environ.pop("SNOWFLAKE_PRIVATE_KEY", "")
    if private_key:
        fd, key_path = tempfile.mkstemp(prefix="sf-key-", suffix=".p8", dir="/tmp")
        with os.fdopen(fd, "w") as handle:
            handle.write(private_key.replace("\\n", "\n").strip() + "\n")
        os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"] = key_path

    missing = [n for n in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER") if not os.environ.get(n)]
    if missing:
        raise ValueError(
            f"{' and '.join(missing)} not set: put SNOWFLAKE_ACCOUNT in Flight config and "
            f"SNOWFLAKE_USER in config or the {secret_name!r} Flights secret"
        )
    if not (os.environ.get("SNOWFLAKE_PASSWORD") or os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")):
        raise ValueError(
            f"no Snowflake credential: add SNOWFLAKE_PRIVATE_KEY (preferred) or "
            f"SNOWFLAKE_PASSWORD to the {secret_name!r} Flights secret"
        )
    if not os.environ.get("SNOWFLAKE_WAREHOUSE"):
        print("warning: SNOWFLAKE_WAREHOUSE is not set; the collector falls back to the "
              "user's default warehouse, and extracts that need one fail without it")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must match {IDENTIFIER_RE.pattern}, got {value!r}")
    return value


if __name__ == "__main__":
    main()
