"""DDL for `nba_box_scores_v3`.

Source of truth for v3's schema. Mirrors `scripts/ingest/db/schema.ts`
from the legacy pipeline. Two changes:

1. View definitions are parameterized by database name via `{db}`. The
   legacy v2 schema hard-coded `nba_box_scores_v2` in the view refs,
   which a `CREATE DATABASE ... FROM ...` clone propagated verbatim
   (so v3's views currently point at v2 — `ensure_schema(con, "v3")`
   fixes that)
2. `data_quality_quarantine` is dropped from the v3 schema — it was
   tied to the GH-Issues-based DQ workflow which is out of scope for
   this migration. The table will be dropped from v3 in a separate
   step; until then the cloned baseline is left alone

The PK definitions are what gives `INSERT OR REPLACE` its idempotency:
re-loading the same `(game_id, entity_id, period)` overwrites instead
of duplicating.
"""

from __future__ import annotations

import duckdb


CREATE_SCHEDULE = """
CREATE TABLE IF NOT EXISTS main.schedule (
  game_id TEXT PRIMARY KEY,
  game_date TIMESTAMP NOT NULL,
  home_team_id INTEGER NOT NULL,
  away_team_id INTEGER NOT NULL,
  home_team_abbreviation TEXT NOT NULL,
  away_team_abbreviation TEXT NOT NULL,
  home_team_score INTEGER NOT NULL,
  away_team_score INTEGER NOT NULL,
  game_status TEXT NOT NULL,
  season_year INTEGER,
  season_type TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_BOX_SCORES = """
