"""MotherDuck Flight: Apache Iceberg REST catalog -> MotherDuck copy.

Works with any Iceberg REST catalog MotherDuck supports: Amazon S3 Tables, AWS Glue
(with or without Lake Formation), Apache Polaris, Cloudflare R2 Data Catalog, Databricks
Unity Catalog (external tables) and Snowflake Horizon (external volumes), or any other
REST endpoint. CATALOG picks a preset that fills in the documented defaults and checks.

What each run does
    1. Resolves the catalog settings from config and the CATALOG preset, and stops on a
       setting the preset can't use (for example an S3 Tables warehouse that isn't a
       table bucket ARN, or a Glue warehouse that isn't a 12-digit account ID).
    2. Checks that the MotherDuck secret named by ICEBERG_SECRET exists and has the type
       the catalog needs (S3 for s3_tables/glue, ICEBERG for the rest). Only names and
       types are read from duckdb_secrets(); values are never read or printed.
    3. Attaches the catalog as a persisted MotherDuck database (CATALOG_DB) with
           CREATE [OR REPLACE] DATABASE <CATALOG_DB> (TYPE ICEBERG, "secret" <name>, ...)
       read-only by default. An error catalog stops the run with the catalog's
       error_message plus a catalog-specific hint; a database created by this run is
       dropped again so nothing broken is left behind.
    4. Discovers namespaces and tables, applies the include/exclude filters, and stops
       before copying if an included namespace or table doesn't exist.
    5. Copies each table with one CREATE OR REPLACE TABLE ... AS SELECT * into
       <TARGET_DB>.<namespace>.<table>, retrying transient errors with backoff.
       Authentication and permission errors fail at once. Row counts in MotherDuck are
       compared with a fresh count in the catalog; a difference is a warning.
    6. Writes one row per table to <TARGET_DB>._meta.load_log and exits non-zero if any
       table failed; the other tables still copy.

Credentials
    Catalog credentials live in a MotherDuck secret (TYPE S3 or TYPE ICEBERG, created with
    CREATE SECRET ... IN MOTHERDUCK or in Settings -> Secrets). The Flight references it by
    name in the CREATE DATABASE options. It is not a Flights secret and is not passed with
    flight_secret_names. No credential appears in config, code, SQL text or logs.

Config (non-secret; an empty value means the default)
    CATALOG                 s3_tables | glue | polaris | r2 | unity | horizon | rest
                                                                    default: rest
    ICEBERG_SECRET          name of the MotherDuck secret            required
    ENDPOINT                Iceberg REST catalog URL                 derived for s3_tables/glue
    WAREHOUSE               catalog warehouse identifier             see the preset table
    DEFAULT_SCHEMA          namespace the attach opens with          default: first included
                                                                    namespace, else 'default'
    DEFAULT_REGION          per-catalog region override              default: org region
    ACCESS_DELEGATION_MODE  vended_credentials | none                default: catalog default
                                                                    (vended_credentials)
    READ_ONLY               attach read-only (true/false)            default: true
    CATALOG_DB              alias of the persisted catalog database  default: iceberg_catalog
    INCLUDED_NAMESPACES / EXCLUDED_NAMESPACES   comma-separated namespaces
    INCLUDED_TABLES / EXCLUDED_TABLES           comma-separated namespace.table or bare table
    TARGET_DB               MotherDuck database to copy into         default: iceberg_copy
    MAX_RETRIES             attempts per table on transient errors   default: 3
    MOTHERDUCK_HOST         MotherDuck API host override             default: unset

requirements.txt
    duckdb==1.5.6
"""
from __future__ import annotations

import os
import random
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb

META_SCHEMA = "_meta"
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
S3_TABLES_ARN_RE = re.compile(
    r"arn:aws:s3tables:([a-z0-9-]+):(\d{12}):bucket/[a-z0-9][a-z0-9-]{1,61}[a-z0-9]"
)
AWS_ACCOUNT_RE = re.compile(r"\d{12}")
R2_ENDPOINT_RE = re.compile(
    r"https://catalog\.cloudflarestorage\.com/([A-Za-z0-9]+)/([a-z0-9][a-z0-9-]*[a-z0-9])/?"
)
UNITY_PATH = "/api/2.1/unity-catalog/iceberg-rest"
DELEGATION_MODES = {"vended_credentials", "none"}

