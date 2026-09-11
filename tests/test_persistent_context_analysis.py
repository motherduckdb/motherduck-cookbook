from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLIGHT_PATH = ROOT / "flight-plans" / "flight-persistent-context-analysis" / "flight.py"


@pytest.fixture(scope="module")
def flight():
    spec = importlib.util.spec_from_file_location(
        "persistent_context_flight", FLIGHT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo_fixture_path(flight, tmp_path_factory):
    output = tmp_path_factory.mktemp("persistent-context-fixture") / "fixture.parquet"
    flight.build_demo_fixture(output)
    return output


def test_parse_config_uses_public_fixture_and_private_guides(flight):
    config = flight.parse_config({})
    assert config.demo_data_url == (
        "https://us.data.motherduck.com/persistent-context-analysis/"
        "v1/duck_shop_sales.parquet"
    )
    assert config.guide_access == "user"
    assert config.demo_revision == 0


def test_parse_config_rejects_unknown_guide_access(flight):
    with pytest.raises(ValueError, match="GUIDE_ACCESS"):
        flight.parse_config({"GUIDE_ACCESS": "public"})


def test_parse_config_rejects_unsafe_state_identifier(flight):
    with pytest.raises(ValueError, match="STATE_SCHEMA"):
        flight.parse_config({"STATE_SCHEMA": "main; DROP DATABASE prod"})


def test_demo_fixture_is_deterministic_and_has_two_london_stores(flight, tmp_path):
    first = tmp_path / "first.parquet"
    second = tmp_path / "second.parquet"
    first_meta = flight.build_demo_fixture(first)
    second_meta = flight.build_demo_fixture(second)

    assert first_meta == second_meta
    assert first_meta.row_count > 1_000
    assert (first_meta.max_date - first_meta.min_date).days == 119

    con = flight.duckdb.connect()
    stores = con.execute(
        """
        SELECT store_id, store_type, market, count(DISTINCT order_id)
        FROM read_parquet(?)
        GROUP BY ALL
        ORDER BY store_id
        """,
        [str(first)],
    ).fetchall()
    assert [row[:3] for row in stores] == [
        ("duck_shop_london", "physical", "London"),
        ("duck_shop_online", "ecommerce", "London"),
    ]
    assert all(row[3] > 0 for row in stores)


def test_demo_fixture_excludes_pii_columns(flight, tmp_path):
    output = tmp_path / "fixture.parquet"
    flight.build_demo_fixture(output)
    con = flight.duckdb.connect()
    columns = {
        row[0].lower()
        for row in con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(output)]
        ).fetchall()
    }
    banned = {"email", "address", "phone", "ip_address", "token", "shopify", "sku"}
    assert not any(term in column for term in banned for column in columns)


def test_load_demo_sales_adds_late_revision_to_both_completed_periods(
    flight, demo_fixture_path
):
    con = flight.duckdb.connect()
    baseline = flight.load_demo_sales(con, str(demo_fixture_path), demo_revision=0)
    revised = flight.load_demo_sales(con, str(demo_fixture_path), demo_revision=1)

    late_rows, late_orders, late_min_date, late_max_date = con.execute(
        """
        SELECT count(*), count(DISTINCT order_id),
               min(order_at)::DATE, max(order_at)::DATE
        FROM demo_sales
        WHERE order_id LIKE 'late-%'
        """
    ).fetchone()
    assert revised.row_count == baseline.row_count + 1
    assert revised.content_hash != baseline.content_hash
    assert (late_rows, late_orders) == (1, 1)
    assert late_min_date == late_max_date == flight.date(2026, 8, 31)


def test_load_demo_sales_hash_is_independent_of_source_row_order(
    flight, demo_fixture_path, tmp_path
):
    ascending = tmp_path / "ascending.parquet"
    descending = tmp_path / "descending.parquet"
    con = flight.duckdb.connect()
    con.execute(
        "CREATE TABLE hash_fixture AS SELECT * FROM read_parquet(?) LIMIT 0",
        [str(demo_fixture_path)],
    )
    con.execute(
        """
        INSERT INTO hash_fixture VALUES
            ('tied-order', TIMESTAMP '2026-08-31 12:00:00', 'tied-customer',
             'duck_shop_online', 'Duck Shop Online', 'ecommerce', 'London',
             'organic', 'paid', false, 'duck-shirt', 'black', 'apparel', 1,
             29.00, 0.00, 29.00, 29.00, NULL, 0.00),
            ('tied-order', TIMESTAMP '2026-08-31 12:00:00', 'tied-customer',
             'duck_shop_online', 'Duck Shop Online', 'ecommerce', 'London',
             'organic', 'paid', false, 'duck-shirt', 'yellow', 'apparel', 2,
             29.00, 0.00, 58.00, 58.00, NULL, 0.00)
        """
    )
    con.execute(
        "COPY (SELECT * FROM hash_fixture ORDER BY variant_id) TO ? (FORMAT PARQUET)",
        [str(ascending)],
    )
    con.execute(
        "COPY (SELECT * FROM hash_fixture ORDER BY variant_id DESC) TO ? "
        "(FORMAT PARQUET)",
        [str(descending)],
    )

    ascending_meta = flight.load_demo_sales(con, str(ascending), demo_revision=0)
    descending_meta = flight.load_demo_sales(con, str(descending), demo_revision=0)

    assert ascending_meta == descending_meta