CREATE TABLE IF NOT EXISTS main.box_scores (
  game_id VARCHAR,
  team_abbreviation VARCHAR,
  entity_id VARCHAR,
  player_name VARCHAR,
  period VARCHAR NOT NULL DEFAULT 'FullGame',
  minutes VARCHAR,
  points INTEGER NOT NULL DEFAULT 0,
  rebounds INTEGER NOT NULL DEFAULT 0,
  assists INTEGER NOT NULL DEFAULT 0,
  steals INTEGER NOT NULL DEFAULT 0,
  blocks INTEGER NOT NULL DEFAULT 0,
  turnovers INTEGER NOT NULL DEFAULT 0,
  fg_made INTEGER NOT NULL DEFAULT 0,
  fg_attempted INTEGER NOT NULL DEFAULT 0,
  fg3_made INTEGER NOT NULL DEFAULT 0,
  fg3_attempted INTEGER NOT NULL DEFAULT 0,
  ft_made INTEGER NOT NULL DEFAULT 0,
  ft_attempted INTEGER NOT NULL DEFAULT 0,
  starter INTEGER,
  PRIMARY KEY (game_id, entity_id, period)
);
"""

CREATE_INGESTION_LOG = """
CREATE TABLE IF NOT EXISTS main.ingestion_log (
  game_id TEXT PRIMARY KEY,
  season_year INTEGER NOT NULL,
  season_type TEXT NOT NULL,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ingestion_status TEXT NOT NULL DEFAULT 'success',
  error_message TEXT,
  audited_at TIMESTAMP
);
"""

CREATE_RAW_GAME_DATA_PBPSTATS = """
CREATE TABLE IF NOT EXISTS main.raw_game_data_pbpstats (
  game_id TEXT PRIMARY KEY,
  season_year INTEGER NOT NULL,
  season_type TEXT NOT NULL,
  game_json JSON NOT NULL,
  box_score_json JSON NOT NULL,
  source_version TEXT,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# View DDL: `{db}` is substituted to the active database name so the views
# read from the same catalog the loader writes into (clones inherit the
# original DDL, which is why we re-issue these on every ensure_schema).

CREATE_TEAM_STATS_VIEW = """
CREATE OR REPLACE VIEW main.team_stats AS
WITH period_scores AS (
  SELECT
    game_id, team_abbreviation, period,
    '12:00' as minutes,
    CAST(SUM(points) AS INTEGER) as points,
    CAST(SUM(rebounds) AS INTEGER) as rebounds,
    CAST(SUM(assists) AS INTEGER) as assists,
    CAST(SUM(steals) AS INTEGER) as steals,
    CAST(SUM(blocks) AS INTEGER) as blocks,
    CAST(SUM(turnovers) AS INTEGER) as turnovers,
    CAST(SUM(fg_made) AS INTEGER) as fg_made,
    CAST(SUM(fg_attempted) AS INTEGER) as fg_attempted,
    CAST(SUM(fg3_made) AS INTEGER) as fg3_made,
    CAST(SUM(fg3_attempted) AS INTEGER) as fg3_attempted,
    CAST(SUM(ft_made) AS INTEGER) as ft_made,
    CAST(SUM(ft_attempted) AS INTEGER) as ft_attempted
  FROM {db}.main.box_scores
  WHERE period != 'FullGame'
  GROUP BY game_id, team_abbreviation, period
)
SELECT * FROM period_scores
UNION ALL
SELECT
  game_id, team_abbreviation,
  'FullGame' as period,
  NULL as minutes,
  CAST(SUM(points) AS INTEGER) as points,
  CAST(SUM(rebounds) AS INTEGER) as rebounds,
  CAST(SUM(assists) AS INTEGER) as assists,
  CAST(SUM(steals) AS INTEGER) as steals,
  CAST(SUM(blocks) AS INTEGER) as blocks,
  CAST(SUM(turnovers) AS INTEGER) as turnovers,
  CAST(SUM(fg_made) AS INTEGER) as fg_made,
  CAST(SUM(fg_attempted) AS INTEGER) as fg_attempted,
  CAST(SUM(fg3_made) AS INTEGER) as fg3_made,
  CAST(SUM(fg3_attempted) AS INTEGER) as fg3_attempted,
  CAST(SUM(ft_made) AS INTEGER) as ft_made,
  CAST(SUM(ft_attempted) AS INTEGER) as ft_attempted
FROM period_scores
WHERE period <> 'FullGame'
GROUP BY game_id, team_abbreviation;
"""

CREATE_PLAYERS_VIEW = """
CREATE OR REPLACE VIEW main.players AS
SELECT DISTINCT entity_id, player_name
FROM {db}.main.box_scores
WHERE period = 'FullGame';
"""

# game_quality view ported verbatim — it's a behaviour-defining view that
# the UI depends on. Internal structure (CTEs, scoring weights) is opaque
# and out of scope to revise here.
CREATE_GAME_QUALITY_VIEW = """
CREATE OR REPLACE VIEW main.game_quality AS
WITH cte_schedule AS MATERIALIZED (
  SELECT
    CAST(yearweek(CAST(timezone('America/New_York', timezone('UTC', game_date)) AS DATE)) AS INTEGER) AS week_id,
    game_id
  FROM {db}.main.schedule
),
cte_box_score_cnt AS (
  SELECT s.week_id, COUNT(*) AS gm_count
  FROM {db}.main.box_scores bs
  INNER JOIN cte_schedule s ON bs.game_id = s.game_id
  WHERE bs.period = 'FullGame'
    AND CAST(substring(bs.minutes, 1, instr(bs.minutes, ':') - 1) AS INTEGER) >= 15
  GROUP BY ALL
),
cte_prep AS MATERIALIZED (
  SELECT
    bs.game_id, bs.entity_id, bs.player_name,
    CASE WHEN bs.fg_attempted > 0 THEN round(CAST(bs.fg_made AS DOUBLE) / bs.fg_attempted, 3) ELSE 0 END AS fg_pct,
    CASE WHEN bs.ft_attempted > 0 THEN round(CAST(bs.ft_made AS DOUBLE) / bs.ft_attempted, 3) ELSE 0 END AS ft_pct,
    round((fg_pct - 0.47) * bs.fg_attempted, 2) AS fg_v,
    round((ft_pct - 0.80) * bs.ft_attempted, 2) AS ft_v,
    bs.fg3_made, bs.points, bs.rebounds, bs.assists, bs.steals, bs.blocks, bs.turnovers,
    s.week_id
  FROM {db}.main.box_scores bs
  INNER JOIN cte_schedule s ON bs.game_id = s.game_id
  WHERE bs.period = 'FullGame'
    AND CAST(substring(bs.minutes, 1, instr(bs.minutes, ':') - 1) AS INTEGER) >= 15
  ORDER BY week_id, entity_id
),
cte_missing_games AS (
  SELECT
    bs.game_id, bs.entity_id, bs.player_name,
    CASE WHEN bs.fg_attempted > 0 THEN round(CAST(bs.fg_made AS DOUBLE) / bs.fg_attempted, 3) ELSE 0 END AS fg_pct,
    CASE WHEN bs.ft_attempted > 0 THEN round(CAST(bs.ft_made AS DOUBLE) / bs.ft_attempted, 3) ELSE 0 END AS ft_pct,
    round((fg_pct - 0.47) * bs.fg_attempted, 2) AS fg_v,
    round((ft_pct - 0.80) * bs.ft_attempted, 2) AS ft_v,
    bs.fg3_made, bs.points, bs.rebounds, bs.assists, bs.steals, bs.blocks, bs.turnovers,
    s.week_id
  FROM {db}.main.box_scores bs
  INNER JOIN cte_schedule s ON bs.game_id = s.game_id
  WHERE bs.period = 'FullGame'
    AND CAST(substring(bs.minutes, 1, instr(bs.minutes, ':') - 1) AS INTEGER) < 15
),
cte_final AS (
  (
    SELECT
      base.*,
      CAST(SUM(CAST(
        (CAST(base.fg_v > comp.fg_v AS INTEGER)
        + CAST(base.ft_v > comp.ft_v AS INTEGER)
        + CAST(base.fg3_made > comp.fg3_made AS INTEGER)
        + CAST(base.points > comp.points AS INTEGER)
        + CAST(base.rebounds > comp.rebounds AS INTEGER)
        + CAST(base.assists > comp.assists AS INTEGER)
        + CAST(base.steals > comp.steals AS INTEGER)
        + CAST(base.blocks > comp.blocks AS INTEGER)
        + CAST(base.turnovers < comp.turnovers AS INTEGER)
        + (CAST(base.fg_v = comp.fg_v AS INTEGER)
          + CAST(base.ft_v = comp.ft_v AS INTEGER)
          + CAST(base.fg3_made = comp.fg3_made AS INTEGER)
          + CAST(base.points = comp.points AS INTEGER)
          + CAST(base.rebounds = comp.rebounds AS INTEGER)
          + CAST(base.assists = comp.assists AS INTEGER)
          + CAST(base.steals = comp.steals AS INTEGER)
          + CAST(base.blocks = comp.blocks AS INTEGER)
          + CAST(base.turnovers = comp.turnovers AS INTEGER))
          * 0.5
        ) > 4.5 AS INTEGER)) AS INTEGER) AS wins,
      bsc.gm_count
    FROM cte_prep base
    LEFT JOIN cte_prep comp ON comp.entity_id != base.entity_id AND comp.week_id = base.week_id
    LEFT JOIN cte_box_score_cnt bsc ON bsc.week_id = base.week_id
    GROUP BY ALL
  )
  UNION ALL
  (
    SELECT mg.*, -1 AS wins, bsc.gm_count
    FROM cte_missing_games mg
    LEFT JOIN cte_box_score_cnt bsc ON bsc.week_id = mg.week_id
  )
)
SELECT *,
  CASE WHEN wins != -1 THEN round(CAST(wins AS DOUBLE) / gm_count, 4) ELSE -1 END AS game_quality
FROM cte_final;
"""


# The four operational tables, keyed by their canonical name. Suffixed
# variants (e.g. box_scores_new / ingestion_log_new) form an isolated
# "table set" for sandbox/validation runs — see ensure_tables_suffixed.
TABLE_DDL_BY_NAME = {
    "schedule": CREATE_SCHEDULE,
    "box_scores": CREATE_BOX_SCORES,
    "ingestion_log": CREATE_INGESTION_LOG,
    "raw_game_data_pbpstats": CREATE_RAW_GAME_DATA_PBPSTATS,
}

TABLE_DDL = tuple(TABLE_DDL_BY_NAME.values())

VIEW_DDL = (
    CREATE_TEAM_STATS_VIEW,
    CREATE_PLAYERS_VIEW,
    CREATE_GAME_QUALITY_VIEW,
)


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotent: creates any missing canonical tables, leaves existing ones alone."""
    for ddl in TABLE_DDL:
        con.execute(ddl)


def ensure_tables_suffixed(con: duckdb.DuckDBPyConnection, suffix: str) -> None:
    """Create the full operational table set under a name suffix.

    A sandbox run (suffix e.g. ``_new``) must isolate ALL of its operational
    state — not just box_scores. If a sandbox run wrote box scores to
    box_scores_new but marked success in the canonical ingestion_log, a later
    production run would skip-on-retry and never write those games to
    box_scores. So box_scores, schedule, ingestion_log, and raw_game_data_pbpstats
    are all suffixed together. Empty suffix is a no-op alias for ensure_tables.

    Each table reuses its canonical DDL (so suffixed tables keep the same PKs,
    which is what gives INSERT OR REPLACE its conflict target).
    """
    if not suffix:
        ensure_tables(con)
        return
    for name, ddl in TABLE_DDL_BY_NAME.items():
        con.execute(ddl.replace(f"main.{name}", f"main.{name}{suffix}", 1))


def ensure_views(con: duckdb.DuckDBPyConnection, *, db: str) -> None:
    """Always issues CREATE OR REPLACE so the views point at `db`'s tables.

    Necessary because zero-copy clones inherit view DDL verbatim, leaving
    cloned views pointing at the source database (e.g. v3 clone of v2 has
    views that still read from `nba_box_scores_v2`).
    """
    for ddl in VIEW_DDL:
        con.execute(ddl.format(db=db))


def ensure_schema(con: duckdb.DuckDBPyConnection, *, db: str) -> None:
    """Bootstrap tables + repoint views in one call."""
    ensure_tables(con)
    ensure_views(con, db=db)
