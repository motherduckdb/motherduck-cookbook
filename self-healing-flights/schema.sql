-- State for the self-healing Flights (the healer writes it, the Dive reads it).
CREATE DATABASE IF NOT EXISTS heal;
CREATE DATABASE IF NOT EXISTS heal_demo;
USE heal;
CREATE SEQUENCE IF NOT EXISTS incident_seq START 1;
CREATE SEQUENCE IF NOT EXISTS event_seq START 1;
CREATE SEQUENCE IF NOT EXISTS alert_seq START 1;
CREATE SEQUENCE IF NOT EXISTS proposal_seq START 1;

-- One normalized failure per failed run, grouped into an incident.
CREATE TABLE IF NOT EXISTS heal.main.events (
    event_id BIGINT DEFAULT nextval('event_seq'), ts TIMESTAMPTZ DEFAULT now(), source VARCHAR DEFAULT 'run', pipeline VARCHAR,
    flight_id UUID, run_number BIGINT, sha VARCHAR, error_class VARCHAR, norm_msg VARCHAR, top_frames VARCHAR[], asset VARCHAR,
    resource VARCHAR, fingerprint VARCHAR, raw_excerpt VARCHAR, incident_id BIGINT, attach_reason VARCHAR, event_uid VARCHAR
);
-- root_id links an incident to the active fix on the same pipeline it is waiting behind.
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
-- Every step of a fix (the Dive draws the healing DAG from this): runs, agent tool calls, notices, checks, reviews, syncs.
CREATE TABLE IF NOT EXISTS heal.main.actions (
    fixer_run_id VARCHAR, seq INTEGER, ts TIMESTAMPTZ DEFAULT now(), kind VARCHAR, command VARCHAR, verdict VARCHAR, detail JSON
);
-- Recent runs of every Flight, for the Dive.
CREATE TABLE IF NOT EXISTS heal.main.flight_runs (
    component VARCHAR, kind VARCHAR, flight_id UUID, run_number BIGINT, status VARCHAR, is_scheduled BOOLEAN,
    created_at TIMESTAMPTZ, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, sha VARCHAR, config JSON, snapshot_at TIMESTAMPTZ,
    PRIMARY KEY (flight_id, run_number)
);
-- Which finished runs the healer has processed. A run that keeps failing to process is parked as 'dead' (and alerted).
CREATE TABLE IF NOT EXISTS heal.main.run_checks (
    flight_id UUID, run_number BIGINT, status VARCHAR, attempts INTEGER DEFAULT 0, error VARCHAR, updated_at TIMESTAMPTZ DEFAULT now(),
    first_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (flight_id, run_number)
);
ALTER TABLE heal.main.run_checks ADD COLUMN IF NOT EXISTS first_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS heal.main.pipeline_registry (
    name VARCHAR PRIMARY KEY, kind VARCHAR, flight_name VARCHAR, description VARCHAR, autonomy VARCHAR,
    upstream VARCHAR[], assets VARCHAR[], resources VARCHAR[], schedule VARCHAR, updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS heal.main.heartbeats (component VARCHAR, ts TIMESTAMPTZ DEFAULT now(), run_number BIGINT, sha VARCHAR, detail JSON);
-- Leases: 'healer' (the one run that checks and fixes) and 'agent:<account>' (one fix per agent account at a time).
CREATE TABLE IF NOT EXISTS heal.main.leases (
    name VARCHAR PRIMARY KEY, holder VARCHAR, run_number BIGINT, acquired_at TIMESTAMPTZ, renewed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS heal.main.control (key VARCHAR PRIMARY KEY, value VARCHAR, updated_at TIMESTAMPTZ DEFAULT now(), updated_by VARCHAR);
INSERT OR IGNORE INTO heal.main.control (key, value, updated_by) VALUES ('kill_switch', 'off', 'setup'), ('daily_spend_cap_usd', '20', 'setup');
-- Everything a human should know about: stuck or crashed healer runs, escalations, fixes waiting for approval.
CREATE TABLE IF NOT EXISTS heal.main.alerts (
    alert_id BIGINT DEFAULT nextval('alert_seq'), ts TIMESTAMPTZ DEFAULT now(), kind VARCHAR, severity VARCHAR, dedupe_key VARCHAR,
    message VARCHAR, incident_id BIGINT, run_number BIGINT, webhook_status VARCHAR
);
-- A fix waiting for a human (autonomy L3). The Dive's Approve button runs the healer with ACTION=approve.
CREATE TABLE IF NOT EXISTS heal.main.proposals (
    proposal_id BIGINT PRIMARY KEY DEFAULT nextval('proposal_seq'), incident_id BIGINT, created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(), status VARCHAR, flight_id UUID, base_version INTEGER, source VARCHAR, diff VARCHAR,
    summary JSON, decided_at TIMESTAMPTZ, decided_by VARCHAR, shipped_version INTEGER, note VARCHAR
);
ALTER TABLE heal.main.proposals ADD COLUMN IF NOT EXISTS decided_by VARCHAR;
-- The pool of service accounts agents run as, and the restricted shares those accounts can read.
CREATE TABLE IF NOT EXISTS heal.main.agent_accounts (username VARCHAR PRIMARY KEY, role VARCHAR, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS heal.main.agent_shares (
    database_name VARCHAR PRIMARY KEY, share_name VARCHAR, share_url VARCHAR, role VARCHAR, created_at TIMESTAMPTZ DEFAULT now()
);
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