def test_load_demo_sales_rejects_unknown_revision(flight, demo_fixture_path):
    con = flight.duckdb.connect()
    with pytest.raises(ValueError, match="DEMO_REVISION must be 0 or 1"):
        flight.load_demo_sales(con, str(demo_fixture_path), demo_revision=2)


def test_load_demo_sales_rejects_unexpected_schema(flight, demo_fixture_path, tmp_path):
    malformed = tmp_path / "malformed.parquet"
    con = flight.duckdb.connect()
    con.execute(
        """
        CREATE TABLE malformed_fixture AS
        SELECT * EXCLUDE (customer_id)
        FROM read_parquet(?)
        """,
        [str(demo_fixture_path)],
    )
    con.execute("COPY malformed_fixture TO ? (FORMAT PARQUET)", [str(malformed)])

    with pytest.raises(ValueError, match="Unexpected demo fixture schema"):
        flight.load_demo_sales(con, str(malformed), demo_revision=0)


def test_periods_include_completed_day_week_and_month(flight):
    periods = flight.periods_to_process(flight.date(2026, 9, 1), 7, True)
    assert (
        flight.PeriodKey("day", flight.date(2026, 8, 31), flight.date(2026, 9, 1))
        in periods
    )
    assert (
        flight.PeriodKey("week", flight.date(2026, 8, 24), flight.date(2026, 8, 31))
        in periods
    )
    assert (
        flight.PeriodKey("month", flight.date(2026, 8, 1), flight.date(2026, 9, 1))
        in periods
    )


def test_comparison_periods_use_fixed_half_open_ranges(flight):
    assert flight.comparison_periods(
        flight.PeriodKey("day", flight.date(2026, 9, 10), flight.date(2026, 9, 11))
    ) == [
        flight.PeriodKey("day", flight.date(2026, 9, 9), flight.date(2026, 9, 10)),
        flight.PeriodKey("day", flight.date(2026, 9, 3), flight.date(2026, 9, 4)),
        flight.PeriodKey("day", flight.date(2026, 8, 13), flight.date(2026, 9, 10)),
    ]
    assert flight.comparison_periods(
        flight.PeriodKey("week", flight.date(2026, 8, 24), flight.date(2026, 8, 31))
    ) == [
        flight.PeriodKey("week", flight.date(2026, 8, 17), flight.date(2026, 8, 24)),
        flight.PeriodKey("week", flight.date(2026, 7, 27), flight.date(2026, 8, 24)),
    ]
    assert flight.comparison_periods(
        flight.PeriodKey("month", flight.date(2026, 8, 1), flight.date(2026, 9, 1))
    ) == [
        flight.PeriodKey("month", flight.date(2026, 7, 1), flight.date(2026, 8, 1)),
        flight.PeriodKey("month", flight.date(2026, 5, 3), flight.date(2026, 8, 1)),
    ]


def test_order_count_deduplicates_repeated_order_fields(flight, tmp_path):
    fixture = tmp_path / "fixture.parquet"
    flight.build_demo_fixture(fixture)
    con = flight.duckdb.connect()
    flight.load_demo_sales(con, str(fixture), 0)
    period = flight.PeriodKey("day", flight.date(2026, 9, 10), flight.date(2026, 9, 11))
    evidence = flight.compute_metric_evidence(con, period)
    total_orders = next(
        item for item in evidence if item.metric == "orders" and item.dimensions == ()
    )
    line_count, expected = con.execute(
        "SELECT count(*), count(DISTINCT order_id) FROM demo_sales "
        "WHERE order_at::DATE = DATE '2026-09-10'"
    ).fetchone()
    assert line_count > expected
    assert int(total_orders.current_value) == expected


def test_order_count_keeps_store_type_evidence_separate(flight, demo_fixture_path):
    con = flight.duckdb.connect()
    flight.load_demo_sales(con, str(demo_fixture_path), 0)
    period = flight.PeriodKey("day", flight.date(2026, 9, 10), flight.date(2026, 9, 11))
    evidence = flight.compute_metric_evidence(con, period)
    store_type_orders = {
        item.dimensions[0][1]: int(item.current_value)
        for item in evidence
        if item.metric == "orders"
        and item.dimensions
        and item.dimensions[0][0] == "store_type"
    }
    assert store_type_orders == {"ecommerce": 28, "physical": 18}
    totals = {item.metric for item in evidence if item.dimensions == ()}
    assert totals == {
        "net_revenue",
        "gross_revenue",
        "orders",
        "average_order_value",
        "refund_rate",
        "cancellation_rate",
        "units_per_order",
        "returning_customer_share",
    }
    dimension_names = {item.dimensions[0][0] for item in evidence if item.dimensions}
    assert dimension_names == {
        "store_id",
        "store_type",
        "acquisition_channel",
        "product_category",
    }
    evidence_ids = [item.evidence_id for item in evidence]
    assert len(evidence_ids) == len(set(evidence_ids))
    assert all(
        flight.re.fullmatch(r"metric-[0-9a-f]{16}", evidence_id)
        for evidence_id in evidence_ids
    )