# Errors that retrying can't fix: the table fails at once.
PERMANENT_ERRORS = (
    "401", "403", "unauthorized", "forbidden", "permission", "access denied",
    "access is not allowed", "not authorized", "insufficient lake formation",
    "missing or invalid credentials", "full select", "does not exist", "not found",
    "not implemented",
)


class FlightError(RuntimeError):
    """An expected failure with a clear message; logged without a traceback."""


@dataclass(frozen=True)
class Preset:
    """Documented defaults and checks for one catalog. See the README's preset table."""
    label: str
    secret_type: str          # duckdb_secrets().type the catalog needs
    endpoint_type: str = ""   # passed as endpoint_type; the endpoint is then derived
    endpoint: str = "forbidden"   # "forbidden" | "required" | "optional"
    warehouse: str = "required"   # "required" | "optional" | "derived"
    secret_hint: str = ""
    error_hint: str = ""


PRESETS: dict[str, Preset] = {
    "s3_tables": Preset(
        label="Amazon S3 Tables",
        secret_type="s3",
        endpoint_type="s3_tables",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE S3, KEY_ID '<aws_access_key_id>', "
            "SECRET '<aws_secret_access_key>', REGION '<region>'). The IAM user needs "
            "S3 Tables catalog access (s3tables:*) in the bucket's account."
        ),
        error_hint=(
            "Check that WAREHOUSE is the table bucket ARN, that the S3 secret's keys have "
            "s3tables:* permissions in the bucket's account, and that DEFAULT_SCHEMA names "
            "a namespace that exists in the bucket."
        ),
    ),
    "glue": Preset(
        label="AWS Glue",
        secret_type="s3",
        endpoint_type="glue",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE S3, KEY_ID '<aws_access_key_id>', "
            "SECRET '<aws_secret_access_key>', REGION '<aws_region>'). The Glue endpoint "
            "is derived from the secret's REGION."
        ),
        error_hint=(
            "Check that WAREHOUSE is the 12-digit AWS account ID, that the S3 secret's REGION "
            "is the Glue catalog's region, and that DEFAULT_SCHEMA is a Glue database that "
            "exists. The IAM principal needs glue:GetCatalog, glue:GetDatabase(s) and "
            "glue:GetTable(s); without Lake Formation also s3:GetObject (and kms:Decrypt for "
            "SSE-KMS); with Lake Formation lakeformation:GetDataAccess plus DESCRIBE/SELECT "
            "grants on whole tables."
        ),
    ),
    "polaris": Preset(
        label="Apache Polaris",
        secret_type="iceberg",
        endpoint="optional",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE ICEBERG, CLIENT_ID '<client_id>', "
            "CLIENT_SECRET '<client_secret>', OAUTH2_SERVER_URI '<token_endpoint>'), or "
            "TYPE ICEBERG with TOKEN '<bearer_token>'."
        ),
        error_hint=(
            "Check ENDPOINT (the Polaris REST URL), that WAREHOUSE is the Polaris catalog "
            "name, that the principal's OAuth2 credentials are valid, and that "
            "DEFAULT_SCHEMA exists."
        ),
    ),
    "r2": Preset(
        label="Cloudflare R2 Data Catalog",
        secret_type="iceberg",
        endpoint="required",
        warehouse="derived",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE ICEBERG, TOKEN '<cloudflare_api_token>'). "
            "The token must be a Cloudflare API token with R2 Data Catalog permission (for "
            "example an Admin Read & Write R2 token); an S3 access key is not enough."
        ),
        error_hint=(
            "401 Unauthorized means the token isn't a Cloudflare API token; 403 Forbidden "
            "means it lacks R2 Data Catalog permission. Check that the catalog is enabled on "
            "the bucket, that ENDPOINT and WAREHOUSE match the bucket's R2 Data Catalog "
            "settings, and that DEFAULT_SCHEMA names an existing namespace (a new catalog "
            "is empty)."
        ),
    ),
    "unity": Preset(
        label="Databricks Unity Catalog",
        secret_type="iceberg",
        endpoint="required",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE ICEBERG, "
            "TOKEN '<databricks_personal_access_token>')."
        ),
        error_hint=(
            "Check that ENDPOINT is https://<workspace-url>/api/2.1/unity-catalog/iceberg-rest, "
            "that WAREHOUSE is the Unity Catalog catalog name and DEFAULT_SCHEMA a schema in "
            "it. 'Missing or invalid credentials' often means the table is on Unity "
            "Catalog managed storage: Databricks vends credentials only for external "
            "locations."
        ),
    ),
    "horizon": Preset(
        label="Snowflake Horizon",
        secret_type="iceberg",
        endpoint="optional",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE ICEBERG, CLIENT_ID '<client_id>', "
            "CLIENT_SECRET '<client_secret>', OAUTH2_SERVER_URI '<oauth_token_endpoint>', "
            "OAUTH2_SCOPE 'session:role:<supported_role>')."
        ),
        error_hint=(
            "Check ENDPOINT, WAREHOUSE and DEFAULT_SCHEMA, the OAUTH2_SCOPE role, and that "
            "the tables are Iceberg tables on an external volume."
        ),
    ),
    "rest": Preset(
        label="Iceberg REST catalog",
        secret_type="iceberg",
        endpoint="optional",
        warehouse="optional",
        secret_hint=(
            "CREATE SECRET <name> IN MOTHERDUCK (TYPE ICEBERG, CLIENT_ID '<client_id>', "
            "CLIENT_SECRET '<client_secret>', OAUTH2_SERVER_URI '<token_endpoint>'), or "
            "TYPE ICEBERG with TOKEN '<bearer_token>'."
        ),
        error_hint=(
            "Check ENDPOINT (or the secret's ENDPOINT), WAREHOUSE, the secret's credentials, "
            "and that DEFAULT_SCHEMA names a namespace that exists."
        ),
    ),
}


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


