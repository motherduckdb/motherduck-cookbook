-- Run on the connection that can read BOTH stored result tables.
-- SET VARIABLE baseline_relation = 'experiment.main.baseline_result';
-- SET VARIABLE candidate_relation = 'experiment.main.candidate_result';
-- Use recorded, explicit normalization projections first for floating columns.

-- Gate 1: column names, order, and types. A successful comparison returns 0.
WITH baseline_schema AS (
    SELECT ordinal: row_number() OVER (), column_name, column_type
    FROM (DESCRIBE SELECT * FROM query_table(getvariable('baseline_relation')))
), candidate_schema AS (
    SELECT ordinal: row_number() OVER (), column_name, column_type
    FROM (DESCRIBE SELECT * FROM query_table(getvariable('candidate_relation')))
), differences AS (
    (SELECT * FROM baseline_schema EXCEPT ALL SELECT * FROM candidate_schema)
    UNION ALL
    (SELECT * FROM candidate_schema EXCEPT ALL SELECT * FROM baseline_schema)
)
SELECT schema_differences: count(*) FROM differences;

-- Gate 2: row counts, including duplicates.
SELECT
    baseline_rows: (SELECT count(*) FROM query_table(getvariable('baseline_relation'))),
    candidate_rows: (SELECT count(*) FROM query_table(getvariable('candidate_relation')));

-- Gate 3: execute only after Gate 1 is zero and row counts agree.
-- These queries preserve duplicate-row multiplicity.
SELECT baseline_minus_candidate: count(*)
FROM (
    SELECT * FROM query_table(getvariable('baseline_relation'))
    EXCEPT ALL
    SELECT * FROM query_table(getvariable('candidate_relation'))
);

SELECT candidate_minus_baseline: count(*)
FROM (
    SELECT * FROM query_table(getvariable('candidate_relation'))
    EXCEPT ALL
    SELECT * FROM query_table(getvariable('baseline_relation'))
);
