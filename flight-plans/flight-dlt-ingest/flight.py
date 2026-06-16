import os
import re
from collections.abc import Iterator

import dlt
import duckdb
import httpx


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WRITE_DISPOSITIONS = {"append", "merge", "replace"}


def repo_rows(repos: list[str]) -> Iterator[dict]:
    # Demo source: public GitHub repository metadata, no credentials needed.
    # Replace this generator with your own dlt source (an API, a database, a
    # filesystem, or a dlt verified source) to ingest real data. Yield plain
    # dicts and dlt infers the schema and evolves it as fields change.
    for repo in repos:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}",
            timeout=30,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        payload = response.json()
        yield {
            "repo": repo,
            "stars": payload.get("stargazers_count"),
            "forks": payload.get("forks_count"),
            "open_issues": payload.get("open_issues_count"),
            "default_branch": payload.get("default_branch"),
            "pushed_at": payload.get("pushed_at"),
        }


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. Defaults load public GitHub
    # repo stats into flights_demo so a fresh deploy produces a successful run.
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "flights_demo"))
    dataset_name = env("DATASET_NAME", "flights_demo_dlt")
    table_name = env("TABLE_NAME", "github_repo_stats")
    pipeline_name = env("PIPELINE_NAME", "flights_dlt_ingest")
    primary_key = env("PRIMARY_KEY", "repo")
    write_disposition = env("WRITE_DISPOSITION", "merge")
    if write_disposition not in WRITE_DISPOSITIONS:
        raise ValueError(
            f"WRITE_DISPOSITION must be one of {sorted(WRITE_DISPOSITIONS)}, got {write_disposition!r}"
        )
    ledger_table = validate_identifier("RUN_LEDGER_TABLE", env("RUN_LEDGER_TABLE", "dlt_ingest_runs"))
    repos = [
        repo.strip()
        for repo in env("GITHUB_REPOS", "duckdb/duckdb,motherduckdb/motherduck-docs,dlt-hub/dlt").split(",")
        if repo.strip()
    ]

    # dlt writes working files under HOME; a Flight has a writable /tmp.
    os.environ.setdefault("HOME", "/tmp")
    # Point the dlt MotherDuck destination at our database. The injected
    # MOTHERDUCK_TOKEN supplies the credential, so no token appears here.
    os.environ["DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE"] = database

    # Create the destination database so dlt has a catalog to build the dataset in;
    # dlt creates the dataset (schema) and tables, but not the database itself.
    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")

    pipeline = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination="motherduck",
        dataset_name=dataset_name,
    )
    load_info = pipeline.run(
        repo_rows(repos),
        table_name=table_name,
        write_disposition=write_disposition,
        primary_key=primary_key,
        # Prefer Parquet loader files over row-wise insert_values so larger
        # sources stay on a bulk-loading path. Keep this unless you have measured
        # a reason to change it.
        loader_file_format="parquet",
    )

    # Record the dlt load package summary so each run leaves an audit trail. The
    # ledger lives in the database's main schema, separate from the dlt dataset.
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.main")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.main.{ledger_table} (
            run_at TIMESTAMPTZ,
            pipeline_name VARCHAR,
            destination_dataset VARCHAR,
            destination_table VARCHAR,
            load_summary VARCHAR
        )
        """
    )
    con.execute(
        f"INSERT INTO {database}.main.{ledger_table} VALUES (current_timestamp, ?, ?, ?, ?)",
        [pipeline_name, dataset_name, table_name, str(load_info)],
    )
    con.close()
    print(load_info)


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def validate_identifier(name: str, value: str) -> str:
    # The database and ledger table names flow into CREATE/INSERT statements that
    # cannot be parameterized, so reject anything that is not a plain SQL
    # identifier before any SQL runs.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


if __name__ == "__main__":
    main()
