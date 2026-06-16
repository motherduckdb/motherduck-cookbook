"""Flight 2: statsbomb-core-transform (in-warehouse SQL).

De-normalizes StatsBomb's possession-normalized coordinates to a fixed pitch and
builds statsbomb.core.* entirely as SQL executed in MotherDuck: no local staging
copy, no per-match Python loop, no row-by-row executemany. Ported 1:1 from
prep_data.py / the old transform_match and validated against match 3869117
(events 3555, dots 44021, mirrored 183, ambiguous 16, keeper fixes 39,
ball-only 381, markers 121).

The transform, all expressed relationally:
  - fixed pitch: home attacks x=120, away flips (x->120-x, y->80-y)
  - freeze-frame mirror correction: actor-vs-event distance decides
    orientation; ambiguous frames fall back to the previous 360-frame's centroid
    via a LAG-style window (reaches one resolved frame back, matching the Python
    running prev_centroid for all non-consecutive-ambiguous frames)
  - keeper goal-anchor fix: a keeper dot within 25u of a goal mouth
    belongs to the team defending it (home x=0, away x=120)
  - b_eff effective-ball chain for off-ball events: carry the last
    on-ball endpoint forward with a window
  - coverage_pct: shoelace area of the 360 visible_area polygon over the pitch

Why SQL: the per-event logic is per-row arithmetic, an array unnest, two window
functions and a couple of aggregates -- exactly what a columnar engine does well.
Doing it in-warehouse removes the 3.4GB raw round-trip and the 30M-row
executemany that made the Python staging version run for over an hour.

Runtime config (env vars):
  MATCH_IDS   optional comma-separated match_id filter (single-match reruns)
  SB_TARGET   optional duckdb path override (local runs/tests); default md:
"""
from __future__ import annotations

import os

import duckdb


# Typed staging view over raw.events: the one place that knows what an event
# looks like relationally. raw stays as-is JSON; marts read this.
STG_EVENTS_DDL = """
CREATE OR REPLACE VIEW core.stg_events AS
SELECT
    match_id,
    record->>'$.id'                                  AS event_id,
    (record->>'$.index')::INT                        AS idx,
    (record->>'$.period')::INT                       AS period,
    record->>'$.timestamp'                           AS event_timestamp,
    (record->>'$.minute')::INT                       AS minute,
    (record->>'$.second')::INT                       AS second,
    record->>'$.type.name'                           AS type,
    (record->>'$.possession')::INT                   AS possession,
    record->>'$.possession_team.name'                AS possession_team,
    record->>'$.play_pattern.name'                   AS play_pattern,
    record->>'$.team.name'                           AS team,
    (record->>'$.player.id')::BIGINT                 AS player_id,
    record->>'$.player.name'                         AS player,
    record->>'$.position.name'                       AS position,
    (record->'$.location'->>0)::DOUBLE               AS location_x,
    (record->'$.location'->>1)::DOUBLE               AS location_y,
    (record->>'$.duration')::DOUBLE                  AS duration,
    coalesce((record->>'$.under_pressure')::BOOLEAN, false) AS under_pressure,
    record->>'$.pass.outcome.name'                   AS pass_outcome,
    record->>'$.shot.outcome.name'                   AS shot_outcome,
    (record->>'$.shot.statsbomb_xg')::DOUBLE         AS shot_xg,
    record
FROM raw.events
"""

INIT_DDL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.events (
    match_id BIGINT, idx INT, period INT, minute INT, second INT,
    type VARCHAR, team VARCHAR, possession_team VARCHAR,
    ob BOOLEAN, goal BOOLEAN, player VARCHAR, pass_recipient VARCHAR,
    pass_outcome VARCHAR, pass_type VARCHAR, pass_height VARCHAR,
    body_part VARCHAR, shot_outcome VARCHAR, shot_end_z DOUBLE,
    b_x DOUBLE, b_y DOUBLE, be_x DOUBLE, be_y DOUBLE,
    beff_x DOUBLE, beff_y DOUBLE);
CREATE TABLE IF NOT EXISTS core.frames (
    match_id BIGINT, idx INT, dot INT, team VARCHAR,
    actor BOOLEAN, keeper BOOLEAN, x DOUBLE, y DOUBLE, nm VARCHAR);
