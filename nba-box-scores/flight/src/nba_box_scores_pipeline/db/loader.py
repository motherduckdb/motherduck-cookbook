"""Bulk loader for the v3 ingest pipeline.

Ports `scripts/ingest/db/loader.ts`. Two important behavior preserves:

1. `INSERT OR REPLACE` on every write — same `(game_id, entity_id,
   period)` row written twice overwrites instead of duplicating. This is
   what makes Flight retries safe: if a run dies after writing some box
   scores but before marking the game ingested, the next run rewrites
   those rows and proceeds
2. Operational separation of "did we fetch raw" from "did we hydrate":
   `get_raw_game_ids()` drives `--fill-raw` (skip games we have raw for),
   `get_ingested_game_ids()` drives the normal skip-on-retry path

Output tables are configurable. Default is the v3 production tables;
during validation the loader can target e.g. `box_scores_new` so we
can diff against the cloned baseline before swapping.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Sequence

import duckdb

from ..parsers.nba_box_score import BoxScoreRow


@dataclass
class ScheduleRow:
    game_id: str
    game_date: str  # ISO timestamp; loader stores as TIMESTAMP
    home_team_id: int
    away_team_id: int
    home_team_abbreviation: str
    away_team_abbreviation: str
    home_team_score: int
    away_team_score: int
    game_status: str
    season_year: int | None
    season_type: str | None


@dataclass
class IngestionLogEntry:
    game_id: str
    season_year: int
    season_type: str
    ingestion_status: str = "success"
    error_message: str | None = None


_BOX_SCORE_COLS = (
    "game_id", "team_abbreviation", "entity_id", "player_name", "period", "minutes",
    "points", "rebounds", "assists", "steals", "blocks", "turnovers",
    "fg_made", "fg_attempted", "fg3_made", "fg3_attempted",
    "ft_made", "ft_attempted", "starter",
)

_SCHEDULE_COLS = (
    "game_id", "game_date",
    "home_team_id", "away_team_id",
    "home_team_abbreviation", "away_team_abbreviation",
    "home_team_score", "away_team_score",
    "game_status", "season_year", "season_type",
)

_INGESTION_LOG_COLS = (
    "game_id", "season_year", "season_type", "ingestion_status", "error_message",
)


def _placeholders(n: int) -> str:
    return ",".join(["?"] * n)


class Loader:
    """Bulk writes for v3's ingest tables.

    Pass non-default table names for the validation sandbox path —
    e.g. `Loader(con, box_scores_table="box_scores_new")` writes to
    `main.box_scores_new` instead of `main.box_scores`.
    """

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        box_scores_table: str = "box_scores",
        schedule_table: str = "schedule",
        ingestion_log_table: str = "ingestion_log",
        raw_pbpstats_table: str = "raw_game_data_pbpstats",
    ) -> None:
        self._con = con
        self._box_scores = box_scores_table
        self._schedule = schedule_table
        self._ingestion_log = ingestion_log_table
        self._raw_pbpstats = raw_pbpstats_table

    # ── Writes ──────────────────────────────────────────────────────

    def load_box_scores(self, rows: Sequence[BoxScoreRow]) -> None:
        if not rows:
            return
        sql = (
            f"INSERT OR REPLACE INTO main.{self._box_scores} "
            f"({', '.join(_BOX_SCORE_COLS)}) VALUES ({_placeholders(len(_BOX_SCORE_COLS))})"
        )
        params = [[getattr(r, col) for col in _BOX_SCORE_COLS] for r in rows]
        self._con.executemany(sql, params)

    def load_schedule(self, rows: Sequence[ScheduleRow]) -> None:
        if not rows:
            return
        sql = (
            f"INSERT OR REPLACE INTO main.{self._schedule} "
            f"({', '.join(_SCHEDULE_COLS)}) VALUES ({_placeholders(len(_SCHEDULE_COLS))})"
        )
        params = [[getattr(r, col) for col in _SCHEDULE_COLS] for r in rows]
        self._con.executemany(sql, params)

    def mark_ingested(self, entry: IngestionLogEntry) -> None:
        sql = (
            f"INSERT OR REPLACE INTO main.{self._ingestion_log} "
            f"({', '.join(_INGESTION_LOG_COLS)}) VALUES ({_placeholders(len(_INGESTION_LOG_COLS))})"
        )
        self._con.execute(sql, [getattr(entry, col) for col in _INGESTION_LOG_COLS])

    def replace_game(self, *, game_id: str, rows: Sequence[BoxScoreRow], log_entry: IngestionLogEntry) -> None:
        """Atomically make ``rows`` the canonical box score for ``game_id``.

        `process_season` hands us a complete game at a time, so the game is the
        natural replacement boundary: delete the game's existing rows, insert
        the freshly parsed set, and mark ingestion success — all in one
        transaction. `INSERT OR REPLACE` alone is enough for crash replay but
        can't drop rows that are no longer present (a force reingest or a
        parser/source correction that yields fewer rows), so we delete first.

        Fails closed on an empty row set: an empty/not-ready PBPStats payload
        parses to zero rows, and deleting + marking success would wipe a real
        game. Raise instead so the caller's retry/error path preserves the
        existing hydrated rows.
        """
        if not rows:
            raise ValueError(
                f"refusing to replace game {game_id} with zero parsed rows "
                "(empty or not-yet-ready box score)"
            )
        con = self._con
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DELETE FROM main.{self._box_scores} WHERE game_id = ?", [game_id])
            self.load_box_scores(rows)
            self.mark_ingested(log_entry)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    def store_raw_pbpstats(
        self,
        *,
        game_id: str,
        season_year: int,
        season_type: str,
        game_json: Any,
        box_score_json: Any,
        source_version: str | None = None,
    ) -> None:
        self._con.execute(
            f"INSERT OR REPLACE INTO main.{self._raw_pbpstats} "
            "(game_id, season_year, season_type, game_json, box_score_json, source_version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [game_id, season_year, season_type, json.dumps(game_json), json.dumps(box_score_json), source_version],
        )

    # ── Reads (skip-on-retry) ───────────────────────────────────────

    def _table_exists(self, table: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = ? LIMIT 1",
            [table],
        ).fetchone()
        return row is not None

    def is_game_ingested(self, game_id: str) -> bool:
        # A missing log table means nothing has been ingested yet (e.g. a
        # dry run against a not-yet-created sandbox set). Treat as not-done.
        if not self._table_exists(self._ingestion_log):
            return False
        (cnt,) = self._con.execute(
            f"SELECT COUNT(*) FROM main.{self._ingestion_log} "
            "WHERE game_id = ? AND ingestion_status = 'success'",
            [game_id],
        ).fetchone()
        return cnt > 0

    def get_ingested_game_ids(self, season_year: int, season_type: str) -> set[str]:
        if not self._table_exists(self._ingestion_log):
            return set()
        rows = self._con.execute(
            f"SELECT game_id FROM main.{self._ingestion_log} "
            "WHERE season_year = ? AND season_type = ? AND ingestion_status = 'success'",
            [season_year, season_type],
        ).fetchall()
        return {r[0] for r in rows}

    def get_raw_game_ids(self, season_year: int, season_type: str) -> set[str]:
        if not self._table_exists(self._raw_pbpstats):
            return set()
        rows = self._con.execute(
            f"SELECT game_id FROM main.{self._raw_pbpstats} "
            "WHERE season_year = ? AND season_type = ?",
            [season_year, season_type],
        ).fetchall()
        return {r[0] for r in rows}


__all__ = ["Loader", "ScheduleRow", "IngestionLogEntry", "asdict"]
