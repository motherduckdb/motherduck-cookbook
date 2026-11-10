-- State for the self-healing Flights: four tables (the healer writes them, the Dive reads them).
-- Guides live in MotherDuck itself (MD_LIST_GUIDES); agent accounts and shares are found by name, not stored.
CREATE DATABASE IF NOT EXISTS heal;
CREATE DATABASE IF NOT EXISTS heal_demo;
USE heal;
CREATE SEQUENCE IF NOT EXISTS incident_seq START 1;

-- One row per incident: its current state, and the fix waiting for a human (proposal: NULL when there is none).
-- Everything that happened to it is in heal.main.log. No PRIMARY KEY: the sequence makes ids unique, and DuckDB 1.5.6
-- fails ("Attempted to access index ... within vector") when a query reads a VARIANT field through an index lookup.
CREATE TABLE IF NOT EXISTS heal.main.incidents (
    incident_id BIGINT DEFAULT nextval('incident_seq'), created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(), status VARCHAR, pipeline VARCHAR, class VARCHAR, category VARCHAR,
    disposition VARCHAR, title VARCHAR, fingerprint VARCHAR, attempts INTEGER DEFAULT 0, green_runs INTEGER DEFAULT 0,
    fixed_version INTEGER, shipped_at TIMESTAMPTZ, guide_id UUID, root_id BIGINT, proposal VARIANT
);
-- Append-only: every fact about an incident or the healer, one row each, never updated.
--   kind        what happened: transition, failure, attempt, cost, decision, alert, guide_link, or a fix step
--               (agent, run, tool, notice, guard, test-review, approver, sync, submit, guide, note)
--   fix_id      the fix attempt it belongs to, e.g. inc12-a1-r340 (fix steps, attempt, cost)
--   verdict     the outcome: the new status, ok/failed, approve/deny, the webhook status of an alert, ...
--   detail      everything else, as a VARIANT (read it in SQL as detail.field or detail::JSON)
CREATE TABLE IF NOT EXISTS heal.main.log (
    ts TIMESTAMPTZ DEFAULT now(), incident_id BIGINT, fix_id VARCHAR, healer_run BIGINT, kind VARCHAR, verdict VARCHAR,
    message VARCHAR, detail VARIANT
);
-- Recent runs of every Flight (for the Dive), and whether the healer has processed each one:
-- checked is NULL until then, 'done', 'retry' (it failed to process), or 'dead' (parked after 30 minutes of retries).
-- The key makes the upsert possible; read config as config::JSON (see the note on incidents).
CREATE TABLE IF NOT EXISTS heal.main.runs (
    flight_id UUID, run_number BIGINT, pipeline VARCHAR, kind VARCHAR, status VARCHAR, is_scheduled BOOLEAN,
    created_at TIMESTAMPTZ, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, config VARIANT,
    checked VARCHAR, check_attempts INTEGER DEFAULT 0, check_error VARCHAR, first_checked_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
    PRIMARY KEY (flight_id, run_number)
);
-- Leases: 'healer' (the one run that checks and fixes), 'agent:<account>' (one fix per agent service account), and
-- 'kill_switch' (on while a human holds it: holder is who turned it on).
CREATE TABLE IF NOT EXISTS heal.main.leases (
    name VARCHAR PRIMARY KEY, holder VARCHAR, run_number BIGINT, acquired_at TIMESTAMPTZ, renewed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ
);
