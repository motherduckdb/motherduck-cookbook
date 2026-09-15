"""Load a pinned Common Crawl web-graph time series into MotherDuck."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
import re
from urllib.parse import urlparse

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Release:
    name: str
    end_date: date


@dataclass(frozen=True)
class Platform:
    domain: str
    reversed_domain: str
    label: str
    kind: str


RELEASES = (
    Release("cc-main-2021-feb-apr-may", date(2021, 5, 31)),
    Release("cc-main-2021-jun-jul-sep", date(2021, 9, 30)),
    Release("cc-main-2021-22-oct-nov-jan", date(2022, 1, 31)),
    Release("cc-main-2022-may-jun-aug", date(2022, 8, 31)),
    Release("cc-main-2022-23-sep-nov-jan", date(2023, 1, 31)),
    Release("cc-main-2023-mar-may-oct", date(2023, 10, 31)),
    Release("cc-main-2023-24-sep-nov-feb", date(2024, 2, 29)),
    Release("cc-main-2024-apr-may-jun", date(2024, 6, 30)),
    Release("cc-main-2024-jul-aug-sep", date(2024, 9, 30)),
    Release("cc-main-2024-oct-nov-dec", date(2024, 12, 31)),
    Release("cc-main-2025-jan-feb-mar", date(2025, 3, 31)),
    Release("cc-main-2025-apr-may-jun", date(2025, 6, 30)),
    Release("cc-main-2025-jul-aug-sep", date(2025, 9, 30)),
    Release("cc-main-2025-oct-nov-dec", date(2025, 12, 31)),
    Release("cc-main-2026-jan-feb-mar", date(2026, 3, 31)),
    Release("cc-main-2026-apr-may-jun", date(2026, 6, 30)),
)


PLATFORMS = (
    Platform("base44.app", "app.base44", "Base44", "platform"),
    Platform("bolt.host", "host.bolt", "Bolt", "platform"),
    Platform("deno.dev", "dev.deno", "Deno Deploy", "platform"),
    Platform("firebaseapp.com", "com.firebaseapp", "Firebase (firebaseapp.com)", "platform"),
    Platform("fly.dev", "dev.fly", "Fly.io", "platform"),
    Platform("framer.app", "app.framer", "Framer", "platform"),
    Platform("framer.website", "website.framer", "Framer (website)", "platform"),
    Platform("github.io", "io.github", "GitHub Pages", "platform"),
    Platform("gitlab.io", "io.gitlab", "GitLab Pages", "platform"),
    Platform("glitch.me", "me.glitch", "Glitch", "platform"),
    Platform("herokuapp.com", "com.herokuapp", "Heroku", "platform"),
    Platform("lovable.app", "app.lovable", "Lovable", "platform"),
    Platform("neocities.org", "org.neocities", "Neocities", "platform"),
    Platform("netlify.app", "app.netlify", "Netlify", "platform"),
    Platform("onrender.com", "com.onrender", "Render", "platform"),
    Platform("pages.dev", "dev.pages", "Cloudflare Pages", "platform"),
    Platform("railway.app", "app.railway", "Railway", "platform"),
    Platform("replit.app", "app.replit", "Replit", "platform"),
    Platform("replit.dev", "dev.replit", "Replit (dev)", "platform"),
    Platform("streamlit.app", "app.streamlit", "Streamlit", "platform"),
    Platform("surge.sh", "sh.surge", "Surge", "platform"),
    Platform("v0.app", "app.v0", "v0", "platform"),
    Platform("v0.dev", "dev.v0", "v0 (dev)", "platform"),
    Platform("vercel.app", "app.vercel", "Vercel", "platform"),
    Platform("web.app", "app.web", "Firebase (web.app)", "platform"),
    Platform("webflow.io", "io.webflow", "Webflow", "platform"),
    Platform("workers.dev", "dev.workers", "Cloudflare Workers", "platform"),
    Platform("blogspot.com", "com.blogspot", "Blogspot", "reference"),
    Platform("medium.com", "com.medium", "Medium", "reference"),
    Platform("myshopify.com", "com.myshopify", "Shopify (myshopify)", "reference"),
    Platform("shopify.com", "com.shopify", "Shopify", "reference"),
    Platform("squarespace.com", "com.squarespace", "Squarespace", "reference"),
    Platform("substack.com", "com.substack", "Substack", "reference"),
    Platform("tumblr.com", "com.tumblr", "Tumblr", "reference"),
    Platform("weebly.com", "com.weebly", "Weebly", "reference"),
    Platform("wixsite.com", "com.wixsite", "Wix", "reference"),
    Platform("wordpress.com", "com.wordpress", "WordPress.com", "reference"),
)


HOST_COLUMNS_SQL = """{
    'harmonicc_pos': 'BIGINT',
    'harmonicc_val': 'DOUBLE',
    'pr_pos': 'BIGINT',
    'pr_val': 'DOUBLE',
    'host_rev': 'VARCHAR'
}"""
DOMAIN_COLUMNS_SQL = """{
    'harmonicc_pos': 'BIGINT',
    'harmonicc_val': 'DOUBLE',
    'pr_pos': 'BIGINT',
    'pr_val': 'DOUBLE',
    'domain_rev': 'VARCHAR',
    'n_hosts': 'BIGINT'
}"""


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def validate_https_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("SOURCE_BASE_URL must be an absolute HTTPS URL without a query or fragment.")
    return normalized


def validate_platform_registry(platforms: tuple[Platform, ...]) -> None:
    domains = set()
    reversed_domains = set()
    for platform in platforms:
        expected_reversed_domain = ".".join(reversed(platform.domain.split(".")))
        if platform.domain != platform.domain.lower() or platform.reversed_domain != expected_reversed_domain:
            raise ValueError(f"Invalid platform domain mapping for {platform.domain!r}.")
        if platform.kind not in {"platform", "reference"} or not platform.label:
            raise ValueError(f"Invalid platform registry row for {platform.domain!r}.")
        if platform.domain in domains or platform.reversed_domain in reversed_domains:
            raise ValueError(f"Duplicate platform registry row for {platform.domain!r}.")
        domains.add(platform.domain)
        reversed_domains.add(platform.reversed_domain)


def platform_registry_hash(platforms: tuple[Platform, ...]) -> str:
    validate_platform_registry(platforms)
    payload = [
        {
            "domain": platform.domain,
            "reversed_domain": platform.reversed_domain,
            "label": platform.label,
            "kind": platform.kind,
        }
        for platform in sorted(platforms, key=lambda platform: platform.domain)
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def parse_release_selection(raw: str, releases: tuple[Release, ...] = RELEASES) -> tuple[Release, ...]:
    if not raw.strip():
        return releases
    names = tuple(name.strip() for name in raw.split(","))
    if any(not name for name in names):
        raise ValueError("RELEASE_NAMES must be a comma-separated list without empty values.")
    if len(set(names)) != len(names):
        raise ValueError("RELEASE_NAMES must not contain duplicate releases.")
    known = {release.name for release in releases}
    unknown = sorted(set(names) - known)
    if unknown:
        raise ValueError(f"RELEASE_NAMES contains releases outside the pinned registry: {', '.join(unknown)}.")
    selected = set(names)
    return tuple(release for release in releases if release.name in selected)


def release_source_url(base_url: str, release: Release, rank_type: str) -> str:
    if rank_type not in {"host", "domain"}:
        raise ValueError(f"Unsupported rank type {rank_type!r}.")
    return f"{base_url}/{release.name}/{rank_type}/{release.name}-{rank_type}-ranks.txt.gz"


def ensure_registry(connection: duckdb.DuckDBPyConnection, platforms_table: str, state_table: str) -> str:
    expected_hash = platform_registry_hash(PLATFORMS)
    state_rows = connection.execute(f"SELECT registry_hash FROM {state_table}").fetchall()
    stored_platforms = tuple(
        Platform(*row)
        for row in connection.execute(
            f"SELECT domain, reversed_domain, label, kind FROM {platforms_table} ORDER BY domain"
        ).fetchall()
    )
    expected_platforms = tuple(sorted(PLATFORMS, key=lambda platform: platform.domain))

    if len(state_rows) > 1:
        raise RuntimeError(f"{state_table} must contain exactly one registry hash.")
    if not state_rows:
        if stored_platforms:
            raise RuntimeError(
                f"{platforms_table} has rows but {state_table} has no registry hash. "
                "Create a new destination or reconcile the existing series before running this Flight."
            )
        connection.execute("BEGIN")
        try:
            connection.executemany(
                f"INSERT INTO {platforms_table} VALUES (?, ?, ?, ?)",
                [(platform.domain, platform.reversed_domain, platform.label, platform.kind) for platform in PLATFORMS],
            )
            connection.execute(f"INSERT INTO {state_table} VALUES (?, current_timestamp)", [expected_hash])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        return expected_hash

    stored_hash = state_rows[0][0]
    if stored_hash != expected_hash:
        raise RuntimeError(
            "The configured platform registry differs from the registry that created this series. "
            "Use a new destination or intentionally migrate the existing series."
        )
    if stored_platforms != expected_platforms:
        raise RuntimeError(
            f"{platforms_table} does not match its stored registry hash. "
            "Restore the table or use a new destination before running this Flight."
        )
    return expected_hash


def process_release(
    connection: duckdb.DuckDBPyConnection,
    release: Release,
    source_base_url: str,
    platforms_table: str,
    hosts_table: str,
    ledger_table: str,
    registry_hash: str,
) -> bool:
    completed = connection.execute(
        f"SELECT 1 FROM {ledger_table} WHERE release = ?", [release.name]
    ).fetchone()
    if completed:
        print(f"skip {release.name}: already completed")
        return False

    host_url = release_source_url(source_base_url, release, "host")
    domain_url = release_source_url(source_base_url, release, "domain")
    connection.execute("BEGIN")
    try:
        connection.execute(
            f"""
            INSERT INTO {hosts_table}
            WITH hosts AS (
                SELECT
                    split_part(host_rev, '.', 1) || '.' || split_part(host_rev, '.', 2) AS domain_rev,
                    host_rev,
                    harmonicc_pos,
                    harmonicc_val,
                    pr_pos,
                    pr_val
                FROM read_csv(?, delim = '\\t', header = true, columns = {HOST_COLUMNS_SQL})
            )
            SELECT ?, ?, hosts.domain_rev, hosts.host_rev, hosts.harmonicc_pos,
                   hosts.harmonicc_val, hosts.pr_pos, hosts.pr_val
            FROM hosts
            JOIN {platforms_table} AS platforms
              ON hosts.domain_rev = platforms.reversed_domain
            """,
            [host_url, release.name, release.end_date],
        )
        discrepancies, domains_checked = connection.execute(
            f"""
            WITH from_hosts AS (
                SELECT domain_rev, count(*) AS n_hosts
                FROM {hosts_table}
                WHERE release = ?
                GROUP BY domain_rev
            ),
            from_domains AS (
                SELECT domains.domain_rev, domains.n_hosts
                FROM read_csv(?, delim = '\\t', header = true, columns = {DOMAIN_COLUMNS_SQL}) AS domains
                JOIN {platforms_table} AS platforms
                  ON domains.domain_rev = platforms.reversed_domain
            )
            SELECT
                count(*) FILTER (
                    WHERE coalesce(from_hosts.n_hosts, 0) <> coalesce(from_domains.n_hosts, 0)
                ) AS discrepancies,
                count(from_domains.domain_rev) AS domains_checked
            FROM from_hosts
            FULL OUTER JOIN from_domains USING (domain_rev)
            """,
            [release.name, domain_url],
        ).fetchone()[0]
        if discrepancies:
            raise RuntimeError(
                f"{release.name} has {discrepancies} host-count discrepancies. Rolling back this release."
            )
        host_rows = connection.execute(
            f"SELECT count(*) FROM {hosts_table} WHERE release = ?", [release.name]
        ).fetchone()[0]
        connection.execute(
            f"INSERT INTO {ledger_table} VALUES (?, ?, ?, current_timestamp, ?, ?)",
            [release.name, release.end_date, registry_hash, host_rows, domains_checked],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    print(f"completed {release.name}: {host_rows} hosts, {domains_checked} platform domains checked")
    return True


def main() -> None:
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "commoncrawl"))
    schema = validate_identifier("DESTINATION_SCHEMA", env("DESTINATION_SCHEMA", "main"))
    platforms_name = validate_identifier("PLATFORMS_TABLE", env("PLATFORMS_TABLE", "platforms"))
    hosts_name = validate_identifier("HOSTS_TABLE", env("HOSTS_TABLE", "platform_hosts"))
    ledger_name = validate_identifier("RUN_LEDGER_TABLE", env("RUN_LEDGER_TABLE", "completed_runs"))
    state_name = validate_identifier("REGISTRY_STATE_TABLE", env("REGISTRY_STATE_TABLE", "registry_state"))
    source_base_url = validate_https_base_url(
        env("SOURCE_BASE_URL", "https://data.commoncrawl.org/projects/hyperlinkgraph")
    )
    selected_releases = parse_release_selection(env("RELEASE_NAMES", ""))

    platforms_table = f"{database}.{schema}.{platforms_name}"
    hosts_table = f"{database}.{schema}.{hosts_name}"
    ledger_table = f"{database}.{schema}.{ledger_name}"
    state_table = f"{database}.{schema}.{state_name}"

    connection = duckdb.connect("md:")
    try:
        connection.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {platforms_table} (
                domain VARCHAR PRIMARY KEY,
                reversed_domain VARCHAR UNIQUE NOT NULL,
                label VARCHAR NOT NULL,
                kind VARCHAR NOT NULL
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {hosts_table} (
                release VARCHAR NOT NULL,
                release_end_date DATE NOT NULL,
                domain_rev VARCHAR NOT NULL,
                host_rev VARCHAR NOT NULL,
                harmonicc_pos BIGINT,
                harmonicc_val DOUBLE,
                pr_pos BIGINT,
                pr_val DOUBLE
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ledger_table} (
                release VARCHAR PRIMARY KEY,
                release_end_date DATE NOT NULL,
                platform_registry_hash VARCHAR NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL,
                host_rows BIGINT NOT NULL,
                domains_checked BIGINT NOT NULL
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {state_table} (
                registry_hash VARCHAR PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        registry_hash = ensure_registry(connection, platforms_table, state_table)
        for release in selected_releases:
            process_release(
                connection,
                release,
                source_base_url,
                platforms_table,
                hosts_table,
                ledger_table,
                registry_hash,
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
