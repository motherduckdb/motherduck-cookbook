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
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping, Protocol, Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
RSS_FEEDS = (
    ("bbc-ducks", "https://feeds.bbci.co.uk/news/topics/czednw5qgllt/rss.xml"),
    ("bbc-london", "https://feeds.bbci.co.uk/news/england/london/rss.xml"),
)
WEATHER_DAILY_VARIABLES = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
    "weather_code",
)
HTTP_TIMEOUT_SECONDS = 20
PUBLIC_SIGNAL_USER_AGENT = "MotherDuck persistent-context-analysis Flight/1.0"
LONDON_TIMEZONE = ZoneInfo("Europe/London")
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
ComparisonLabel = Literal[
    "prior_day",
    "same_weekday",
    "trailing_28_days",
    "prior_week",
    "trailing_4_weeks",
    "prior_month",
    "trailing_90_days",
]


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
    comparison_label: ComparisonLabel
    comparison_period: PeriodKey
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


def _payload_hash(payload: bytes | Mapping[str, object]) -> str:
    if isinstance(payload, bytes):
        content = payload
    else:
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(content).hexdigest()


def _canonical_http_url(value: str) -> str | None:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), host, parsed.path or "/", parsed.query, "")
    )


def _rss_child_text(item: ET.Element, name: str) -> str:
    for child in item:
        if child.tag.rsplit("}", 1)[-1] == name:
            return "".join(child.itertext()).strip()
    return ""


def _rss_feed_id(feed_url: str) -> str:
    for feed_id, known_url in RSS_FEEDS:
        if feed_url == known_url:
            return feed_id
    raise ValueError(f"Unsupported RSS feed URL: {feed_url}")


