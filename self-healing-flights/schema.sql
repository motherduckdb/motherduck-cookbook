-- State for the self-healing Flights (same tables and columns the Dive reads).
CREATE DATABASE IF NOT EXISTS heal;
CREATE DATABASE IF NOT EXISTS heal_demo;
USE heal;
CREATE SEQUENCE IF NOT EXISTS incident_seq START 1;
CREATE SEQUENCE IF NOT EXISTS event_seq START 1;

-- Failures the pipeline reports about itself (data tests, HTTP errors).
CREATE TABLE IF NOT EXISTS heal.main.pipeline_events (
    ts TIMESTAMPTZ DEFAULT now(), pipeline VARCHAR, flight_id UUID, run_number BIGINT,
    severity VARCHAR, error_type VARCHAR, message VARCHAR
);
-- One normalized failure per failed run, grouped into an incident.
CREATE TABLE IF NOT EXISTS heal.main.events (
    event_id BIGINT DEFAULT nextval('event_seq'), ts TIMESTAMPTZ DEFAULT now(), source VARCHAR DEFAULT 'run', pipeline VARCHAR,
    flight_id UUID, run_number BIGINT, sha VARCHAR, error_class VARCHAR, norm_msg VARCHAR, top_frames VARCHAR[], asset VARCHAR,
    resource VARCHAR, fingerprint VARCHAR, raw_excerpt VARCHAR, incident_id BIGINT, attach_reason VARCHAR, event_uid VARCHAR
);
CREATE TABLE IF NOT EXISTS heal.main.incidents (
    incident_id BIGINT PRIMARY KEY DEFAULT nextval('incident_seq'), created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(), status VARCHAR, pipeline VARCHAR, pipelines VARCHAR[], class VARCHAR,
    category VARCHAR, disposition VARCHAR, severity VARCHAR, level VARCHAR, title VARCHAR, fingerprint VARCHAR,
    event_count INTEGER DEFAULT 1, last_event_at TIMESTAMPTZ, attempts INTEGER DEFAULT 0,
    cost_usd DOUBLE DEFAULT 0, gh_issue INTEGER, gh_pr INTEGER, fix_sha VARCHAR, merged_at TIMESTAMPTZ,
    green_runs INTEGER DEFAULT 0, red_runs INTEGER DEFAULT 0, muted BOOLEAN DEFAULT false, guide_id UUID, resolved_at TIMESTAMPTZ,
    scenario VARCHAR, notes VARCHAR, resource VARCHAR, root_id BIGINT, first_sha VARCHAR
);
CREATE TABLE IF NOT EXISTS heal.main.incident_transitions (
    incident_id BIGINT, ts TIMESTAMPTZ DEFAULT now(), from_status VARCHAR, to_status VARCHAR, reason VARCHAR, actor VARCHAR
);
CREATE TABLE IF NOT EXISTS heal.main.fixer_runs (
    fixer_run_id VARCHAR PRIMARY KEY, incident_id BIGINT, attempt INTEGER, flight_run_number BIGINT,
    started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, status VARCHAR, level VARCHAR, model VARCHAR, approver_model VARCHAR,
    cost_usd DOUBLE, approver_rounds INTEGER, outcome VARCHAR, branch VARCHAR, pr_number INTEGER, summary JSON
);
-- Every step a fixer run took: the Dive draws the healing DAG from this.
CREATE TABLE IF NOT EXISTS heal.main.actions (
    fixer_run_id VARCHAR, seq INTEGER, ts TIMESTAMPTZ DEFAULT now(), kind VARCHAR, command VARCHAR, verdict VARCHAR, detail JSON
);
-- Recent runs of every Flight (the Dive reads these; a pipeline run listed here as finished has been checked).
CREATE TABLE IF NOT EXISTS heal.main.flight_runs (
    component VARCHAR, kind VARCHAR, flight_id UUID, run_number BIGINT, status VARCHAR, is_scheduled BOOLEAN,
    created_at TIMESTAMPTZ, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, sha VARCHAR, config JSON, snapshot_at TIMESTAMPTZ,
    PRIMARY KEY (flight_id, run_number)
);
CREATE TABLE IF NOT EXISTS heal.main.pipeline_registry (
    name VARCHAR PRIMARY KEY, kind VARCHAR, flight_name VARCHAR, description VARCHAR, autonomy VARCHAR,
    upstream VARCHAR[], assets VARCHAR[], resources VARCHAR[], schedule VARCHAR, updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS heal.main.heartbeats (component VARCHAR, ts TIMESTAMPTZ DEFAULT now(), run_number BIGINT, sha VARCHAR, detail JSON);
CREATE TABLE IF NOT EXISTS heal.main.control (key VARCHAR PRIMARY KEY, value VARCHAR, updated_at TIMESTAMPTZ DEFAULT now(), updated_by VARCHAR);
INSERT OR IGNORE INTO heal.main.control (key, value, updated_by) VALUES
    ('kill_switch', 'off', 'setup'), ('autonomy_cap', 'L1', 'setup'), ('daily_spend_cap_usd', '20', 'setup');
CREATE TABLE IF NOT EXISTS heal.main.guide_index (
    guide_id UUID PRIMARY KEY, version INTEGER, topic VARCHAR, category VARCHAR, fingerprint VARCHAR, class VARCHAR,
    pipeline VARCHAR, outcome VARCHAR, incident_id BIGINT, title VARCHAR, description VARCHAR, embedding FLOAT[512],
    helpful_count INTEGER DEFAULT 0, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS heal.main.guide_links (
    from_guide_id UUID, to_guide_id UUID, to_version INTEGER, how_used VARCHAR, reason VARCHAR, incident_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS heal.main.guide_reads (incident_id BIGINT, fixer_run_id VARCHAR, guide_id UUID, guide_version INTEGER, read_at TIMESTAMPTZ DEFAULT now());
CREATE SCHEMA IF NOT EXISTS heal.eval;
CREATE TABLE IF NOT EXISTS heal.eval.scenario_runs (
    campaign VARCHAR, scenario VARCHAR, level VARCHAR, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, injected_at TIMESTAMPTZ,
    detected_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ, incident_ids BIGINT[], expected JSON, observed JSON, scores JSON,
    passed BOOLEAN, cost_usd DOUBLE, notes VARCHAR
);
