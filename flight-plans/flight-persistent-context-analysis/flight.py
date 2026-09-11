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
    parse_config(os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