def cfg(name: str, default: str = "") -> str:
    """Config value; unset or empty (after trimming) falls back to the default."""
    return (os.environ.get(name) or "").strip() or default


def qi(name: str) -> str:
    """Quote an identifier, escaping embedded double quotes."""
    return '"' + name.replace('"', '""') + '"'


def ql(value: str) -> str:
    """Quote a string literal, escaping embedded single quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a plain identifier (letters, digits, _), got {value!r}")
    return value


def _unquote(part: str) -> str:
    part = part.strip()
    if len(part) >= 2 and part[0] == '"' and part[-1] == '"':
        return part[1:-1].replace('""', '"')
    return part


def _split_outside_quotes(text: str, sep: str) -> list[str]:
    parts, buf, in_quotes = [], [], False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
        if ch == sep and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_namespaces(raw: str) -> list[str]:
    """'sales, "Ops"' -> ['sales', 'Ops']. Order kept, duplicates dropped."""
    out: list[str] = []
    for entry in _split_outside_quotes(raw or "", ","):
        name = _unquote(entry)
        if name and name not in out:
            out.append(name)
    return out


def parse_tables(raw: str) -> list[tuple[str | None, str]]:
    """'sales.orders, events' -> [('sales', 'orders'), (None, 'events')]. A bare table
    matches that table in any selected namespace. Quotes protect dots in names."""
    out: list[tuple[str | None, str]] = []
    for entry in _split_outside_quotes(raw or "", ","):
        parts = [p for p in (_unquote(x) for x in _split_outside_quotes(entry, ".")) if p]
        if not parts:
            continue
        spec = (None, parts[0]) if len(parts) == 1 else (".".join(parts[:-1]), parts[-1])
        if spec not in out:
            out.append(spec)
    return out


def parse_bool(name: str, raw: str, default: bool) -> bool:
    if not raw:
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def is_permanent(exc: Exception) -> bool:
    if isinstance(exc, (duckdb.CatalogException, duckdb.BinderException,
                        duckdb.ParserException, duckdb.PermissionException)):
        return True
    return any(s in str(exc).lower() for s in PERMANENT_ERRORS)


# --------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------
@dataclass
class Settings:
    catalog: str
    preset: Preset
    secret: str
    endpoint: str
    warehouse: str
    default_schema: str
    default_region: str
    access_delegation_mode: str
    read_only: bool
    catalog_db: str
    target_db: str
    included_namespaces: list[str]
    excluded_namespaces: list[str]
    included_tables: list[tuple[str | None, str]]
    excluded_tables: list[tuple[str | None, str]]
    max_retries: int
    notes: list[str] = field(default_factory=list)


def pick_default_schema(explicit: str, namespaces: list[str],
                        tables: list[tuple[str | None, str]]) -> tuple[str, str]:
    """The attach needs a default_schema that exists in the catalog. Use DEFAULT_SCHEMA,
    else the first included namespace, else the namespace of the first qualified included
    table, else 'default'. Returns (schema, where it came from)."""
    if explicit:
        return _unquote(explicit), "DEFAULT_SCHEMA"
    if namespaces:
        return namespaces[0], "INCLUDED_NAMESPACES"
    for ns, _ in tables:
        if ns:
            return ns, "INCLUDED_TABLES"
    return "default", "fallback"


def resolve_settings(env: dict[str, str] | None = None) -> Settings:
    """Read config, apply the CATALOG preset, and validate. Raises ValueError with a
    message that names the setting to fix."""
    env = os.environ if env is None else env

    def get(name: str, default: str = "") -> str:
        return (env.get(name) or "").strip() or default

    catalog = get("CATALOG", "rest").lower().replace("-", "_")
    if catalog not in PRESETS:
        raise ValueError(f"CATALOG must be one of {sorted(PRESETS)}, got {catalog!r}")
    p = PRESETS[catalog]
    notes: list[str] = []

    secret = get("ICEBERG_SECRET")
    if not secret:
        raise ValueError(
            f"ICEBERG_SECRET is required: the name of a MotherDuck {p.secret_type.upper()} "
            f"secret for {p.label}. Create one with {p.secret_hint}"
        )
    identifier("ICEBERG_SECRET", secret)

    endpoint = get("ENDPOINT")
    warehouse = get("WAREHOUSE")

    # Endpoint rules
    if p.endpoint == "forbidden" and endpoint:
        raise ValueError(
            f"Leave ENDPOINT empty for CATALOG={catalog}: {p.label} sets endpoint_type "
            f"'{p.endpoint_type}' and the endpoint is derived "
            + ("from the bucket ARN." if catalog == "s3_tables" else "from the secret's REGION.")
        )
    if p.endpoint == "required" and not endpoint:
        example = {
            "r2": "https://catalog.cloudflarestorage.com/<account_id>/<bucket_name>",
            "unity": f"https://<workspace-url>{UNITY_PATH}",
        }.get(catalog, "https://...")
        raise ValueError(f"ENDPOINT is required for CATALOG={catalog} ({p.label}), e.g. {example}")
    if endpoint and not endpoint.lower().startswith(("https://", "http://")):
        raise ValueError(f"ENDPOINT must be an http(s) URL, got {endpoint!r}")
    if endpoint.lower().startswith("http://"):
        notes.append("ENDPOINT uses http://; credentials would travel unencrypted. Use https://.")
    if not endpoint and p.endpoint == "optional":
        notes.append("ENDPOINT is empty, so the secret must hold the catalog's ENDPOINT.")

    # Warehouse rules
    if catalog == "s3_tables":
        if not warehouse:
            raise ValueError(
                "WAREHOUSE is required for CATALOG=s3_tables: the table bucket ARN, "
                "arn:aws:s3tables:<region>:<account_id>:bucket/<bucket_name>"
            )
        if not S3_TABLES_ARN_RE.fullmatch(warehouse):
            raise ValueError(
                "WAREHOUSE for CATALOG=s3_tables must be a table bucket ARN like "
                f"arn:aws:s3tables:us-east-1:123456789012:bucket/my-bucket, got {warehouse!r}"
            )
    elif catalog == "glue":
        if not AWS_ACCOUNT_RE.fullmatch(warehouse):
            raise ValueError(
                "WAREHOUSE for CATALOG=glue must be the 12-digit AWS account ID that owns "
                f"the Glue Data Catalog, got {warehouse!r}"
            )
    elif catalog == "r2":
        m = R2_ENDPOINT_RE.fullmatch(endpoint)
        if m:
            expected = f"{m.group(1)}_{m.group(2)}"
            if not warehouse:
                warehouse = expected
                notes.append(f"WAREHOUSE derived from ENDPOINT as {warehouse!r}.")
            elif warehouse != expected:
                notes.append(
                    f"WAREHOUSE {warehouse!r} doesn't match ENDPOINT (expected {expected!r}); "
                    "copy both from the bucket's R2 Data Catalog settings."
                )
        else:
            notes.append(
                "ENDPOINT doesn't look like https://catalog.cloudflarestorage.com/<account_id>/"
                "<bucket_name>; copy it from the bucket's R2 Data Catalog settings."
            )
            if not warehouse:
                raise ValueError(
                    "WAREHOUSE is required for CATALOG=r2: <account_id>_<bucket_name>, as shown "
                    "in the bucket's R2 Data Catalog settings"
                )
    elif p.warehouse == "required" and not warehouse:
        what = {
            "polaris": "the Polaris catalog name",
            "unity": "the Unity Catalog catalog name",
            "horizon": "the Snowflake warehouse identifier",
        }[catalog]
        raise ValueError(f"WAREHOUSE is required for CATALOG={catalog}: {what}")
    if catalog == "unity" and not endpoint.rstrip("/").endswith(UNITY_PATH):
        notes.append(f"Unity Catalog's Iceberg REST endpoint usually ends with {UNITY_PATH}.")

    mode = get("ACCESS_DELEGATION_MODE").lower()
    if mode and mode not in DELEGATION_MODES:
        raise ValueError(f"ACCESS_DELEGATION_MODE must be one of {sorted(DELEGATION_MODES)}, got {mode!r}")

    namespaces_in = parse_namespaces(get("INCLUDED_NAMESPACES"))
    tables_in = parse_tables(get("INCLUDED_TABLES"))
    default_schema, source = pick_default_schema(get("DEFAULT_SCHEMA"), namespaces_in, tables_in)
    if source == "fallback":
        notes.append(
            "DEFAULT_SCHEMA is empty and no namespace is named in INCLUDED_NAMESPACES or "
            "INCLUDED_TABLES, so the attach uses 'default'. Set DEFAULT_SCHEMA if the "
            "catalog has no 'default' namespace."
        )

    catalog_db = identifier("CATALOG_DB", get("CATALOG_DB", "iceberg_catalog"))
    target_db = identifier("TARGET_DB", get("TARGET_DB", "iceberg_copy"))
    if catalog_db.lower() == target_db.lower():
        raise ValueError("CATALOG_DB and TARGET_DB must be different databases")

    retries_raw = get("MAX_RETRIES", "3")
    if not retries_raw.isdigit() or int(retries_raw) < 1:
        raise ValueError(f"MAX_RETRIES must be a positive integer, got {retries_raw!r}")

    return Settings(
        catalog=catalog,
        preset=p,
        secret=secret,
        endpoint=endpoint,
        warehouse=warehouse,
        default_schema=default_schema,
        default_region=get("DEFAULT_REGION"),
        access_delegation_mode=mode,
        read_only=parse_bool("READ_ONLY", get("READ_ONLY"), True),
        catalog_db=catalog_db,
        target_db=target_db,
        included_namespaces=namespaces_in,
        excluded_namespaces=parse_namespaces(get("EXCLUDED_NAMESPACES")),
        included_tables=tables_in,
        excluded_tables=parse_tables(get("EXCLUDED_TABLES")),
        max_retries=int(retries_raw),
        notes=notes,
    )


def attach_options(s: Settings) -> list[str]:
    """The CREATE DATABASE option list. Only catalog settings and the secret's name;
    credentials live in the secret and never reach SQL text."""
    opts = ["TYPE ICEBERG", f'"secret" {qi(s.secret)}']
    if s.preset.endpoint_type:
        opts.append(f"endpoint_type {ql(s.preset.endpoint_type)}")
    if s.endpoint:
        opts.append(f"endpoint {ql(s.endpoint)}")
    if s.warehouse:
        opts.append(f"warehouse {ql(s.warehouse)}")
    opts.append(f"default_schema {ql(s.default_schema)}")
    if s.default_region:
        opts.append(f"default_region {ql(s.default_region)}")
    if s.access_delegation_mode:
        opts.append(f"access_delegation_mode {ql(s.access_delegation_mode)}")
    opts.append(f"read_only {'true' if s.read_only else 'false'}")
    return opts


def attach_sql(s: Settings, replace: bool) -> str:
    verb = "CREATE OR REPLACE DATABASE" if replace else "CREATE DATABASE"
    return f"{verb} {qi(s.catalog_db)} ({', '.join(attach_options(s))})"


# --------------------------------------------------------------------------------------
# MotherDuck steps
# --------------------------------------------------------------------------------------
def connect_motherduck() -> duckdb.DuckDBPyConnection:
    host = cfg("MOTHERDUCK_HOST")
    if host:
        os.environ["motherduck_host"] = host
        log(f"Using MotherDuck host {host}")
    return duckdb.connect("md:")


def check_secret(con, s: Settings) -> None:
    """Confirm the secret exists and has the right type. Reads names and types only."""
    rows = con.execute(
        "SELECT name, lower(type) FROM duckdb_secrets()"
    ).fetchall()
    types = {name.lower(): typ for name, typ in rows}
    want = s.preset.secret_type
    found = types.get(s.secret.lower())
    if found is None:
        same_type = sorted(n for n, t in rows if t == want)
        raise FlightError(
            f"No MotherDuck secret named {s.secret!r} was found. {s.preset.label} needs a "
            f"TYPE {want.upper()} secret, created in Settings -> Secrets or with "
            f"{s.preset.secret_hint} "
            f"{want.upper()} secrets visible to this run: {same_type or 'none'}."
        )
    if found != want:
        raise FlightError(
            f"Secret {s.secret!r} is TYPE {found.upper()}, but {s.preset.label} needs a TYPE "
            f"{want.upper()} secret: {s.preset.secret_hint}"
        )
    log(f"Secret {s.secret!r} found (TYPE {found.upper()})")


def existing_database_type(con, alias: str) -> str | None:
    row = con.execute(
        "SELECT type FROM MD_ALL_DATABASES() WHERE lower(alias) = lower(?)", [alias]
    ).fetchone()
    return row[0] if row else None


def attach_catalog(con, s: Settings) -> None:
    existing = existing_database_type(con, s.catalog_db)
    if existing is not None and "iceberg" not in existing.lower():
        raise FlightError(
            f"CATALOG_DB {s.catalog_db!r} is already a {existing!r} database. Pick another "
            "CATALOG_DB; this Flight only replaces Iceberg catalog databases."
        )
    created = existing is None
    con.execute(attach_sql(s, replace=not created))
    row = con.execute(
        "SELECT is_error_catalog, error_message FROM MD_ATTACHED_DATABASES() "
        "WHERE lower(alias) = lower(?)",
        [s.catalog_db],
    ).fetchone()
    if row is None or row[0]:
        message = row[1] if row else "database not attached"
        cleanup = ""
        if created:
            con.execute(f"DROP DATABASE IF EXISTS {qi(s.catalog_db)}")
            cleanup = f" The database {s.catalog_db!r} created by this run was dropped again."
        else:
            cleanup = (f" {s.catalog_db!r} existed before this run and now holds these "
                       "settings; fix the config and re-run, or drop it.")
        raise FlightError(
            f"Attaching {s.preset.label} as {s.catalog_db!r} failed: {message}. "
            f"{s.preset.error_hint} (default_schema used: {s.default_schema!r}.)" + cleanup
        )
    log(f"Attached {s.preset.label} as {s.catalog_db} "
        f"({'read-only' if s.read_only else 'read-write'}, default_schema {s.default_schema!r})")


def discover(con, catalog_db: str) -> tuple[list[str], list[tuple[str, str]]]:
    schemas = [r[0] for r in con.execute(
        "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ? "
        "AND schema_name NOT IN ('information_schema', 'pg_catalog') ORDER BY 1",
        [catalog_db],
    ).fetchall()]
    tables = [(r[0], r[1]) for r in con.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = ? "
        "ORDER BY 1, 2",
        [catalog_db],
    ).fetchall()]
    return schemas, tables


def select_tables(s: Settings, namespaces: list[str],
                  tables: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Apply the filters; unknown included names stop the run. Matching ignores case.
    Excludes win over includes."""
    by_ns = {n.casefold(): n for n in namespaces}
    by_ns.update({n.casefold(): n for n, _ in tables})
    unknown_ns = [n for n in s.included_namespaces if n.casefold() not in by_ns]
    if unknown_ns:
        raise FlightError(
            f"INCLUDED_NAMESPACES not found in the catalog: {unknown_ns}. "
            f"Namespaces visible: {sorted(by_ns.values())}"
        )
    inc_ns = {n.casefold() for n in s.included_namespaces}
    exc_ns = {n.casefold() for n in s.excluded_namespaces}

    def ns_ok(ns: str) -> bool:
        return (not inc_ns or ns.casefold() in inc_ns) and ns.casefold() not in exc_ns

    def matches(spec: tuple[str | None, str], ns: str, table: str) -> bool:
        sns, stable = spec
        return stable.casefold() == table.casefold() and (sns is None or sns.casefold() == ns.casefold())

    candidates = [(ns, t) for ns, t in tables if ns_ok(ns)]
    if s.included_tables:
        unknown = [
            spec for spec in s.included_tables
            if not any(matches(spec, ns, t) for ns, t in candidates)
        ]
        if unknown:
            names = [f"{a}.{b}" if a else b for a, b in unknown]
            raise FlightError(
                f"INCLUDED_TABLES not found in the selected namespaces: {names}. Use "
                "namespace.table, and check INCLUDED_NAMESPACES / EXCLUDED_NAMESPACES."
            )
        candidates = [(ns, t) for ns, t in candidates
                      if any(matches(spec, ns, t) for spec in s.included_tables)]
    selected = [(ns, t) for ns, t in candidates
                if not any(matches(spec, ns, t) for spec in s.excluded_tables)]

    # Target names are case-insensitive in MotherDuck; refuse collisions before copying.
    seen: dict[tuple[str, str], str] = {}
    for ns, t in selected:
        if ns.casefold() == META_SCHEMA:
            raise FlightError(f"Namespace {META_SCHEMA!r} is reserved for the load log; exclude it")
        key = (ns.casefold(), t.casefold())
        if key in seen:
            raise FlightError(f"{seen[key]} and {ns}.{t} would land on the same target table")
        seen[key] = f"{ns}.{t}"
    return selected