def parse_rss(
    feed_url: str, payload: bytes, retrieved_at: datetime
) -> list[ExternalSignal]:
    feed_id = _rss_feed_id(feed_url)
    root = ET.fromstring(payload)
    signals: list[ExternalSignal] = []
    payload_hash = _payload_hash(payload)
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "item":
            continue
        link = _canonical_http_url(_rss_child_text(item, "link"))
        if link is None:
            continue
        guid = _rss_child_text(item, "guid") or link
        published_at = parsedate_to_datetime(_rss_child_text(item, "pubDate"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        published_at = published_at.astimezone(timezone.utc)
        title = _rss_child_text(item, "title")[:300]
        description = _rss_child_text(item, "description")[:1_000]
        attributes = tuple(
            sorted(
                (
                    ("feed_id", feed_id),
                    ("guid", guid),
                    ("description", description),
                )
            )
        )
        identity = f"{feed_id}|{guid}".encode()
        signals.append(
            ExternalSignal(
                signal_id=f"rss-{hashlib.sha256(identity).hexdigest()[:16]}",
                provider=feed_id,
                provider_id=guid,
                starts_at=published_at,
                ends_at=published_at,
                location="London",
                title=title,
                source_url=link,
                attributes=attributes,
                payload_hash=payload_hash,
                retrieved_at=retrieved_at.astimezone(timezone.utc),
            )
        )
    return signals


def fetch_rss_signals(
    opener: Callable[..., object], retrieved_at: datetime
) -> tuple[list[ExternalSignal], list[str]]:
    signals: list[ExternalSignal] = []
    caveats: list[str] = []
    for feed_id, feed_url in RSS_FEEDS:
        request = urllib.request.Request(
            feed_url, headers={"User-Agent": PUBLIC_SIGNAL_USER_AGENT}
        )
        try:
            with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                payload = response.read()
            signals.extend(parse_rss(feed_url, payload, retrieved_at))
        except (OSError, ET.ParseError, UnicodeDecodeError, ValueError) as error:
            caveats.append(f"{feed_id} RSS is unavailable: {error}")
    return signals, caveats


def parse_weather(payload: Mapping[str, object]) -> list[ExternalSignal]:
    daily = payload.get("daily")
    if not isinstance(daily, Mapping):
        raise ValueError("Open-Meteo response does not contain daily weather")
    values_by_name: dict[str, list[object]] = {}
    for name in ("time", *WEATHER_DAILY_VARIABLES):
        values = daily.get(name)
        if not isinstance(values, list):
            raise ValueError(f"Open-Meteo response is missing daily {name}")
        values_by_name[name] = values
    dates = values_by_name["time"]
    if any(len(values) != len(dates) for values in values_by_name.values()):
        raise ValueError("Open-Meteo daily values have inconsistent lengths")

    payload_hash = _payload_hash(payload)
    retrieved_at = datetime.now(timezone.utc)
    signals: list[ExternalSignal] = []
    for index, raw_day in enumerate(dates):
        day = date.fromisoformat(str(raw_day))
        if any(values_by_name[name][index] is None for name in WEATHER_DAILY_VARIABLES):
            continue
        starts_at = datetime.combine(day, time(), LONDON_TIMEZONE).astimezone(
            timezone.utc
        )
        ends_at = datetime.combine(
            day + timedelta(days=1), time(), LONDON_TIMEZONE
        ).astimezone(timezone.utc)
        attributes = tuple(
            sorted(
                (name, str(values_by_name[name][index]))
                for name in WEATHER_DAILY_VARIABLES
            )
        )
        provider_id = f"london-weather-{day.isoformat()}"
        signals.append(
            ExternalSignal(
                signal_id=provider_id,
                provider="open-meteo",
                provider_id=provider_id,
                starts_at=starts_at,
                ends_at=ends_at,
                location="London",
                title=f"London weather for {day.isoformat()}",
                source_url=WEATHER_URL,
                attributes=attributes,
                payload_hash=payload_hash,
                retrieved_at=retrieved_at,
            )
        )
    return signals


def fetch_weather_signals(
    opener: Callable[..., object], start: date, end: date
) -> tuple[list[ExternalSignal], list[str]]:
    request_url = f"{WEATHER_URL}?{
        urllib.parse.urlencode(
            {
                'latitude': LONDON_LATITUDE,
                'longitude': LONDON_LONGITUDE,
                'daily': ','.join(WEATHER_DAILY_VARIABLES),
                'timezone': 'Europe/London',
                'start_date': start.isoformat(),
                'end_date': end.isoformat(),
            }
        )
    }"
    request = urllib.request.Request(
        request_url, headers={"User-Agent": PUBLIC_SIGNAL_USER_AGENT}
    )
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
        signals = parse_weather(payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return [], [f"London weather is unavailable: {error}"]
    retrieved_at = datetime.now(timezone.utc)
    available_signals = [
        replace(signal, source_url=request_url, retrieved_at=retrieved_at)
        for signal in signals
    ]
    available_ids = {signal.provider_id for signal in available_signals}
    unavailable_days = {
        str(raw_day)
        for raw_day in payload["daily"]["time"]
        if f"london-weather-{raw_day}" not in available_ids
    }
    return available_signals, [
        f"London weather is unavailable for {day}" for day in sorted(unavailable_days)
    ]


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
        """
        SELECT sha256(string_agg(row_json, '\n' ORDER BY row_json))
        FROM (
            SELECT row_to_json(d)::VARCHAR AS row_json
            FROM demo_sales d
        )
        """
    ).fetchone()[0]
    return FixtureMetadata(source_hash, row_count, min_date, max_date)


def _previous_month_start(value: date) -> date:
    if value.month == 1:
        return date(value.year - 1, 12, 1)
    return date(value.year, value.month - 1, 1)


def periods_to_process(
    as_of: date,
    reconciliation_days: int,
    bootstrap: bool,
) -> list[PeriodKey]:
    if reconciliation_days < 1:
        raise ValueError("reconciliation_days must be at least 1")

    window_start = as_of - timedelta(days=reconciliation_days)
    periods = {
        PeriodKey("day", day, day + timedelta(days=1))
        for day in (
            window_start + timedelta(days=offset)
            for offset in range(reconciliation_days)
        )
    }

    latest_week_end = as_of - timedelta(days=as_of.weekday())
    week_end = latest_week_end
    while week_end > window_start:
        periods.add(PeriodKey("week", week_end - timedelta(days=7), week_end))
        week_end -= timedelta(days=7)

    latest_month_end = date(as_of.year, as_of.month, 1)
    month_end = latest_month_end
    while month_end > window_start:
        periods.add(PeriodKey("month", _previous_month_start(month_end), month_end))
        month_end = _previous_month_start(month_end)

    if bootstrap:
        periods.add(
            PeriodKey(
                "week",
                latest_week_end - timedelta(days=7),
                latest_week_end,
            )
        )
        periods.add(
            PeriodKey(
                "month",
                _previous_month_start(latest_month_end),
                latest_month_end,
            )
        )

    grain_order = {"day": 0, "week": 1, "month": 2}
    return sorted(
        periods,
        key=lambda period: (
            period.period_start,
            grain_order[period.grain],
            period.period_end,
        ),
    )


def _comparison_periods_with_labels(
    period: PeriodKey,
) -> list[tuple[ComparisonLabel, PeriodKey]]:
    scope = period.scope
    if period.grain == "day":
        return [
            (
                "prior_day",
                PeriodKey(
                    "day",
                    period.period_start - timedelta(days=1),
                    period.period_start,
                    scope,
                ),
            ),
            (
                "same_weekday",
                PeriodKey(
                    "day",
                    period.period_start - timedelta(days=7),
                    period.period_end - timedelta(days=7),
                    scope,
                ),
            ),
            (
                "trailing_28_days",
                PeriodKey(
                    "day",
                    period.period_start - timedelta(days=28),
                    period.period_start,
                    scope,
                ),
            ),
        ]
    if period.grain == "week":
        return [
            (
                "prior_week",
                PeriodKey(
                    "week",
                    period.period_start - timedelta(days=7),
                    period.period_start,
                    scope,
                ),
            ),
            (
                "trailing_4_weeks",
                PeriodKey(
                    "week",
                    period.period_start - timedelta(days=28),
                    period.period_start,
                    scope,
                ),
            ),
        ]
    if period.grain == "month":
        return [
            (
                "prior_month",
                PeriodKey(
                    "month",
                    _previous_month_start(period.period_start),
                    period.period_start,
                    scope,
                ),
            ),
            (
                "trailing_90_days",
                PeriodKey(
                    "month",
                    period.period_start - timedelta(days=90),
                    period.period_start,
                    scope,
                ),
            ),
        ]
    raise ValueError(f"Unsupported period grain: {period.grain}")


def comparison_periods(period: PeriodKey) -> list[PeriodKey]:
    return [
        comparison_period
        for _, comparison_period in _comparison_periods_with_labels(period)
    ]


def _query_metric_evidence_rows(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    unit_column: str,
    dimension: str | None,
    current_period: PeriodKey,
    comparison_period: PeriodKey,
) -> list[tuple[object, ...]]:
    dimension_select = (
        f"metrics.{dimension}::VARCHAR AS dimension_value"
        if dimension is not None
        else "NULL::VARCHAR AS dimension_value"
    )
    group_by = f"GROUP BY metrics.{dimension}" if dimension is not None else ""
    current_limit = (
        "ORDER BY net_revenue DESC NULLS LAST, dimension_value LIMIT 10"
        if dimension is not None
        else ""
    )
    metric_names = (
        "net_revenue",
        "gross_revenue",
        "orders",
        "average_order_value",
        "refund_rate",
        "cancellation_rate",
        "units_per_order",
        "returning_customer_share",
    )
    metric_rows = "\nUNION ALL\n".join(
        f"""
        SELECT
            dimension_value,
            sample_size,
            '{metric}' AS metric,
            current_{metric} AS current_value,
            comparison_{metric} AS comparison_value
        FROM paired
        """
        for metric in metric_names
    )
    return con.execute(
        f"""
        WITH first_orders AS (
            SELECT customer_id, min(order_at) AS first_order_at
            FROM demo_orders
            GROUP BY customer_id
        ),
        current_metrics AS (
          SELECT
            {dimension_select},
            coalesce(sum(metrics.net_revenue_gbp), 0) AS net_revenue,
            coalesce(sum(metrics.gross_revenue_gbp), 0) AS gross_revenue,
            count(DISTINCT metrics.order_id) AS orders,
            coalesce(sum(metrics.net_revenue_gbp), 0)
                / nullif(count(DISTINCT metrics.order_id), 0) AS average_order_value,
            coalesce(sum(metrics.refunded_amount_gbp), 0)
                / nullif(sum(metrics.gross_revenue_gbp), 0) AS refund_rate,
            count(DISTINCT CASE WHEN metrics.cancelled THEN metrics.order_id END)
                / nullif(count(DISTINCT metrics.order_id), 0)::DECIMAL
                AS cancellation_rate,
            coalesce(sum(metrics.{unit_column}), 0)
                / nullif(count(DISTINCT metrics.order_id), 0) AS units_per_order,
            count(DISTINCT CASE
                WHEN first_orders.first_order_at < ? THEN metrics.customer_id
            END) / nullif(count(DISTINCT metrics.customer_id), 0)::DECIMAL
                AS returning_customer_share
          FROM {source} AS metrics
          JOIN first_orders USING (customer_id)
          WHERE metrics.order_at >= ? AND metrics.order_at < ?
          {group_by}
          {current_limit}
        ),
        comparison_metrics AS (
          SELECT
            {dimension_select},
            coalesce(sum(metrics.net_revenue_gbp), 0) AS net_revenue,
            coalesce(sum(metrics.gross_revenue_gbp), 0) AS gross_revenue,
            count(DISTINCT metrics.order_id) AS orders,
            coalesce(sum(metrics.net_revenue_gbp), 0)
                / nullif(count(DISTINCT metrics.order_id), 0) AS average_order_value,
            coalesce(sum(metrics.refunded_amount_gbp), 0)
                / nullif(sum(metrics.gross_revenue_gbp), 0) AS refund_rate,
            count(DISTINCT CASE WHEN metrics.cancelled THEN metrics.order_id END)
                / nullif(count(DISTINCT metrics.order_id), 0)::DECIMAL
                AS cancellation_rate,
            coalesce(sum(metrics.{unit_column}), 0)
                / nullif(count(DISTINCT metrics.order_id), 0) AS units_per_order,
            count(DISTINCT CASE
                WHEN first_orders.first_order_at < ? THEN metrics.customer_id
            END) / nullif(count(DISTINCT metrics.customer_id), 0)::DECIMAL
                AS returning_customer_share
          FROM {source} AS metrics
          JOIN first_orders USING (customer_id)
          WHERE metrics.order_at >= ? AND metrics.order_at < ?
          {group_by}
        ),
        paired AS (
          SELECT
            current_metrics.dimension_value,
            current_metrics.orders AS sample_size,
            current_metrics.net_revenue AS current_net_revenue,
            comparison_metrics.net_revenue AS comparison_net_revenue,
            current_metrics.gross_revenue AS current_gross_revenue,
            comparison_metrics.gross_revenue AS comparison_gross_revenue,
            current_metrics.orders AS current_orders,
            comparison_metrics.orders AS comparison_orders,
            current_metrics.average_order_value AS current_average_order_value,
            comparison_metrics.average_order_value AS comparison_average_order_value,
            current_metrics.refund_rate AS current_refund_rate,
            comparison_metrics.refund_rate AS comparison_refund_rate,
            current_metrics.cancellation_rate AS current_cancellation_rate,
            comparison_metrics.cancellation_rate AS comparison_cancellation_rate,
            current_metrics.units_per_order AS current_units_per_order,
            comparison_metrics.units_per_order AS comparison_units_per_order,
            current_metrics.returning_customer_share AS current_returning_customer_share,
            comparison_metrics.returning_customer_share
                AS comparison_returning_customer_share
          FROM current_metrics
          LEFT JOIN comparison_metrics
            ON current_metrics.dimension_value
              IS NOT DISTINCT FROM comparison_metrics.dimension_value
        ),
        metric_values AS (
          {metric_rows}
        )
        SELECT
          dimension_value,
          sample_size,
          metric,
          cast(round(current_value, 4) AS DECIMAL(20, 4)) AS current_value,
          cast(round(comparison_value, 4) AS DECIMAL(20, 4)) AS comparison_value,
          cast(round(current_value - comparison_value, 4) AS DECIMAL(20, 4))
            AS absolute_change,
          CASE
            WHEN comparison_value IS NULL OR comparison_value = 0 THEN NULL
            ELSE cast(
              round(
                (current_value - comparison_value)
                  / abs(comparison_value) * 100,
                4
              ) AS DECIMAL(20, 4)
            )
          END AS percentage_change
        FROM metric_values
        ORDER BY dimension_value NULLS FIRST, metric
        """,
        [
            current_period.period_start,
            current_period.period_start,
            current_period.period_end,
            comparison_period.period_start,
            comparison_period.period_start,
            comparison_period.period_end,
        ],
    ).fetchall()


def _metric_id(
    period: PeriodKey,
    comparison_label: ComparisonLabel,
    comparison_period: PeriodKey,
    metric: str,
    dimensions: tuple[tuple[str, str], ...],
) -> str:
    identity = (
        period.grain,
        period.period_start.isoformat(),
        period.period_end.isoformat(),
        period.scope,
        comparison_label,
        comparison_period.period_start.isoformat(),
        comparison_period.period_end.isoformat(),
        metric,
        *[f"{name}={value}" for name, value in dimensions],
    )
    digest = hashlib.sha256("|".join(identity).encode()).hexdigest()[:16]
    return f"metric-{digest}"


def compute_metric_evidence(
    con: duckdb.DuckDBPyConnection,
    period: PeriodKey,
) -> list[MetricEvidence]:
    con.execute(
        """
        CREATE OR REPLACE TEMP VIEW demo_order_lines AS
        SELECT * FROM demo_sales;

        CREATE OR REPLACE TEMP VIEW demo_orders AS
        SELECT
          order_id,
          min(order_at) AS order_at,
          any_value(customer_id) AS customer_id,
          any_value(store_id) AS store_id,
          any_value(store_type) AS store_type,
          any_value(acquisition_channel) AS acquisition_channel,
          bool_or(cancelled) AS cancelled,
          any_value(financial_status) AS financial_status,
          sum(gross_revenue_gbp) AS gross_revenue_gbp,
          sum(net_revenue_gbp) AS net_revenue_gbp,
          sum(refunded_amount_gbp) AS refunded_amount_gbp,
          sum(quantity) AS units
        FROM demo_sales
        GROUP BY order_id;
        """
    )
    evidence: list[MetricEvidence] = []
    group_specs = (
        ("demo_orders", "units", None),
        ("demo_orders", "units", "store_id"),
        ("demo_orders", "units", "store_type"),
        ("demo_orders", "units", "acquisition_channel"),
        ("demo_order_lines", "quantity", "product_category"),
    )

    for source, unit_column, dimension in group_specs:
        for comparison_label, comparison_period in _comparison_periods_with_labels(
            period
        ):
            rows = _query_metric_evidence_rows(
                con,
                source=source,
                unit_column=unit_column,
                dimension=dimension,
                current_period=period,
                comparison_period=comparison_period,
            )
            for (
                dimension_value,
                sample_size,
                metric,
                current_value,
                comparison_value,
                absolute_change,
                percentage_change,
            ) in rows:
                if int(sample_size) == 0:
                    continue
                dimensions = (
                    () if dimension is None else ((dimension, str(dimension_value)),)
                )
                dimensions = tuple(sorted(dimensions))
                evidence.append(
                    MetricEvidence(
                        evidence_id=_metric_id(
                            period,
                            comparison_label,
                            comparison_period,
                            metric,
                            dimensions,
                        ),
                        period=period,
                        metric=metric,
                        dimensions=dimensions,
                        comparison_label=comparison_label,
                        comparison_period=comparison_period,
                        current_value=current_value,
                        comparison_value=comparison_value,
                        absolute_change=absolute_change,
                        percentage_change=percentage_change,
                        sample_size=int(sample_size),
                    )
                )

    return evidence


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
