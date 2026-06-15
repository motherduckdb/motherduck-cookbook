"""Flight 3: statsbomb-marts.

Ordered CREATE OR REPLACE statements over core.* (incl. the stg_events view).
Pure SQL: the fast-iteration layer (dbt-lite). Add new marts at the end of
STATEMENTS.

Runtime config (env vars):
  SB_TARGET   optional duckdb path override (local runs/tests); default md:
"""
from __future__ import annotations

import os

import duckdb

STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS marts",

    # ---- replay feed: the SQL equivalent of match_data.json -------------------
    """
    CREATE OR REPLACE TABLE marts.replay_events AS
    SELECT e.*, m.home_team, m.away_team
    FROM core.events e JOIN core.matches m USING (match_id)
    ORDER BY match_id, idx
    """,
    """
    CREATE OR REPLACE TABLE marts.replay_dots AS
    SELECT * FROM core.frames ORDER BY match_id, idx, dot
    """,
    """
    CREATE OR REPLACE TABLE marts.replay_markers AS
    SELECT * FROM core.markers ORDER BY match_id, minute, second
    """,

    # ---- pass/shot flights for the passes view --------------------------------
    """
    CREATE OR REPLACE TABLE marts.pass_flights AS
    SELECT match_id, idx, period, minute, second, type, team, player,
           pass_recipient, pass_outcome, pass_type, pass_height, body_part,
           shot_outcome, shot_end_z, b_x, b_y, be_x, be_y
    FROM core.events
    WHERE type IN ('Pass', 'Shot') AND be_x IS NOT NULL
    ORDER BY match_id, idx
    """,

    # ---- per match x team statistics (corpus-wide, from stg_events) -----------
    """
    CREATE OR REPLACE TABLE marts.match_stats AS
    WITH ev AS (
        SELECT match_id, team, type, location_x,
               pass_outcome, shot_outcome,
               coalesce(shot_xg, 0) AS xg
        FROM core.stg_events
    ),
    per_team AS (
        SELECT match_id, team,
            count(*) FILTER (WHERE type = 'Shot')                                  AS shots,
            -- StatsBomb on-target outcomes: Goal, Saved, Saved to Post; lower() guards vocabulary casing
            count(*) FILTER (WHERE type = 'Shot' AND lower(shot_outcome) IN ('goal', 'saved', 'saved to post')) AS shots_on_target,
            count(*) FILTER (WHERE type = 'Shot' AND shot_outcome = 'Goal')        AS goals_shot,
            count(*) FILTER (WHERE type = 'Own Goal For')                          AS goals_own,
            sum(xg) FILTER (WHERE type = 'Shot')                                   AS xg,
            count(*) FILTER (WHERE type = 'Pass')                                  AS passes,
            count(*) FILTER (WHERE type = 'Pass' AND pass_outcome IS NULL)         AS passes_completed,
            -- coords are possession-normalized, so location_x >= 80 is the acting team's
            -- ATTACKING final third: exactly what field tilt needs
            count(*) FILTER (WHERE type = 'Pass' AND location_x >= 80)             AS final_third_passes,
            count(*) FILTER (WHERE type = 'Pressure')                              AS pressures,
            -- in the pressing team's possession-normalized coords, x >= 48 is the forward 60% of the pitch
            count(*) FILTER (WHERE type IN ('Pressure', 'Duel', 'Interception', 'Foul Committed')
                             AND location_x >= 48)                                 AS press_zone_actions,
            -- own build-up passes (x<=72 in the passer's view); the opponent's
            -- value of this feeds PPDA below
            count(*) FILTER (WHERE type = 'Pass' AND location_x <= 72)             AS buildup_passes
        FROM ev GROUP BY match_id, team
    )
    SELECT
        t.match_id, t.team,
        m.competition, m.season, m.stage, m.match_date,
        t.team = m.home_team                                              AS is_home,
        t.goals_shot + t.goals_own                                        AS goals,
        round(t.xg, 2)                                                    AS xg,
        t.shots, t.shots_on_target, t.passes,
        round(100.0 * t.passes_completed / nullif(t.passes, 0), 1)        AS pass_pct,
        -- pass-share proxy for possession; matches public records well (this fixture: USA 58.5 vs FotMob 58)
        round(100.0 * t.passes / nullif(t.passes + o.passes, 0), 1)       AS possession_pct,
        round(100.0 * t.final_third_passes
              / nullif(t.final_third_passes + o.final_third_passes, 0), 1) AS field_tilt_pct,
        t.pressures,
        round(o.buildup_passes / nullif(t.press_zone_actions, 0), 1)      AS ppda
    FROM per_team t
    JOIN per_team o ON o.match_id = t.match_id AND o.team <> t.team
    JOIN core.matches m ON m.match_id = t.match_id
    ORDER BY t.match_id, is_home DESC
    """,

    # ---- rebuilt 360 spatial metrics (the paid statsbombpy columns) -----------
    """
    CREATE OR REPLACE TABLE marts.spatial_metrics AS
    -- events without 360 dots (ball-only, ~11%) have no rows here by design;
    -- LEFT JOIN from core.events when full coverage is needed
    WITH actor AS (
        SELECT match_id, idx, team, x, y FROM core.frames WHERE actor
    ),
    dots AS (
        SELECT f.match_id, f.idx, f.team, f.x, f.y, f.keeper,
               a.team AS actor_team, a.x AS ax, a.y AS ay,
               -- +1 when the actor's team attacks right (home), -1 otherwise
               CASE WHEN a.team = m.home_team THEN 1 ELSE -1 END AS dir
        FROM core.frames f
        JOIN actor a USING (match_id, idx)
        JOIN core.matches m USING (match_id)
        WHERE NOT f.actor
    ),
    agg AS (
        SELECT match_id, idx,
            min(sqrt(power(x - ax, 2) + power(y - ay, 2)))
                FILTER (WHERE team <> actor_team)                     AS nearest_defender_dist,
            count(*) FILTER (WHERE team <> actor_team AND dir * (x - ax) > 0)
                                                                      AS defenders_goal_side,
            count(*) FILTER (WHERE team = actor_team)                 AS visible_teammates,
            count(*) FILTER (WHERE team <> actor_team)                AS visible_opponents
        FROM dots GROUP BY match_id, idx
    ),
    bypassed AS (
        -- opponents whose x lies between a completed pass's start and end,
        -- along the passer's attack direction: our line-breaking approximation
        SELECT d.match_id, d.idx,
               count(*) FILTER (
                   WHERE d.team <> d.actor_team
                   AND d.dir * d.x > d.dir * e.b_x
                   AND d.dir * d.x < d.dir * e.be_x
               ) AS opponents_bypassed
        FROM dots d
        JOIN core.events e USING (match_id, idx)
        WHERE e.type = 'Pass' AND e.pass_outcome IS NULL AND e.be_x IS NOT NULL
        GROUP BY d.match_id, d.idx
    )
    SELECT e.match_id, e.idx, e.type, e.team, e.player, e.period, e.minute, e.second,
           round(a.nearest_defender_dist, 1) AS nearest_defender_dist,
           a.defenders_goal_side, a.visible_teammates, a.visible_opponents,
           b.opponents_bypassed,
           coalesce(b.opponents_bypassed >= 3, false) AS line_breaking_pass,
           fm.coverage_pct
    FROM agg a
    JOIN core.events e USING (match_id, idx)
    LEFT JOIN bypassed b USING (match_id, idx)
    LEFT JOIN core.frame_meta fm USING (match_id, idx)
    ORDER BY match_id, idx
    """,

    # ---- the data-quality receipts, queryable -------------------------------------
    """
    CREATE OR REPLACE TABLE marts.frame_quality AS
    SELECT match_id,
           count(*)                                  AS frames,
           count(*) FILTER (WHERE mirrored)          AS frames_mirrored,
           count(*) FILTER (WHERE ambiguous)         AS frames_ambiguous,
           count(*) FILTER (WHERE ball_only)         AS frames_ball_only,
           sum(keeper_fixes)                         AS keeper_fixes,
           round(avg(coverage_pct), 1)               AS avg_coverage_pct
    FROM core.frame_meta
    GROUP BY match_id
    ORDER BY match_id
    """,
]


def run(con: duckdb.DuckDBPyConnection) -> None:
    for i, sql in enumerate(STATEMENTS, 1):
        con.execute(sql)
        print(f"statement {i}/{len(STATEMENTS)} ok")


def main() -> None:
    target = os.environ.get("SB_TARGET", "md:")
    con = duckdb.connect(target)
    if target == "md:":
        con.execute("USE statsbomb")
    run(con)
    print("done")


if __name__ == "__main__":
    main()
