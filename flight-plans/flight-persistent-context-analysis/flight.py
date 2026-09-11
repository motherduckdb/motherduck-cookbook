from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

import duckdb
import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

DEMO_DATA_URL = (
    "https://us.data.motherduck.com/persistent-context-analysis/"
    "v1/duck_shop_sales.parquet"
)
STATE_DATABASE = "persistent_analysis"
STATE_SCHEMA = "main"
GUIDE_ROOT = "persistent-analysis/ecommerce"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
LONDON_LATITUDE = 51.5072
LONDON_LONGITUDE = -0.1276
PROMPT_VERSION = "persistent-context-v1"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FIXTURE_COLUMNS = (
    "order_id",
    "order_at",
    "customer_id",
    "store_id",
    "store_name",
    "store_type",
    "market",
    "acquisition_channel",
    "financial_status",
    "cancelled",
    "product_id",
    "variant_id",
    "product_category",
    "quantity",
    "unit_price_gbp",
    "discount_gbp",
    "gross_revenue_gbp",
    "net_revenue_gbp",
    "refund_at",
    "refunded_amount_gbp",
)
STORES = (
    ("duck_shop_online", "Duck Shop Online", "ecommerce"),
    ("duck_shop_london", "Duck Shop London", "physical"),
)
PRODUCTS = (
    ("duck-shirt", "yellow", "apparel", 2_900),
    ("duck-shirt", "black", "apparel", 2_900),
    ("duck-mug", "standard", "homeware", 1_600),
    ("duck-cap", "yellow", "apparel", 2_400),
    ("duck-sticker-pack", "standard", "accessories", 600),
)


@dataclass(frozen=True)
class Config:
    demo_data_url: str
    state_database: str
    state_schema: str
    guide_root: str
    guide_access: Literal["user", "organization"]
    analysis_as_of: date | None
    reconciliation_days: int
    demo_revision: int
    retention_mode: Literal["keep", "archive"]
    daily_guide_keep_days: int
    weekly_guide_keep_weeks: int
    model: str


@dataclass(frozen=True, order=True)
class PeriodKey:
    grain: Literal["day", "week", "month"]
    period_start: date
    period_end: date
    scope: str = "all"


@dataclass(frozen=True)
class FixtureMetadata:
    content_hash: str
    row_count: int
    min_date: date
    max_date: date


@dataclass(frozen=True)
class MetricEvidence:
    evidence_id: str
    period: PeriodKey
    metric: str
    dimensions: tuple[tuple[str, str], ...]
    current_value: Decimal
    comparison_value: Decimal | None
    absolute_change: Decimal | None
    percentage_change: Decimal | None
    sample_size: int


@dataclass(frozen=True)
class ExternalSignal:
    signal_id: str
    provider: str
    provider_id: str
    starts_at: datetime
    ends_at: datetime
    location: str
    title: str
    source_url: str
    attributes: tuple[tuple[str, str], ...]
    payload_hash: str
    retrieved_at: datetime


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    guide_id: UUID
    guide_version: int
    event_id: str
    starts_at: datetime
    ends_at: datetime
    scope: str
    category: str
    source: str
    body: str


@dataclass(frozen=True)
class GuideRecord:
    id: UUID
    topic: str
    title: str
    description: str
    access: str
    current_version: int
    content: str | None = None
    external_id: str | None = None


class Finding(BaseModel):
    title: str
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    signal_ids: list[str] = Field(default_factory=list)
    annotation_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


class AnalysisDraft(BaseModel):
    summary: str
    findings: list[Finding]
    caveats: list[str] = Field(default_factory=list)


def _amount_from_pence(pence: int) -> Decimal:
    return Decimal(pence) / Decimal(100)


