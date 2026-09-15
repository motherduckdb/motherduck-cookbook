"""Regression tests for both single-file Iceberg transformation templates."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import duckdb
import pytest


@pytest.fixture(params=['direct', 'stage'])
def flight(request):
    path = Path(__file__).resolve().parents[1] / f'flight-plans/flight-iceberg-databricks-{request.param}/flight.py'
    spec = importlib.util.spec_from_file_location(f'iceberg_{request.param}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rollup_handles_big_ids_nulls_duplicates_and_utc(flight):
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        con.execute('CREATE TABLE events (customer_id BIGINT, event_ts TIMESTAMPTZ, event_type VARCHAR)')
        con.execute("""INSERT INTO events VALUES
            (4294967296, '2026-09-15 00:30:00+02', 'query'),
            (4294967296, '2026-09-14 22:30:00+00', 'query'),
            (4294967296, '2026-09-14 22:30:00+00', NULL),
            (NULL, NULL, 'login')""")
        rows = con.execute(flight.rollup_sql('events') + ' ORDER BY customer_id NULLS LAST').fetchall()
        assert [(r[0], str(r[1]), r[2], r[3]) for r in rows] == [
            (4294967296, '2026-09-14', 3, 2), (None, 'None', 1, 0)]


def test_publication_repeats_and_rolls_back_bad_transform(flight):
    with duckdb.connect() as con:
        sql = "SELECT 4294967296, DATE '2026-09-15', 2, 1"
        for _ in range(2):
            flight.publish_rollup(con, 'rollup', sql)
        before = con.execute('FROM rollup').fetchall()
        assert len(before) == 1
        with pytest.raises(duckdb.ConversionException):
            flight.publish_rollup(con, 'rollup', "SELECT 'invalid-id', current_date, 1, 1")
        assert con.execute('FROM rollup').fetchall() == before
        flight.publish_rollup(con, 'rollup', sql + ' WHERE false')
        assert con.execute('SELECT count(*) FROM rollup').fetchone() == (0,)


def test_first_failed_publication_leaves_no_table(flight):
    with duckdb.connect() as con:
        with pytest.raises(duckdb.ConversionException):
            flight.publish_rollup(con, 'rollup', "SELECT 'bad', current_date, 1, 1")
        assert con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='rollup'").fetchone() == (0,)


def test_source_alias_is_rejected_before_connect(flight, monkeypatch):
    monkeypatch.setenv('ICEBERG_ENDPOINT', 'https://example.com')
    monkeypatch.setenv('SOURCE_TABLE', 'same_table')
    monkeypatch.setenv('TARGET_TABLE', 'SAME_TABLE')
    connect = MagicMock()
    monkeypatch.setattr(flight.duckdb, 'connect', connect)
    with pytest.raises(ValueError, match='must be different'):
        flight.main()
    connect.assert_not_called()


def test_native_catalog_is_rejected(flight):
    con = MagicMock()
    con.execute.return_value.fetchone.return_value = ('NATIVE',)
    with pytest.raises(ValueError, match='Iceberg database'):
        flight.attach_iceberg(con, 'lake', 'token', 'https://example.com', 'warehouse', 'default')


def test_quoted_identifiers_and_escaped_literals(flight):
    con = MagicMock()
    con.execute.return_value.fetchone.return_value = ('ICEBERG',)
    flight.attach_iceberg(con, 'select', 'from', "https://example.com/'", "ware'house", "schema'")
    sql = con.execute.call_args_list[0].args[0]
    assert 'DATABASE IF NOT EXISTS "select"' in sql
    assert '\"secret\" "from"' in sql
    assert "warehouse 'ware''house'" in sql
    with pytest.raises(ValueError):
        flight.quote_identifier('lake;DROP TABLE source')


def test_connection_closed_on_failure(flight, monkeypatch):
    monkeypatch.setenv('ICEBERG_ENDPOINT', 'https://example.com')
    con = MagicMock()
    con.execute.side_effect = RuntimeError('failed to attach')
    monkeypatch.setattr(flight.duckdb, 'connect', lambda *args: con)
    with pytest.raises(RuntimeError, match='failed to attach'):
        flight.main()
    con.close.assert_called_once()


def test_stage_config_is_strict(flight, monkeypatch):
    if not hasattr(flight, 'env_bool'):
        return
    monkeypatch.setenv('PUBLISH_TO_ICEBERG', 'flase')
    with pytest.raises(ValueError, match='true or false'):
        flight.env_bool('PUBLISH_TO_ICEBERG', True)
    monkeypatch.setenv('PUBLISH_TO_ICEBERG', 'false')
    assert flight.env_bool('PUBLISH_TO_ICEBERG', True) is False
    monkeypatch.setenv('ICEBERG_ENDPOINT', 'https://example.com')
    monkeypatch.setenv('ICEBERG_CATALOG', 'lake')
    monkeypatch.setenv('MD_DATABASE', 'LAKE')
    with pytest.raises(ValueError, match='MD_DATABASE must differ'):
        flight.main()


@pytest.fixture
def local_iceberg():
    """Opt-in real REST catalog + object-storage integration, no MotherDuck shim."""
    import os
    from uuid import uuid4
    endpoint = os.environ.get('ICEBERG_TEST_ENDPOINT')
    if not endpoint:
        pytest.skip('set ICEBERG_TEST_ENDPOINT for the local Iceberg integration')
    con = duckdb.connect()
    schema = 'pr157_' + uuid4().hex
    created = False
    try:
        con.execute('INSTALL httpfs; LOAD httpfs; INSTALL iceberg; LOAD iceberg;')
        def literal(value):
            return "'" + value.replace("'", "''") + "'"
        try:
            con.execute(
                'CREATE SECRET (TYPE S3, '
                f"KEY_ID {literal(os.environ['AWS_ACCESS_KEY_ID'])}, "
                f"SECRET {literal(os.environ['AWS_SECRET_ACCESS_KEY'])}, "
                f"ENDPOINT {literal(os.environ['ICEBERG_TEST_S3_ENDPOINT'])}, "
                "REGION 'us-east-1', URL_STYLE 'path', USE_SSL false)"
            )
        except duckdb.Error:
            raise RuntimeError('could not configure local test storage credentials') from None
        con.execute(f"ATTACH 'warehouse' AS ice (TYPE ICEBERG, ENDPOINT {literal(endpoint)}, AUTHORIZATION_TYPE 'none')")
        con.execute(f'CREATE SCHEMA ice.{schema}')
        created = True
        con.execute("SET TimeZone='UTC'")
        yield con, f'ice.{schema}'
    finally:
        # Only delete this fixture's unique namespace and tables.
        try:
            if created:
                for table in ['rollup', 'events']:
                    con.execute(f'DROP TABLE IF EXISTS ice.{schema}.{table}')
                con.execute(f'DROP SCHEMA IF EXISTS ice.{schema}')
        finally:
            con.close()


def test_real_iceberg_refresh_rollback_and_empty(flight, local_iceberg):
    con, namespace = local_iceberg
    source, target = f'{namespace}.events', f'{namespace}.rollup'
    con.execute(f'CREATE TABLE {source} (customer_id BIGINT, event_ts TIMESTAMPTZ, event_type VARCHAR)')
    con.execute(f"""INSERT INTO {source} VALUES
        (4294967296, '2026-09-15 00:30:00+02', 'query'),
        (4294967296, '2026-09-14 22:30:00+00', 'query'),
        (NULL, NULL, 'login')""")
    sql = flight.rollup_sql(source)
    for _ in range(2):
        flight.publish_rollup(con, target, sql)
    before = con.execute(f'SELECT * FROM {target} ORDER BY customer_id NULLS LAST').fetchall()
    assert [(r[0], str(r[1]), r[2], r[3]) for r in before] == [
        (4294967296, '2026-09-14', 2, 2), (None, 'None', 1, 0)]
    with pytest.raises(duckdb.Error):
        flight.publish_rollup(con, target, "SELECT 'bad-id', current_date, 1, 1")
    assert con.execute(f'SELECT * FROM {target} ORDER BY customer_id NULLS LAST').fetchall() == before
    con.execute(f'DELETE FROM {source}')
    flight.publish_rollup(con, target, sql)
    assert con.execute(f'SELECT count(*) FROM {target}').fetchone() == (0,)