def ensure_meta(con, target_db: str) -> None:
    con.execute(f"CREATE DATABASE IF NOT EXISTS {qi(target_db)}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {qi(target_db)}.{qi(META_SCHEMA)}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {qi(target_db)}.{qi(META_SCHEMA)}.load_log (
            flight_id      VARCHAR,
            run_id         VARCHAR,
            catalog        VARCHAR,
            catalog_db     VARCHAR,
            source_table   VARCHAR,
            target_table   VARCHAR,
            status         VARCHAR,
            rows_in_target BIGINT,
            rows_in_source BIGINT,
            attempts       INTEGER,
            started_at     TIMESTAMPTZ,
            finished_at    TIMESTAMPTZ,
            error          VARCHAR
        )
        """
    )


def copy_table(con, s: Settings, ns: str, table: str) -> tuple[int, int]:
    src = f"{qi(s.catalog_db)}.{qi(ns)}.{qi(table)}"
    dst = f"{qi(s.target_db)}.{qi(ns)}.{qi(table)}"
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {qi(s.target_db)}.{qi(ns)}")
    # One statement: readers keep seeing the previous copy until the new one is complete.
    con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM {src}")
    in_target = con.execute(f"SELECT count(*) FROM {dst}").fetchone()[0]
    in_source = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    return in_target, in_source


def write_log(con, target_db: str, row: list) -> None:
    try:
        con.execute(
            f"INSERT INTO {qi(target_db)}.{qi(META_SCHEMA)}.load_log "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    except Exception as exc:  # never let logging mask the real result
        log(f"  (could not write load_log row: {exc})")


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main() -> None:
    s = resolve_settings()
    flight_id = os.environ.get("MOTHERDUCK_FLIGHT_ID") or "local"
    run_id = os.environ.get("MOTHERDUCK_FLIGHT_RUN_ID") or f"local-{os.getpid()}"
    log(f"Catalog {s.catalog} ({s.preset.label}) -> {s.target_db}; catalog database {s.catalog_db}")
    for note in s.notes:
        log(f"Note: {note}")

    con = connect_motherduck()
    check_secret(con, s)
    attach_catalog(con, s)

    namespaces, tables = discover(con, s.catalog_db)
    selected = select_tables(s, namespaces, tables)
    log(f"Found {len(tables)} table(s) in {len(namespaces)} namespace(s); {len(selected)} selected")
    if not selected:
        raise FlightError(
            "No tables to copy after INCLUDED_/EXCLUDED_ filters. "
            f"Namespaces visible: {namespaces}"
        )

    ensure_meta(con, s.target_db)
    failures: list[str] = []
    for ns, table in selected:
        started = datetime.now(timezone.utc)
        in_target = in_source = error = None
        attempt = 0
        while True:
            attempt += 1
            try:
                in_target, in_source = copy_table(con, s, ns, table)
                status = "succeeded"
                break
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                if attempt >= s.max_retries or is_permanent(exc):
                    status, error = "failed", msg
                    break
                wait = min(60, 2 ** attempt) + random.random()
                log(f"  {ns}.{table}: attempt {attempt}/{s.max_retries} failed ({msg}); "
                    f"retrying in {wait:.0f}s")
                time.sleep(wait)

        secs = (datetime.now(timezone.utc) - started).total_seconds()
        if status == "succeeded":
            drift = ("" if in_source == in_target
                     else f"  WARNING: catalog now has {in_source:,} rows (changed during copy?)")
            log(f"  ok    {ns}.{table}: {in_target:,} rows in {secs:.1f}s (attempts={attempt}){drift}")
        else:
            failures.append(f"{ns}.{table}")
            log(f"  FAIL  {ns}.{table}: {error}")
        write_log(con, s.target_db, [
            flight_id, run_id, s.catalog, s.catalog_db, f"{ns}.{table}", f"{ns}.{table}",
            status, in_target, in_source, attempt, started, datetime.now(timezone.utc), error,
        ])

    con.close()
    log(f"Done: {len(selected) - len(failures)} succeeded, {len(failures)} failed")
    if failures:
        log("Failed tables: " + ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except ValueError as exc:  # config problems: one clear line, no traceback noise
        log(f"FAILED (config): {exc}")
        sys.exit(1)
    except FlightError as exc:
        log(f"FAILED: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(traceback.format_exc(), flush=True)
        log(f"FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