def build_demo_fixture(path: Path, seed: int = 20260911) -> FixtureMetadata:
    rng = random.Random(seed)
    rows: list[tuple[object, ...]] = []
    first_date = date(2026, 5, 14)
    order_number = 1

    for day_number in range(120):
        order_date = first_date + timedelta(days=day_number)
        online_orders = 28
        physical_orders = 18
        if order_date.weekday() == 5:
            online_orders += 5
            physical_orders += 8
        elif order_date.weekday() == 6:
            online_orders += 5
            physical_orders -= 6
        if day_number == 42:
            physical_orders = int(physical_orders * 0.4)
            online_orders = int(online_orders * 1.25)
        elif day_number == 87:
            online_orders += 20
            physical_orders += 20

        for store_id, store_name, store_type in STORES:
            order_count = (
                online_orders if store_type == "ecommerce" else physical_orders
            )
            for _ in range(order_count):
                order_id = f"order-{order_number:06d}"
                order_number += 1
                order_at = datetime.combine(
                    order_date,
                    time(
                        hour=rng.randrange(24),
                        minute=rng.randrange(60),
                        second=rng.randrange(60),
                    ),
                )
                customer_id = f"customer-{rng.randrange(1, 1801):04d}"
                acquisition_channel = (
                    rng.choice(("organic", "direct", "social", "email"))
                    if store_type == "ecommerce"
                    else "point_of_sale"
                )
                cancelled = rng.random() < (0.06 if store_type == "ecommerce" else 0.02)
                line_count = rng.randint(1, 3)
                product_weights = (
                    (28, 22, 18, 17, 15)
                    if store_type == "ecommerce"
                    else (22, 18, 26, 21, 13)
                )

                for _ in range(line_count):
                    product_id, variant_id, category, unit_price_pence = rng.choices(
                        PRODUCTS,
                        weights=product_weights,
                        k=1,
                    )[0]
                    quantity = 2 if rng.random() < 0.15 else 1
                    gross_pence = unit_price_pence * quantity
                    discount_probability = 0.18 if store_type == "ecommerce" else 0.06
                    discount_pence = (
                        gross_pence // 10 if rng.random() < discount_probability else 0
                    )
                    refundable_pence = gross_pence - discount_pence
                    refunded = not cancelled and rng.random() < (
                        0.09 if store_type == "ecommerce" else 0.04
                    )
                    refunded_pence = refundable_pence if refunded else 0
                    net_pence = 0 if cancelled else refundable_pence - refunded_pence
                    refund_at = (
                        order_at + timedelta(days=rng.randint(1, 14))
                        if refunded
                        else None
                    )
                    financial_status = (
                        "cancelled" if cancelled else "refunded" if refunded else "paid"
                    )
                    rows.append(
                        (
                            order_id,
                            order_at,
                            customer_id,
                            store_id,
                            store_name,
                            store_type,
                            "London",
                            acquisition_channel,
                            financial_status,
                            cancelled,
                            product_id,
                            variant_id,
                            category,
                            quantity,
                            _amount_from_pence(unit_price_pence),
                            _amount_from_pence(discount_pence),
                            _amount_from_pence(gross_pence),
                            _amount_from_pence(net_pence),
                            refund_at,
                            _amount_from_pence(refunded_pence),
                        )
                    )

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(
            """
            CREATE TABLE demo_fixture (
                order_id VARCHAR,
                order_at TIMESTAMP,
                customer_id VARCHAR,
                store_id VARCHAR,
                store_name VARCHAR,
                store_type VARCHAR,
                market VARCHAR,
                acquisition_channel VARCHAR,
                financial_status VARCHAR,
                cancelled BOOLEAN,
                product_id VARCHAR,
                variant_id VARCHAR,
                product_category VARCHAR,
                quantity INTEGER,
                unit_price_gbp DECIMAL(12, 2),
                discount_gbp DECIMAL(12, 2),
                gross_revenue_gbp DECIMAL(12, 2),
                net_revenue_gbp DECIMAL(12, 2),
                refund_at TIMESTAMP,
                refunded_amount_gbp DECIMAL(12, 2)
            )
            """
        )
        fixture_rows = [dict(zip(FIXTURE_COLUMNS, row)) for row in rows]
        con.execute(
            "INSERT INTO demo_fixture SELECT fixture.* FROM unnest(?) AS rows(fixture)",
            [fixture_rows],
        )
        con.execute(
            "COPY demo_fixture TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(path)],
        )
        row_count, min_date, max_date = con.execute(
            """
            SELECT count(*), min(order_at)::DATE, max(order_at)::DATE
            FROM demo_fixture
            """
        ).fetchone()
    finally:
        con.close()

    return FixtureMetadata(
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        row_count=row_count,
        min_date=min_date,
        max_date=max_date,
    )


