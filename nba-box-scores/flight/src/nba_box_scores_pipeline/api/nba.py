"""PBPStats HTTP client with retry and adaptive backoff.

Ports `scripts/ingest/api/client.ts` from the legacy pipeline. Behavior:

- Each request waits on the shared `RateLimiter.acquire()` before
  dispatching, so throughput adapts to upstream errors
- 429 → fixed 15s pause + signal throttle
- 5xx / network → exponential backoff with +/- 25% jitter, max 30s
- 4xx other than 429 → don't retry
- Up to 5 retries before giving up
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from ..rate_limiter import RateLimiter


_PBPSTATS_BASE = "https://api.pbpstats.com"

_DEFAULT_HEADERS = {
    "accept": "application/json",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "origin": "https://pbpstats.com",
    "referer": "https://pbpstats.com/",
}

_MAX_RETRIES = 5
_RATE_LIMIT_PAUSE_MS = 15_000
_INITIAL_BACKOFF_MS = 1_000
_MAX_BACKOFF_MS = 30_000


log = logging.getLogger(__name__)


def _jitter(base_ms: int) -> float:
    return base_ms * (0.75 + random.random() * 0.5)


class PBPStatsClient:
    def __init__(self, rate_limiter: RateLimiter, *, client: httpx.Client | None = None) -> None:
        self._rate_limiter = rate_limiter
        self._client = client or httpx.Client(headers=_DEFAULT_HEADERS, timeout=30.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "PBPStatsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _fetch(self, path: str, params: dict[str, str]) -> Any:
        url = f"{_PBPSTATS_BASE}{path}"
        backoff_ms = _INITIAL_BACKOFF_MS

        for attempt in range(_MAX_RETRIES + 1):
            self._rate_limiter.acquire()
            status: int | None = None
            try:
                resp = self._client.get(url, params=params)
                status = resp.status_code
                if status >= 400:
                    resp.raise_for_status()
                self._rate_limiter.record_success()
                return resp.json()
            except httpx.HTTPStatusError as err:
                status = err.response.status_code
                if 400 <= status < 500 and status != 429:
                    raise
                if status in (429, 503):
                    self._rate_limiter.record_throttle()
                if attempt == _MAX_RETRIES:
                    raise

                if status == 429:
                    delay_ms = _RATE_LIMIT_PAUSE_MS
                    log.warning("rate limited (429); pausing %dms (attempt %d/%d)", delay_ms, attempt + 1, _MAX_RETRIES)
                else:
                    delay_ms = _jitter(backoff_ms)
                    backoff_ms = min(backoff_ms * 2, _MAX_BACKOFF_MS)
                    log.warning(
                        "request failed (status=%s); retry in %dms (attempt %d/%d)",
                        status, round(delay_ms), attempt + 1, _MAX_RETRIES,
                    )
                time.sleep(delay_ms / 1000.0)
            except httpx.HTTPError:
                # Network-level failure: same exponential backoff as 5xx
                if attempt == _MAX_RETRIES:
                    raise
                delay_ms = _jitter(backoff_ms)
                backoff_ms = min(backoff_ms * 2, _MAX_BACKOFF_MS)
                log.warning(
                    "network error; retry in %dms (attempt %d/%d)",
                    round(delay_ms), attempt + 1, _MAX_RETRIES,
                )
                time.sleep(delay_ms / 1000.0)

        raise RuntimeError("unreachable: retry loop exited without return")

    def get_games(self, season: str, season_type: str) -> Any:
        log.debug("fetching games list season=%s type=%s", season, season_type)
        return self._fetch("/get-games/nba", {"Season": season, "SeasonType": season_type})

    def get_box_score(self, game_id: str) -> Any:
        log.debug("fetching box score game_id=%s", game_id)
        return self._fetch("/get-game-stats", {"GameId": game_id, "Type": "Player"})
