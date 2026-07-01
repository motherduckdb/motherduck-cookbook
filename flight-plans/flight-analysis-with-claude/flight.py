"""
Build your own analysis agent, then run it on a schedule (MotherDuck Flight).

An agent is just a loop: call a model, let it call tools, feed results back,
repeat until it answers. You do not hand-write that loop here, Pydantic AI runs
it. What makes the agent strong is what you compose around the loop:

  1. a model, via OpenRouter, so the agent is not locked to one provider
     (swap the MODEL slug for any tool-capable model),
  2. a skill: the inline SKILL constant below, your company/domain context,
  3. tools you build yourself: explore_warehouse (read-only SQL over
     sample_data) and get_weather (the Open-Meteo archive API).

The Flight discovers the active NYC boroughs in a recent window of the public
311 dataset, then fans out one agent per borough to write a "notable things"
brief, grounded in real query results and enriched with weather where it
explains activity. Briefs are written to flights_demo.main.borough_briefs. One
borough failing never aborts the batch.

The sample data is a frozen snapshot (it ends in 2023), so the lookback window
is anchored to MAX(created_date), not now(). Against a live table you would
anchor to now() instead.

Runtime inputs:
  OPENROUTER_API_KEY  remapped from a Flights secret (any *_OPENROUTER_API_KEY)
  MOTHERDUCK_TOKEN    auto-injected by the Flights runtime; used by
                      duckdb.connect for discovery, persistence, and the
                      warehouse tool
  BRIEF_WINDOW_DAYS   lookback window in days (default "7")
  CONCURRENCY         max simultaneous agents (default "3")
  MODEL               OpenRouter model slug (default "anthropic/claude-sonnet-4.6")
  MAX_BOROUGHS        cap borough count for testing; "0" = all (default "0")
  BOROUGHS            optional comma-separated override; skips discovery
"""

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import duckdb
from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


WINDOW_DAYS = max(1, int(os.environ.get("BRIEF_WINDOW_DAYS", "7")))
CONCURRENCY = max(1, int(os.environ.get("CONCURRENCY", "3")))
MODEL = os.environ.get("MODEL", "anthropic/claude-sonnet-4.6").strip()
MAX_BOROUGHS = int(os.environ.get("MAX_BOROUGHS", "0"))
BOROUGHS_OVERRIDE = os.environ.get("BOROUGHS", "").strip()

# The warehouse tool connects to a single database (md:sample_data). Discovery
# and persistence use md: because they also write to flights_demo.
SOURCE_TABLE = "sample_data.nyc.service_requests"
RESULTS_TABLE = "flights_demo.main.borough_briefs"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"


def resolve_openrouter_key() -> str:
    """Return the OpenRouter API key.

    A local run sets OPENROUTER_API_KEY directly. Deployed as a Flight, the key
    comes from a `TYPE flights` secret, which MotherDuck injects under the env
    var `<secret_name>_<PARAM>`, not the bare param name. So a secret named
    `openrouter` with an OPENROUTER_API_KEY param arrives as
    `openrouter_OPENROUTER_API_KEY`. Accept the exact name first (local), then
    any var ending in the suffix (the secret, whatever you named it).
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    for name, value in os.environ.items():
        if name.endswith("_OPENROUTER_API_KEY") and value.strip():
            log(f"Using OPENROUTER_API_KEY from secret env var {name!r}")
            return value.strip()
    raise SystemExit(
        "OPENROUTER_API_KEY is required (set it locally or add a Flights secret)."
    )
