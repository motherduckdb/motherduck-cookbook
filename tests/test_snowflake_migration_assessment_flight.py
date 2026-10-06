"""Offline tests for flight-plans/flight-snowflake-migration-assessment/flight.py.

Covers the parts the Flight adds around md-assess: the guard that keeps
REPLACE from dropping a database that is not an assessment, and the
credential resolution from a TYPE flights secret. The md: connection is
replaced with a local DuckDB session that attaches throwaway databases.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path

import duckdb
import pytest


ROOT = Path(__file__).resolve().parents[1]
FLIGHT = ROOT / "flight-plans" / "flight-snowflake-migration-assessment" / "flight.py"


@pytest.fixture()
def flight():
    return runpy.run_path(str(FLIGHT), run_name="snowflake_migration_assessment_test")


@pytest.fixture()
def fake_motherduck(monkeypatch, tmp_path):
    real_connect = duckdb.connect

    def connect(path=":memory:", *args, **kwargs):
        if path != "md:":
            return real_connect(path, *args, **kwargs)
        con = real_connect(":memory:")
        con.execute(f"ATTACH '{tmp_path / 'prod_db.duckdb'}' AS prod_db")
        con.execute("CREATE TABLE IF NOT EXISTS prod_db.main.orders AS SELECT 1 AS id")
        con.execute(f"ATTACH '{tmp_path / 'old_assessment.duckdb'}' AS old_assessment")
        con.execute("CREATE SCHEMA IF NOT EXISTS old_assessment.meta")
        con.execute("CREATE TABLE IF NOT EXISTS old_assessment.meta.collections AS SELECT 1 AS x")
        return con

    monkeypatch.setattr(duckdb, "connect", connect)


@pytest.fixture()
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.upper().startswith("SNOWFLAKE"):
            monkeypatch.delenv(key)
    return monkeypatch


def test_guard_allows_a_new_database(flight, fake_motherduck):
    flight["guard_existing_database"]("brand_new", True)


def test_guard_replaces_an_earlier_assessment(flight, fake_motherduck, capsys):
    flight["guard_existing_database"]("old_assessment", True)
    assert "replacing earlier assessment" in capsys.readouterr().out


def test_guard_respects_replace_false(flight, fake_motherduck):
    with pytest.raises(RuntimeError, match="REPLACE=false"):
        flight["guard_existing_database"]("old_assessment", False)


def test_guard_refuses_to_replace_a_non_assessment(flight, fake_motherduck):
    with pytest.raises(RuntimeError, match="not an earlier assessment"):
        flight["guard_existing_database"]("prod_db", True)


def test_namespaced_secret_wins_and_key_is_written_0600(flight, clean_env):
    clean_env.setenv("SNOWFLAKE_ACCOUNT", "myorg-myaccount")
    clean_env.setenv("SNOWFLAKE_WAREHOUSE", "XSMALL_WH")
    clean_env.setenv("SNOWFLAKE_USER", "raw_alias_user")
    clean_env.setenv("snowflake_creds_SNOWFLAKE_USER", "namespaced_user")
    clean_env.setenv(
        "snowflake_creds_SNOWFLAKE_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----",
    )

    flight["resolve_snowflake_credentials"]("snowflake_creds")

    assert os.environ["SNOWFLAKE_USER"] == "namespaced_user"
    assert "SNOWFLAKE_PRIVATE_KEY" not in os.environ
    key_path = Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"])
    try:
        assert key_path.stat().st_mode & 0o777 == 0o600
        assert key_path.read_text().splitlines() == [
            "-----BEGIN PRIVATE KEY-----",
            "ABC",
            "-----END PRIVATE KEY-----",
        ]
    finally:
        key_path.unlink()


def test_missing_credential_is_an_error(flight, clean_env):
    clean_env.setenv("SNOWFLAKE_ACCOUNT", "myorg-myaccount")
    clean_env.setenv("snowflake_creds_SNOWFLAKE_USER", "namespaced_user")
    with pytest.raises(ValueError, match="no Snowflake credential"):
        flight["resolve_snowflake_credentials"]("snowflake_creds")


def test_external_browser_sso_is_rejected(flight, clean_env):
    clean_env.setenv("SNOWFLAKE_ACCOUNT", "myorg-myaccount")
    clean_env.setenv("SNOWFLAKE_USER", "user")
    clean_env.setenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")
    with pytest.raises(ValueError, match="externalbrowser"):
        flight["resolve_snowflake_credentials"]("snowflake_creds")
