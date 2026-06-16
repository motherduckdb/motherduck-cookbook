import os
import re

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Seeded into the control table on the first run so the Flight has users to act
# on. Replace these with real MotherDuck usernames (or point USERS_TABLE at your
# own table) before running with DRY_RUN disabled.
DEMO_USERS = [
    ("analyst_one@example.com", "starter", True),
    ("analyst_two@example.com", "growth", True),
    ("former_user@example.com", "starter", False),
]


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code.
    provision_db = validate_identifier("PROVISION_DATABASE", env("PROVISION_DATABASE", "flights_demo"))
    provision_schema = validate_identifier("PROVISION_SCHEMA", env("PROVISION_SCHEMA", "main"))
    users_table = validate_identifier("USERS_TABLE", env("USERS_TABLE", "flight_users"))
    ledger_table = validate_identifier("LEDGER_TABLE", env("LEDGER_TABLE", "user_database_map"))
    database_prefix = validate_identifier("DATABASE_PREFIX", env("DATABASE_PREFIX", "user_dw_"))
    share_suffix = validate_identifier("SHARE_SUFFIX", env("SHARE_SUFFIX", "_share"))
    user_schema = validate_identifier("USER_SCHEMA", env("USER_SCHEMA", "app"))
    # DRY_RUN defaults to true: the first run logs the plan and writes the ledger
    # without creating any account-level databases, shares, or grants. Set
    # DRY_RUN=false to provision for real once the users table holds real usernames.
    dry_run = env_bool("DRY_RUN", True)

    control = f"{provision_db}.{provision_schema}"
    users_fqn = f"{control}.{users_table}"
    ledger_fqn = f"{control}.{ledger_table}"

    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {provision_db}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {control}")
    seed_demo_users(con, provision_db, provision_schema, users_table, users_fqn)
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ledger_fqn} (
            email VARCHAR,
            database_name VARCHAR,
            share_name VARCHAR,
            active BOOLEAN,
            dry_run BOOLEAN,
            processed_at TIMESTAMPTZ
        )
        """
    )

    users = con.execute(
        f"SELECT email, segment, active FROM {users_fqn} ORDER BY email"
    ).fetchall()

    mode = "dry-run" if dry_run else "live"
    print(f"provisioning {len(users)} user(s) in {mode} mode")

    for email, segment, active in users:
        database_name = database_prefix + slug(email)
        share_name = database_name + share_suffix

        if active:
            provision_active(con, database_name, share_name, user_schema, email, segment, dry_run)
        else:
            deprovision_inactive(con, share_name, email, dry_run)

        con.execute(
            f"INSERT INTO {ledger_fqn} VALUES (?, ?, ?, ?, ?, current_timestamp)",
            [email, database_name, share_name, active, dry_run],
        )

    con.close()
    print(f"provisioning complete ({mode})")


def provision_active(
    con: duckdb.DuckDBPyConnection,
    database_name: str,
    share_name: str,
    user_schema: str,
    email: str,
    segment: str,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"[dry-run] would create database {database_name} and share {share_name}, then grant {email}")
        return

    con.execute(f"CREATE DATABASE IF NOT EXISTS {ident(database_name)}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {ident(database_name)}.{ident(user_schema)}")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {ident(database_name)}.{ident(user_schema)}.profile AS
        SELECT ? AS email, ? AS segment, current_timestamp AS provisioned_at
        """,
        [email, segment],
    )
    con.execute(
        f"""
        CREATE SHARE IF NOT EXISTS {ident(share_name)}
        FROM {ident(database_name)} (
            ACCESS RESTRICTED,
            VISIBILITY HIDDEN,
            UPDATE AUTOMATIC
        )
        """
    )
    # The username must be a valid MotherDuck user in the same sharing scope, so a
    # bad address fails only this grant instead of the whole run.
    try:
        con.execute(f"GRANT READ ON SHARE {ident(share_name)} TO {ident(email)}")
        print(f"granted {email} access to {share_name}")
    except Exception as exc:
        print(f"skipped grant for {email}: {exc}")


def deprovision_inactive(
    con: duckdb.DuckDBPyConnection,
    share_name: str,
    email: str,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"[dry-run] would revoke {email} from {share_name}")
        return
    try:
        con.execute(f"REVOKE READ ON SHARE {ident(share_name)} FROM {ident(email)}")
        print(f"revoked {email} from {share_name}")
    except Exception as exc:
        print(f"skipped revoke for {email}: {exc}")


def seed_demo_users(
    con: duckdb.DuckDBPyConnection,
    provision_db: str,
    provision_schema: str,
    users_table: str,
    users_fqn: str,
) -> None:
    # Seed a demo control table on the first run so the Flight has users to act on.
    # An existing table is left untouched, so a real users table is never
    # overwritten.
    existing = con.execute(
        """
        SELECT count(*)
        FROM duckdb_tables()
        WHERE database_name = ? AND schema_name = ? AND table_name = ?
        """,
        [provision_db, provision_schema, users_table],
    ).fetchone()[0]
    if existing:
        return

    con.execute(f"CREATE TABLE {users_fqn} (email VARCHAR, segment VARCHAR, active BOOLEAN)")
    con.executemany(f"INSERT INTO {users_fqn} VALUES (?, ?, ?)", DEMO_USERS)
    print(f"seeded demo users into {users_fqn}")


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def validate_identifier(name: str, value: str) -> str:
    # Config-supplied database, schema, and table names flow into SQL that cannot
    # be parameterized, so reject anything that is not a plain SQL identifier
    # before any SQL runs. Per-user database, share, schema, and username values
    # are derived at runtime and quoted with ident() instead.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def ident(value: str) -> str:
    # Quote a dynamic SQL identifier by escaping embedded double quotes.
    return '"' + value.replace('"', '""') + '"'


def slug(email: str) -> str:
    # Turn an email into a safe database-name fragment: lowercase the local part,
    # collapse non-identifier characters to underscores, and cap the length.
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", email.split("@")[0].lower()).strip("_")
    return value[:40] or "user"


if __name__ == "__main__":
    main()