CREATE TABLE IF NOT EXISTS core.frame_meta (
    match_id BIGINT, idx INT, mirrored BOOLEAN, ambiguous BOOLEAN,
    keeper_fixes INT, ball_only BOOLEAN, coverage_pct DOUBLE);
CREATE TABLE IF NOT EXISTS core.markers (
    match_id BIGINT, minute INT, second INT, period INT, kind VARCHAR,
    team VARCHAR, player VARCHAR, label VARCHAR, b_x DOUBLE, b_y DOUBLE);
-- typed projection of raw.matches (raw keeps the verbatim record)
CREATE OR REPLACE TABLE core.matches AS
SELECT
    match_id,
    (record->>'$.competition.competition_id')::INT   AS competition_id,
    record->>'$.competition.competition_name'        AS competition,
    (record->>'$.season.season_id')::INT             AS season_id,
    record->>'$.season.season_name'                  AS season,
    record->>'$.match_date'                          AS match_date,
    record->>'$.competition_stage.name'              AS stage,
    record->>'$.stadium.name'                        AS stadium,
    record->>'$.home_team.home_team_name'            AS home_team,
    record->>'$.away_team.away_team_name'            AS away_team,
    (record->>'$.home_score')::INT                   AS home_score,
    (record->>'$.away_score')::INT                   AS away_score,
    match_id IN (SELECT match_id FROM raw.three_sixty) AS has_360