def insert_late_demo_order(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        INSERT INTO demo_sales VALUES (
            'late-order-20260831',
            TIMESTAMP '2026-08-31 12:00:00',
            'late-customer-20260831',
            'duck_shop_online',
            'Duck Shop Online',
            'ecommerce',
            'London',
            'organic',
            'paid',
            false,
            'duck-shirt',
            'yellow',
            'apparel',
            1,
            29.00,
            0.00,
            29.00,
            29.00,
            NULL,
            0.00
        )
        """
    )


def load_demo_sales(
    con: duckdb.DuckDBPyConnection,
    source: str,
    demo_revision: int,
) -> FixtureMetadata:
    con.execute(
        "CREATE OR REPLACE TEMP TABLE demo_sales AS FROM read_parquet(?)",
        [source],
    )
    columns = tuple(
        row[1] for row in con.execute("PRAGMA table_info('demo_sales')").fetchall()
    )
    if columns != FIXTURE_COLUMNS:
        raise ValueError(f"Unexpected demo fixture schema: {columns}")
    if demo_revision == 1:
        insert_late_demo_order(con)
    elif demo_revision != 0:
        raise ValueError("DEMO_REVISION must be 0 or 1")
    row_count, min_date, max_date = con.execute(
        "SELECT count(*), min(order_at)::DATE, max(order_at)::DATE FROM demo_sales"
    ).fetchone()
    source_hash = con.execute(
        "SELECT sha256(string_agg(row_to_json(d)::VARCHAR, '' ORDER BY order_id, product_id)) "
        "FROM demo_sales d"
    ).fetchone()[0]
    return FixtureMetadata(source_hash, row_count, min_date, max_date)


def parse_config(env: Mapping[str, str]) -> Config:
    access = env.get("GUIDE_ACCESS", "user").strip()
    if access not in {"user", "organization"}:
        raise ValueError("GUIDE_ACCESS must be 'user' or 'organization'")
    retention_mode = env.get("RETENTION_MODE", "keep").strip()
    if retention_mode not in {"keep", "archive"}:
        raise ValueError("RETENTION_MODE must be 'keep' or 'archive'")
    as_of_raw = env.get("ANALYSIS_AS_OF", "").strip()
    state_database = env.get("STATE_DATABASE", STATE_DATABASE).strip()
    state_schema = env.get("STATE_SCHEMA", STATE_SCHEMA).strip()
    for variable, value in {
        "STATE_DATABASE": state_database,
        "STATE_SCHEMA": state_schema,
    }.items():
        if not IDENTIFIER.fullmatch(value):
            raise ValueError(f"{variable} is not a valid SQL identifier")
    return Config(
        demo_data_url=env.get("DEMO_DATA_URL", DEMO_DATA_URL).strip(),
        state_database=state_database,
        state_schema=state_schema,
        guide_root=env.get("GUIDE_ROOT", GUIDE_ROOT).strip().strip("/"),
        guide_access=access,
        analysis_as_of=date.fromisoformat(as_of_raw) if as_of_raw else None,
        reconciliation_days=max(1, int(env.get("RECONCILIATION_DAYS", "7"))),
        demo_revision=max(0, int(env.get("DEMO_REVISION", "0"))),
        retention_mode=retention_mode,
        daily_guide_keep_days=max(1, int(env.get("DAILY_GUIDE_KEEP_DAYS", "90"))),
        weekly_guide_keep_weeks=max(1, int(env.get("WEEKLY_GUIDE_KEEP_WEEKS", "52"))),
        model=env.get("MODEL", "anthropic/claude-sonnet-4.6").strip(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-demo-data", type=Path)
    args = parser.parse_args()
    if args.build_demo_data is not None:
        metadata = build_demo_fixture(args.build_demo_data)
        print(json.dumps(asdict(metadata), default=str, sort_keys=True))
        return 0

    parse_config(os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
