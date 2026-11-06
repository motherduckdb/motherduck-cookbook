-- Optimization results: one database per organization, many campaigns.
-- Applied by `record_results.py setup` inside the results database (default: optimization_results).
-- Rows are owned by a campaign: `record_results.py load` replaces every row of a campaign at once.
-- All timestamps are UTC.

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id       VARCHAR NOT NULL,
    title             VARCHAR,
    goal              VARCHAR,
    workload_kind     VARCHAR,   -- query | query_group | dbt
    workload_ref      VARCHAR,   -- project path/revision or query name; no secrets
    source_snapshot   VARCHAR,   -- pinned share / snapshot the campaign reads
    region            VARCHAR,   -- SELECT region FROM md_user_info()
    headline_subject  VARCHAR,   -- subject the campaign is judged on, e.g. 'dbt build (selected models)'
    headline_size     VARCHAR,   -- Duckling size of the headline comparison; NULL = any
    headline_threads  INTEGER,   -- threads of the headline comparison; NULL = any
    noise_pct         DOUBLE,    -- |delta| below this is reported as neutral (default 5)
    status            VARCHAR,   -- running | complete | abandoned
    agent             VARCHAR,   -- model / harness that ran the loop
    envelope          JSON,      -- compute envelope: sizes, slots, threads
    created_at        TIMESTAMP,
    updated_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS experiments (
    campaign_id       VARCHAR NOT NULL,
    experiment_id     VARCHAR NOT NULL,
    title             VARCHAR,
    hypothesis        VARCHAR,   -- observed bottleneck and expected plan change
    change_kind       VARCHAR,   -- sql_rewrite | materialization | incremental | config | duckling_size | threads | baseline | other
    change_summary    VARCHAR,   -- what the candidate changes, in one or two sentences
    targets           VARCHAR[], -- models / queries touched
    status            VARCHAR,   -- planned | running | complete | failed | abandoned
    decision          VARCHAR,   -- accepted | rejected | inconclusive | informational | pending
    reason            VARCHAR,
    validation_status VARCHAR,   -- passed | failed | inconclusive | not_run
    diff_path         VARCHAR,   -- relative path of the candidate diff in the ledger
    diff_text         VARCHAR,   -- the diff itself (loaded from diff_path unless --no-diffs)
    baseline_experiment_id VARCHAR, -- compare against this experiment's runs when this one measured no 'baseline'
    baseline_variant  VARCHAR,   -- ... of this variant (default 'candidate')
    created_at        TIMESTAMP,
    updated_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    campaign_id       VARCHAR NOT NULL,
    experiment_id     VARCHAR,
    run_id            VARCHAR NOT NULL,
    variant           VARCHAR,   -- original (unmodified workload) | baseline (this experiment's reference) | candidate | other candidate labels
    label             VARCHAR,
    subject           VARCHAR,   -- what was measured: a query, a model, or a pipeline/job
    scope             VARCHAR,   -- query | model | pipeline
    duckling_size     VARCHAR,   -- pulse | standard | jumbo | mega | giga
    threads           INTEGER,   -- dbt threads / client concurrency
    repetition        INTEGER,
    purpose           VARCHAR,   -- measure | warmup | profile (EXPLAIN ANALYZE); only 'measure' counts as a timing
    status            VARCHAR,   -- succeeded | failed | oom | timeout | cancelled | invalid
    triggered_at      TIMESTAMP, -- when the agent started the run (includes queueing / startup)
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    wall_s            DOUBLE,    -- client / job wall time: the number being optimized
    server_s          DOUBLE,    -- query_history execution time
    wait_s            DOUBLE,    -- query_history wait time
    spill_bytes       BIGINT,
    rows_out          BIGINT,
    query_ids         VARCHAR[],
    service_account   VARCHAR,   -- experiment slot user name; never a token
    databases         VARCHAR[], -- databases / clones / shares the run read or wrote
    command           VARCHAR,   -- exact command or SQL entry point, secrets redacted
    error             VARCHAR,
    notes             VARCHAR
);

CREATE TABLE IF NOT EXISTS run_steps (
    campaign_id       VARCHAR NOT NULL,
    run_id            VARCHAR NOT NULL,
    step_name         VARCHAR NOT NULL,  -- model, query or phase name
    step_kind         VARCHAR,           -- model | test | seed | snapshot | query | phase
    status            VARCHAR,
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    duration_s        DOUBLE,
    rows_out          BIGINT,
    spill_bytes       BIGINT,
    query_ids         VARCHAR[]  -- lets the loader fill spill_bytes from query_history
);

CREATE TABLE IF NOT EXISTS agent_events (
    campaign_id       VARCHAR NOT NULL,
    event_id          VARCHAR NOT NULL,
    experiment_id     VARCHAR,
    run_id            VARCHAR,
    kind              VARCHAR,   -- plan | provision | warmup | profile | run | validate | analyze | decide | wait | cleanup | report | error
    summary           VARCHAR,
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP
);

-- Columns added after the first release: lets `setup` upgrade an existing results database.
ALTER TABLE campaigns   ADD COLUMN IF NOT EXISTS source_snapshot VARCHAR;
ALTER TABLE experiments ADD COLUMN IF NOT EXISTS diff_text VARCHAR;
ALTER TABLE runs        ADD COLUMN IF NOT EXISTS service_account VARCHAR;
ALTER TABLE runs        ADD COLUMN IF NOT EXISTS databases VARCHAR[];
ALTER TABLE runs        ADD COLUMN IF NOT EXISTS command VARCHAR;
ALTER TABLE run_steps   ADD COLUMN IF NOT EXISTS query_ids VARCHAR[];

-- Median per measured configuration (subject x Duckling size x threads x variant).
-- Warm-ups, profiles and unsuccessful runs are excluded.
CREATE OR REPLACE VIEW v_run_stats AS
SELECT
    campaign_id, experiment_id, subject, coalesce(scope, 'pipeline') AS scope, duckling_size,
    coalesce(threads, 0) AS threads, variant,
    count(*)            AS samples,
    median(wall_s)      AS median_s,
    min(wall_s)         AS min_s,
    max(wall_s)         AS max_s,
    median(spill_bytes) AS median_spill_bytes
FROM runs
WHERE coalesce(purpose, 'measure') = 'measure' AND status = 'succeeded' AND wall_s IS NOT NULL
GROUP BY ALL;

-- Every candidate configuration against two references of the same subject, size and threads:
--   baseline_s  the experiment's reference: its own 'baseline' runs, else the referenced experiment's
--               variant (experiments.baseline_experiment_id), else the original workload
--   original_s  the unmodified workload: this experiment's 'original' runs, else the campaign's
-- delta_pct says what the experiment's change did; vs_original_pct is the cumulative gain.
CREATE OR REPLACE VIEW v_comparisons AS
WITH orig_campaign AS (
    SELECT campaign_id, subject, duckling_size, coalesce(threads, 0) AS threads,
           median(wall_s) AS median_s, median(spill_bytes) AS median_spill_bytes
    FROM runs
    WHERE variant = 'original' AND coalesce(purpose, 'measure') = 'measure' AND status = 'succeeded' AND wall_s IS NOT NULL
    GROUP BY ALL
), stats AS (SELECT * FROM v_run_stats),
joined AS (
    SELECT
        c.campaign_id, c.experiment_id, c.subject, c.scope, c.duckling_size, c.threads, c.variant,
        c.median_s AS candidate_s, c.samples AS candidate_samples, c.median_spill_bytes AS candidate_spill_bytes,
        coalesce(own.median_s, ref.median_s, oe.median_s, oc.median_s) AS baseline_s,
        coalesce(own.median_spill_bytes, ref.median_spill_bytes, oe.median_spill_bytes, oc.median_spill_bytes) AS baseline_spill_bytes,
        CASE WHEN own.median_s IS NOT NULL THEN 'experiment baseline'
             WHEN ref.median_s IS NOT NULL THEN e.baseline_experiment_id || ' ' || coalesce(e.baseline_variant, 'candidate')
             WHEN oe.median_s IS NOT NULL OR oc.median_s IS NOT NULL THEN 'original' END AS baseline_source,
        coalesce(oe.median_s, oc.median_s) AS original_s,
        coalesce(k.noise_pct, 5) AS noise_pct
    FROM stats c
    JOIN campaigns k USING (campaign_id)
    LEFT JOIN experiments e USING (campaign_id, experiment_id)
    LEFT JOIN stats own ON own.campaign_id = c.campaign_id AND own.experiment_id = c.experiment_id AND own.variant = 'baseline'
         AND own.subject = c.subject AND own.duckling_size = c.duckling_size AND own.threads = c.threads
    LEFT JOIN stats ref ON ref.campaign_id = c.campaign_id AND ref.experiment_id = e.baseline_experiment_id
         AND ref.variant = coalesce(e.baseline_variant, 'candidate')
         AND ref.subject = c.subject AND ref.duckling_size = c.duckling_size AND ref.threads = c.threads
    LEFT JOIN stats oe ON oe.campaign_id = c.campaign_id AND oe.experiment_id = c.experiment_id AND oe.variant = 'original'
         AND oe.subject = c.subject AND oe.duckling_size = c.duckling_size AND oe.threads = c.threads
    LEFT JOIN orig_campaign oc ON oc.campaign_id = c.campaign_id
         AND oc.subject = c.subject AND oc.duckling_size = c.duckling_size AND oc.threads = c.threads
    WHERE c.variant NOT IN ('original', 'baseline')
)
SELECT *,
    candidate_s - baseline_s                                      AS delta_s,
    100.0 * (candidate_s - baseline_s) / nullif(baseline_s, 0)    AS delta_pct,
    100.0 * (candidate_s - original_s) / nullif(original_s, 0)    AS vs_original_pct,
    CASE
        WHEN baseline_s IS NULL THEN 'no_baseline'
        WHEN 100.0 * (candidate_s - baseline_s) / nullif(baseline_s, 0) <= -noise_pct THEN 'improved'
        WHEN 100.0 * (candidate_s - baseline_s) / nullif(baseline_s, 0) >=  noise_pct THEN 'regressed'
        ELSE 'neutral'
    END                                                           AS effect
FROM joined;

-- One row per experiment: its best comparison (pipeline-level results first), run counts and compute time.
CREATE OR REPLACE VIEW v_experiment_summary AS
WITH best AS (
    SELECT * FROM v_comparisons WHERE effect <> 'no_baseline'
    QUALIFY row_number() OVER (PARTITION BY campaign_id, experiment_id
                               ORDER BY scope = 'pipeline' DESC, delta_pct) = 1
), r AS (
    SELECT campaign_id, experiment_id,
           count(*)                                                  AS runs,
           count(*) FILTER (WHERE status <> 'succeeded')             AS failed_runs,
           sum(coalesce(wall_s, epoch(finished_at - started_at)))    AS compute_s,
           min(coalesce(triggered_at, started_at))                   AS first_run_at,
           max(finished_at)                                          AS last_run_at,
           list(DISTINCT duckling_size ORDER BY duckling_size) FILTER (WHERE duckling_size IS NOT NULL) AS sizes
    FROM runs GROUP BY ALL
)
SELECT e.campaign_id, e.experiment_id, e.title, e.change_kind, e.change_summary, e.hypothesis,
       e.status, e.decision, e.reason, e.validation_status, e.targets, e.created_at, e.updated_at,
       coalesce(r.runs, 0) AS runs, coalesce(r.failed_runs, 0) AS failed_runs, coalesce(r.compute_s, 0) AS compute_s,
       r.first_run_at, r.last_run_at, r.sizes,
       b.subject AS best_subject, b.scope AS best_scope, b.duckling_size AS best_size, b.threads AS best_threads, b.variant AS best_variant,
       b.baseline_s, b.baseline_source, b.candidate_s, b.delta_s, b.delta_pct, b.original_s, b.vs_original_pct, b.effect
FROM experiments e
LEFT JOIN r USING (campaign_id, experiment_id)
LEFT JOIN best b USING (campaign_id, experiment_id);

-- Headline: the best accepted result against the original workload on the campaign's headline subject.
CREATE OR REPLACE VIEW v_headline AS
SELECT c.*, e.title AS experiment_title
FROM v_comparisons c
JOIN campaigns k USING (campaign_id)
JOIN experiments e USING (campaign_id, experiment_id)
WHERE c.original_s IS NOT NULL
  AND e.decision = 'accepted'
  AND (k.headline_subject IS NULL OR c.subject = k.headline_subject)
  AND (k.headline_size IS NULL OR c.duckling_size = k.headline_size)
  AND (k.headline_threads IS NULL OR c.threads = k.headline_threads)
QUALIFY row_number() OVER (PARTITION BY c.campaign_id ORDER BY c.vs_original_pct) = 1;

CREATE OR REPLACE VIEW v_campaign_summary AS
WITH r AS (
    SELECT campaign_id, count(*) AS runs,
           count(*) FILTER (WHERE status <> 'succeeded') AS failed_runs,
           sum(coalesce(wall_s, epoch(finished_at - started_at))) AS compute_s,
           min(coalesce(triggered_at, started_at)) AS first_at, max(finished_at) AS last_at,
           list(DISTINCT duckling_size ORDER BY duckling_size) FILTER (WHERE duckling_size IS NOT NULL) AS sizes
    FROM runs GROUP BY ALL
), e AS (
    SELECT campaign_id, count(*) AS experiments,
           count(*) FILTER (WHERE decision = 'accepted') AS accepted,
           count(*) FILTER (WHERE decision = 'rejected') AS rejected,
           count(*) FILTER (WHERE decision NOT IN ('accepted', 'rejected')) AS other_decisions
    FROM experiments GROUP BY ALL
), ev AS (
    SELECT campaign_id, min(started_at) AS first_at, max(coalesce(finished_at, started_at)) AS last_at
    FROM agent_events GROUP BY ALL
)
SELECT k.*, coalesce(e.experiments, 0) AS experiments, coalesce(e.accepted, 0) AS accepted,
       coalesce(e.rejected, 0) AS rejected, coalesce(e.other_decisions, 0) AS other_decisions,
       coalesce(r.runs, 0) AS runs, coalesce(r.failed_runs, 0) AS failed_runs, coalesce(r.compute_s, 0) AS compute_s,
       r.sizes,
       least(r.first_at, ev.first_at) AS started_at, greatest(r.last_at, ev.last_at) AS finished_at,
       h.subject AS headline_subject_measured, h.duckling_size AS headline_size_measured, h.threads AS headline_threads_measured,
       h.original_s AS headline_original_s, h.candidate_s AS headline_best_s, h.vs_original_pct AS headline_pct,
       h.experiment_id AS headline_experiment_id, h.experiment_title AS headline_experiment
FROM campaigns k
LEFT JOIN e USING (campaign_id)
LEFT JOIN r USING (campaign_id)
LEFT JOIN ev USING (campaign_id)
LEFT JOIN v_headline h USING (campaign_id);

-- Best configuration per subject: candidates that are safe to adopt (accepted, or validated
-- informational runs such as size/thread sweeps) and not slower than their reference, ranked by
-- median time. rank = 1 is the fastest on any Duckling size; size_rank = 1 the fastest per size.
CREATE OR REPLACE VIEW v_leaderboard AS
SELECT c.*, e.title AS experiment_title, e.decision,
       row_number() OVER (PARTITION BY c.campaign_id, c.subject ORDER BY c.candidate_s, c.experiment_id) AS rank,
       row_number() OVER (PARTITION BY c.campaign_id, c.subject, c.duckling_size ORDER BY c.candidate_s, c.experiment_id) AS size_rank
FROM v_comparisons c
JOIN experiments e USING (campaign_id, experiment_id)
WHERE c.effect <> 'regressed'
  AND (e.decision = 'accepted' OR (e.decision = 'informational' AND e.validation_status = 'passed'));