FROM raw.matches;
""" + STG_EVENTS_DDL


# ---------------------------------------------------------------------------
# Transform SQL. `__MIDS__` is replaced with a subquery selecting the target
# match_ids (the 360-data matches, optionally narrowed for a partial rerun).
# Every JSON extraction is parenthesized: DuckDB binds `->>` looser than AND/=.

# Per-player resolved dots: unnest freeze frames, decide per-frame mirror, apply
# it + the team/keeper fixes. Materialized once so core.frames and core.frame_meta
# (which needs the pre-fix team to count keeper corrections) both read from it.
DOTS_SQL = """
CREATE OR REPLACE TABLE core._dots AS
WITH mt AS (
    SELECT match_id,
           record->>'$.home_team.home_team_name' AS home,
           record->>'$.away_team.away_team_name' AS away
    FROM raw.matches
),
ev AS (
    SELECT t.match_id,
        (e.record->>'$.index')::INT          AS idx,
        (e.record->'$.location'->>0)::DOUBLE AS lx,
        (e.record->'$.location'->>1)::DOUBLE AS ly,
        (e.record->>'$.team.name')           AS team,
        (e.record->>'$.player.name')         AS player_name,
        mt.home, mt.away,
        ((e.record->>'$.team.name') = mt.away) AS flip,
        json_extract(t.record, '$.freeze_frame[*]') AS arr
    FROM raw.three_sixty t
    JOIN raw.events e
      ON e.match_id = t.match_id AND (e.record->>'$.id') = (t.record->>'$.event_uuid')
    JOIN mt ON mt.match_id = t.match_id
    WHERE t.match_id IN (__MIDS__)
      AND (e.record->'$.location') IS NOT NULL
      AND (e.record->>'$.team.name') IS NOT NULL
),
pl AS (
    SELECT match_id, idx, lx, ly, team, player_name, home, away, flip, dot,
        (p->'$.location'->>0)::DOUBLE AS px,
        (p->'$.location'->>1)::DOUBLE AS py,
        (p->>'$.teammate')::BOOLEAN  AS teammate,
        (p->>'$.actor')::BOOLEAN     AS actor,
        (p->>'$.keeper')::BOOLEAN    AS keeper
    FROM (
        SELECT match_id, idx, lx, ly, team, player_name, home, away, flip,
               unnest(arr) AS p, unnest(range(len(arr))) AS dot
        FROM ev
    )
),
frag AS (
    -- per-frame: actor position (raw) + the two candidate display centroids
    SELECT match_id, idx,
        max(px) FILTER (WHERE actor) AS ax,
        max(py) FILTER (WHERE actor) AS ay,
        any_value(lx) AS lx, any_value(ly) AS ly, any_value(flip) AS flip,
        avg(round(CASE WHEN flip THEN 120-px ELSE px END, 1)) AS cen_not_x,
        avg(round(CASE WHEN flip THEN 80-py  ELSE py END, 1)) AS cen_not_y,
        avg(round(CASE WHEN flip THEN px ELSE 120-px END, 1)) AS cen_mir_x,
        avg(round(CASE WHEN flip THEN py ELSE 80-py  END, 1)) AS cen_mir_y
    FROM pl GROUP BY match_id, idx
),
mir AS (
    SELECT match_id, idx, cen_not_x, cen_not_y, cen_mir_x, cen_mir_y,
        sqrt(power(ax-lx, 2) + power(ay-ly, 2))             AS d_same,
        sqrt(power((120-ax)-lx, 2) + power((80-ay)-ly, 2))  AS d_mirr
    FROM frag
),
mir2 AS (
    SELECT *,
        (NOT (d_mirr+5 < d_same) AND NOT (d_same+5 < d_mirr)) AS ambiguous,
        -- resolved mirror for unambiguous frames, NULL for ambiguous ones
        CASE WHEN NOT (NOT (d_mirr+5 < d_same) AND NOT (d_same+5 < d_mirr))
             THEN (d_mirr+5 < d_same) END AS rmir
    FROM mir
),
mir3 AS (
    SELECT *,
        last_value(CASE WHEN rmir IS NOT NULL
                        THEN (CASE WHEN rmir THEN cen_mir_x ELSE cen_not_x END) END
                   IGNORE NULLS) OVER w AS prev_x,
        last_value(CASE WHEN rmir IS NOT NULL
                        THEN (CASE WHEN rmir THEN cen_mir_y ELSE cen_not_y END) END
                   IGNORE NULLS) OVER w AS prev_y
    FROM mir2
    WINDOW w AS (PARTITION BY match_id ORDER BY idx ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
decided AS (
    SELECT match_id, idx, ambiguous,
        CASE WHEN NOT ambiguous THEN rmir
             WHEN prev_x IS NULL THEN false
             ELSE (power(cen_mir_x-prev_x, 2) + power(cen_mir_y-prev_y, 2)
                   < power(cen_not_x-prev_x, 2) + power(cen_not_y-prev_y, 2))
        END AS ff_mirror
    FROM mir3
),
resolved AS (
    SELECT pl.match_id, pl.idx, pl.dot, pl.actor, pl.keeper,
        d.ff_mirror, d.ambiguous, pl.home, pl.away,
        round(CASE WHEN d.ff_mirror THEN (CASE WHEN pl.flip THEN pl.px ELSE 120-pl.px END)
                                    ELSE (CASE WHEN pl.flip THEN 120-pl.px ELSE pl.px END) END, 1) AS x,
        round(CASE WHEN d.ff_mirror THEN (CASE WHEN pl.flip THEN pl.py ELSE 80-pl.py END)
                                    ELSE (CASE WHEN pl.flip THEN 80-pl.py ELSE pl.py END) END, 1) AS y,
        CASE WHEN pl.teammate THEN pl.team
             ELSE (CASE WHEN pl.team = pl.home THEN pl.away ELSE pl.home END) END AS team0,
        CASE WHEN pl.actor THEN pl.player_name END AS nm
    FROM pl JOIN decided d ON d.match_id = pl.match_id AND d.idx = pl.idx
)
SELECT match_id, idx, dot, actor, keeper, ff_mirror, ambiguous, x, y, nm, team0,
    CASE WHEN keeper AND sqrt(power(x, 2)     + power(y-40, 2)) < 25 THEN home
         WHEN keeper AND sqrt(power(x-120, 2) + power(y-40, 2)) < 25 THEN away
         ELSE team0 END AS team
FROM resolved
"""

FRAMES_SQL = """
INSERT INTO core.frames (match_id, idx, dot, team, actor, keeper, x, y, nm)
SELECT match_id, idx, dot, team, actor, keeper, x, y, nm
FROM core._dots
ORDER BY match_id, idx, dot
"""

FRAME_META_SQL = """
INSERT INTO core.frame_meta (match_id, idx, mirrored, ambiguous, keeper_fixes, ball_only, coverage_pct)
WITH base AS (  -- every located event of a 360 match (ball-only frames included)
    SELECT e.match_id, (e.record->>'$.index')::INT AS idx
    FROM raw.events e
    WHERE e.match_id IN (__MIDS__)
      AND (e.record->'$.location') IS NOT NULL
      AND (e.record->>'$.team.name') IS NOT NULL
),
fd AS (
    SELECT match_id, idx,
        any_value(ff_mirror) AS mirrored,
        any_value(ambiguous) AS ambiguous,
        count(*) FILTER (WHERE team <> team0) AS keeper_fixes
    FROM core._dots GROUP BY match_id, idx
),
cov0 AS (
    SELECT t.match_id, (e.record->>'$.index')::INT AS idx,
        json_extract(t.record, '$.visible_area[*]')::DOUBLE[] AS va
    FROM raw.three_sixty t
    JOIN raw.events e
      ON e.match_id = t.match_id AND (e.record->>'$.id') = (t.record->>'$.event_uuid')
    WHERE t.match_id IN (__MIDS__)
      AND (e.record->'$.location') IS NOT NULL
      AND (e.record->>'$.team.name') IS NOT NULL
      AND json_extract(t.record, '$.visible_area[*]') IS NOT NULL
),
coverage AS (  -- shoelace polygon area over the 120x80 (=9600) pitch, as a %
    SELECT match_id, idx,
        CASE WHEN (len(va)//2) >= 3 THEN round(abs(list_sum(list_transform(
            range(0, len(va)//2),
            k -> va[2*k+1] * va[2*((k+1)%(len(va)//2))+2]
               - va[2*((k+1)%(len(va)//2))+1] * va[2*k+2]
        ))) / 2 / 9600 * 100, 1) ELSE 0.0 END AS coverage_pct
    FROM cov0
)
SELECT b.match_id, b.idx,
    coalesce(fd.mirrored, false)    AS mirrored,
    coalesce(fd.ambiguous, false)   AS ambiguous,
    coalesce(fd.keeper_fixes, 0)    AS keeper_fixes,
    (fd.match_id IS NULL)           AS ball_only,
    coverage.coverage_pct
FROM base b
LEFT JOIN fd       ON fd.match_id = b.match_id AND fd.idx = b.idx
LEFT JOIN coverage ON coverage.match_id = b.match_id AND coverage.idx = b.idx
"""

EVENTS_SQL = """
INSERT INTO core.events (match_id, idx, period, minute, second, type, team, possession_team,
    ob, goal, player, pass_recipient, pass_outcome, pass_type, pass_height, body_part,
    shot_outcome, shot_end_z, b_x, b_y, be_x, be_y, beff_x, beff_y)
WITH mt AS (
    SELECT match_id, record->>'$.away_team.away_team_name' AS away FROM raw.matches
),
src AS (
    SELECT e.match_id,
        (e.record->>'$.index')::INT  AS idx,
        (e.record->>'$.period')::INT AS period,
        (e.record->>'$.minute')::INT AS minute,
        (e.record->>'$.second')::INT AS second,
        (e.record->>'$.type.name')             AS type,
        (e.record->>'$.team.name')             AS team,
        (e.record->>'$.possession_team.name')  AS possession_team,
        (e.record->>'$.player.name')           AS player,
        (e.record->>'$.pass.recipient.name')   AS pass_recipient,
        (e.record->'$.location'->>0)::DOUBLE   AS lx,
        (e.record->'$.location'->>1)::DOUBLE   AS ly,
        mt.away, e.record AS r
    FROM raw.events e JOIN mt ON mt.match_id = e.match_id
    WHERE e.match_id IN (__MIDS__)
      AND (e.record->'$.location') IS NOT NULL
      AND (e.record->>'$.team.name') IS NOT NULL
),
calc AS (
    SELECT match_id, idx, period, minute, second, type, team, possession_team, player, pass_recipient,
        (team = away)         AS flip,
        (type = 'Pressure')   AS ob,
        (type = 'Shot' AND (r->>'$.shot.outcome.name') = 'Goal') AS goal,
        CASE WHEN type='Pass' THEN (r->>'$.pass.outcome.name') END AS pass_outcome,
        CASE WHEN type='Pass' THEN (r->>'$.pass.type.name')    END AS pass_type,
        CASE WHEN type='Pass' THEN (r->>'$.pass.height.name')  END AS pass_height,
        CASE WHEN type='Pass' THEN (r->>'$.pass.body_part.name')
             WHEN type='Shot' THEN (r->>'$.shot.body_part.name') END AS body_part,
        CASE WHEN type='Shot' THEN (r->>'$.shot.outcome.name') END AS shot_outcome,
        CASE WHEN type='Shot' AND json_array_length(r->'$.shot.end_location') > 2
             THEN round((r->'$.shot.end_location'->>2)::DOUBLE, 1) END AS shot_end_z,
        lx, ly,
        CASE type WHEN 'Pass'  THEN (r->'$.pass.end_location'->>0)::DOUBLE
                  WHEN 'Carry' THEN (r->'$.carry.end_location'->>0)::DOUBLE
                  WHEN 'Shot'  THEN (r->'$.shot.end_location'->>0)::DOUBLE END AS ex,
        CASE type WHEN 'Pass'  THEN (r->'$.pass.end_location'->>1)::DOUBLE
                  WHEN 'Carry' THEN (r->'$.carry.end_location'->>1)::DOUBLE
                  WHEN 'Shot'  THEN (r->'$.shot.end_location'->>1)::DOUBLE END AS ey
    FROM src
),
disp AS (
    SELECT match_id, idx, period, minute, second, type, team, possession_team, ob, goal, player,
        pass_recipient, pass_outcome, pass_type, pass_height, body_part, shot_outcome, shot_end_z,
        round(CASE WHEN flip THEN 120-lx ELSE lx END, 1) AS b_x,
        round(CASE WHEN flip THEN 80-ly  ELSE ly END, 1) AS b_y,
        CASE WHEN ex IS NOT NULL THEN round(CASE WHEN flip THEN 120-ex ELSE ex END, 1) END AS be_x,
        CASE WHEN ey IS NOT NULL THEN round(CASE WHEN flip THEN 80-ey  ELSE ey END, 1) END AS be_y
    FROM calc
)
-- beff: off-ball events show the last on-ball endpoint. For an
-- on-ball frame the "anchor" is coalesce(be, b); pressure frames carry it forward.
SELECT match_id, idx, period, minute, second, type, team, possession_team, ob, goal, player,
    pass_recipient, pass_outcome, pass_type, pass_height, body_part, shot_outcome, shot_end_z,
    b_x, b_y, be_x, be_y,
    CASE WHEN ob THEN last_value(CASE WHEN NOT ob THEN coalesce(be_x, b_x) END IGNORE NULLS) OVER w END AS beff_x,
    CASE WHEN ob THEN last_value(CASE WHEN NOT ob THEN coalesce(be_y, b_y) END IGNORE NULLS) OVER w END AS beff_y
FROM disp
WINDOW w AS (PARTITION BY match_id ORDER BY idx ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
"""

MARKERS_SQL = """
INSERT INTO core.markers (match_id, minute, second, period, kind, team, player, label, b_x, b_y)
WITH mt AS (
    SELECT match_id, record->>'$.away_team.away_team_name' AS away FROM raw.matches
),
src AS (
    SELECT e.match_id,
        (e.record->>'$.minute')::INT AS minute,
        (e.record->>'$.second')::INT AS second,
        (e.record->>'$.period')::INT AS period,
        (e.record->>'$.type.name')   AS type,
        (e.record->>'$.team.name')   AS team,
        (e.record->>'$.player.name') AS player,
        (e.record->'$.location'->>0)::DOUBLE AS lx,
        (e.record->'$.location'->>1)::DOUBLE AS ly,
        ((e.record->>'$.team.name') = mt.away) AS flip,
        (e.record->>'$.shot.outcome.name')             AS shot_outcome,
        (e.record->>'$.shot.type.name')                AS shot_type,
        (e.record->>'$.foul_committed.card.name')      AS foul_card,
        (e.record->>'$.bad_behaviour.card.name')       AS bad_card,
        (e.record->>'$.pass.type.name')                AS pass_type,
        (e.record->>'$.substitution.replacement.name') AS sub_repl
    FROM raw.events e JOIN mt ON mt.match_id = e.match_id
    WHERE e.match_id IN (__MIDS__)
),
m AS (
    SELECT match_id, minute, second, period, team, player, lx, ly, flip,
        CASE
            WHEN type='Shot' AND shot_outcome='Goal' AND shot_type='Penalty' THEN 'penalty_goal'
            WHEN type='Shot' AND shot_outcome='Goal' THEN 'goal'
            WHEN type='Shot' THEN 'shot'
            WHEN type='Own Goal For' THEN 'goal'
            WHEN type='Offside' THEN 'offside'
            WHEN type='Substitution' THEN 'sub'
            WHEN type='Foul Committed' AND foul_card LIKE '%Yellow%' THEN 'yellow'
            WHEN type='Foul Committed' AND foul_card LIKE '%Red%'    THEN 'red'
            WHEN type='Foul Committed' THEN 'foul'
            WHEN type='Bad Behaviour' AND bad_card LIKE '%Yellow%' THEN 'yellow'
            WHEN type='Bad Behaviour' AND bad_card LIKE '%Red%'    THEN 'red'
            WHEN type='Pass' AND pass_type='Throw-in'  THEN 'throw_in'
            WHEN type='Pass' AND pass_type='Corner'    THEN 'corner'
            WHEN type='Pass' AND pass_type='Free Kick' THEN 'free_kick'
        END AS kind,
        CASE
            WHEN type='Shot' AND shot_outcome='Goal' THEN 'GOAL · ' || coalesce(player, '')
            WHEN type='Shot' THEN 'Shot · ' || coalesce(shot_outcome, '')
            WHEN type='Own Goal For' THEN 'GOAL (own goal)'
            WHEN type='Offside' THEN 'Offside'
            WHEN type='Substitution' THEN 'Sub · ' || coalesce(player, '') || ' ➜ ' || coalesce(sub_repl, '')
            WHEN type='Foul Committed' AND foul_card LIKE '%Yellow%' THEN 'Yellow · ' || coalesce(player, '')
            WHEN type='Foul Committed' AND foul_card LIKE '%Red%'    THEN 'Red · ' || coalesce(player, '')
            WHEN type='Foul Committed' THEN 'Foul'
            WHEN type='Bad Behaviour' AND bad_card LIKE '%Yellow%' THEN 'Yellow card'
            WHEN type='Bad Behaviour' AND bad_card LIKE '%Red%'    THEN 'Red card'
            WHEN type='Pass' AND pass_type='Throw-in'  THEN 'Throw-in'
            WHEN type='Pass' AND pass_type='Corner'    THEN 'Corner'
            WHEN type='Pass' AND pass_type='Free Kick' THEN 'Free kick'
        END AS label
    FROM src
)
SELECT match_id, minute, second, period, kind, team, player, label,
    CASE WHEN lx IS NOT NULL THEN round(CASE WHEN flip THEN 120-lx ELSE lx END, 1) END AS b_x,
    CASE WHEN ly IS NOT NULL THEN round(CASE WHEN flip THEN 80-ly  ELSE ly END, 1) END AS b_y
FROM m WHERE kind IS NOT NULL
"""


def run(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(INIT_DDL)

    ids = os.environ.get("MATCH_IDS")
    tables = ("events", "frames", "frame_meta", "markers")
    if ids:
        # Partial rerun: rebuild only the listed matches, leave the rest intact.
        wanted = ",".join(str(int(m)) for m in ids.split(","))
        mids = f"SELECT DISTINCT match_id FROM raw.three_sixty WHERE match_id IN ({wanted})"
        for t in tables:
            con.execute(f"DELETE FROM core.{t} WHERE match_id IN ({wanted})")
    else:
        # Full run: wipe the per-match tables so a rebuild is idempotent.
        mids = "SELECT DISTINCT match_id FROM raw.three_sixty"
        for t in tables:
            con.execute(f"DELETE FROM core.{t}")

    def sql(template: str) -> str:
        return template.replace("__MIDS__", mids)

    con.execute(sql(DOTS_SQL))
    print("built core._dots (resolved player dots)", flush=True)
    con.execute(FRAMES_SQL)
    print("inserted core.frames", flush=True)
    con.execute(sql(FRAME_META_SQL))
    print("inserted core.frame_meta", flush=True)
    con.execute(sql(EVENTS_SQL))
    print("inserted core.events", flush=True)
    con.execute(sql(MARKERS_SQL))
    print("inserted core.markers", flush=True)
    con.execute("DROP TABLE IF EXISTS core._dots")


def main() -> None:
    target = os.environ.get("SB_TARGET", "md:")
    con = duckdb.connect(target)
    if target == "md:":
        con.execute("USE statsbomb")
    run(con)
    con.close()
    print("done", flush=True)


if __name__ == "__main__":
    main()
