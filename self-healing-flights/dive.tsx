import { useEffect, useMemo, useRef, useState } from 'react';
import { useSQLQuery, useDiveState, useFlightRun, useRunFlight } from '@motherduck/react-sql-query';
import {
	Bell,
	BookOpen,
	Bot,
	Clock,
	CloudSun,
	Database,
	Eye,
	FileCode,
	FlaskConical,
	GitMerge,
	HeartPulse,
	Inbox,
	KeyRound,
	LayoutDashboard,
	Lock,
	Rocket,
	Search,
	ShieldCheck,
	Sparkles,
	UserCheck,
	Users,
	Wrench,
} from 'lucide-react';

export const REQUIRED_DATABASES = [
	{ type: 'database', path: 'md:heal', alias: 'heal_board' },
];

// Theme: MotherDuck palette plus status colors from the spec
const T = {
	bg: '#f8f8f8',
	panel: '#ffffff',
	text: '#231f20',
	muted: '#6a6a6a',
	faint: '#9a9a9a',
	grid: '#e4e4e4',
	line: '#d4d4d4',
	blue: '#0777b3',
	green: '#2d7a00',
	red: '#bc1200',
	orange: '#e18727',
	purple: '#7b4fb5',
	amber: '#a87f00',
	teal: '#2a7f8f',
	gray: '#adadad',
	slate: '#638cad',
	font: "'Helvetica Neue', Helvetica, Arial, sans-serif",
	mono: "Consolas, Monaco, 'SF Mono', monospace",
};

const H = '"heal_board"."main"';
// The healer Flight that the Approve, Reject and Retry buttons run (deploy.py writes its id here).
const HEALER_FLIGHT_ID = '__HEALER_FLIGHT_ID__';
// The healer runs every 5 minutes and beats every 30 seconds while it works: red when silent for 2x the schedule.
const HB_EXPECTED_MIN = 5;

type Row = Record<string, unknown>;
const N = (v: unknown): number => (v == null ? 0 : Number(v));
const NN = (v: unknown): number | null => (v == null || v === '' ? null : Number(v));
const S = (v: unknown): string => (v == null ? '' : String(v));
const B = (v: unknown): boolean => v === true || v === 'true';
const rowsOf = (q: { data?: unknown }): Row[] => (Array.isArray(q.data) ? (q.data as Row[]) : []);
const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
const usd = (v: number) => (v >= 10 ? `$${v.toFixed(0)}` : v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(3)}`);
const parseList = (s: unknown): string[] => {
	try {
		const v = JSON.parse(S(s) || 'null');
		if (Array.isArray(v)) return v.map((x) => (typeof x === 'string' ? x : JSON.stringify(x)));
		if (v && typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k}: ${typeof x === 'string' ? x : JSON.stringify(x)}`);
		return v == null ? [] : [String(v)];
	} catch {
		return S(s) ? [S(s)] : [];
	}
};

function fmtDur(ms: number | null | undefined): string {
	if (ms == null || !isFinite(ms) || ms < 0) return '–';
	const s = ms / 1000;
	if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
	const h = Math.floor(m / 60);
	if (h < 48) return `${h}h ${String(m % 60).padStart(2, '0')}m`;
	return `${Math.floor(h / 24)}d ${h % 24}h`;
}
function ago(ms: number | null, now: number): string {
	if (ms == null) return 'never';
	const d = Math.max(0, now - ms);
	if (d < 60000) return `${Math.round(d / 1000)}s ago`;
	if (d < 3600000) return `${Math.round(d / 60000)}m ago`;
	if (d < 172800000) return `${Math.round(d / 3600000)}h ago`;
	return `${Math.round(d / 86400000)}d ago`;
}
const clock = (ms: number | null) =>
	ms == null ? '–' : new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const stamp = (ms: number | null) =>
	ms == null
		? '–'
		: new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });

// ---------- status styles ----------
const RUN_COLOR: Record<string, string> = {
	SUCCEEDED: T.green,
	FAILED: T.red,
	RUNNING: T.blue,
	PENDING: T.gray,
	CANCELLED: '#7d7d7d',
};
const runColor = (s: string) => RUN_COLOR[s] ?? T.gray;
const INC_STYLE: Record<string, { c: string; outline?: boolean }> = {
	open: { c: T.red },
	watching: { c: T.amber, outline: true },
	investigating: { c: T.blue },
	proposed: { c: T.purple },
	merged: { c: T.green },
	verifying: { c: T.teal, outline: true },
	resolved: { c: T.green, outline: true },
	regressed: { c: T.red, outline: true },
	escalated: { c: T.orange },
};
const OUTCOME_COLOR: Record<string, string> = { merged: T.green, proposed: T.purple, escalated: T.orange, running: T.blue };
const ACTIVE = ['open', 'watching', 'investigating', 'proposed', 'merged', 'verifying', 'regressed', 'escalated'];

function Pill({ label, color, outline, title }: { label: string; color: string; outline?: boolean; title?: string }) {
	return (
		<span
			title={title}
			style={{
				display: 'inline-block',
				fontSize: 10,
				fontWeight: 700,
				lineHeight: '14px',
				padding: '0 6px',
				borderRadius: 8,
				whiteSpace: 'nowrap',
				color: outline ? color : '#fff',
				background: outline ? 'transparent' : color,
				border: `1px solid ${color}`,
			}}
		>
			{label}
		</span>
	);
}
const IncPill = ({ status }: { status: string }) => {
	const st = INC_STYLE[status] ?? { c: T.gray };
	return <Pill label={status || 'unknown'} color={st.c} outline={st.outline} />;
};
const RunPill = ({ status }: { status: string }) => <Pill label={status.toLowerCase() || 'unknown'} color={runColor(status)} />;

function Skeleton({ h = 16, w = '100%' }: { h?: number; w?: number | string }) {
	return <div className="animate-pulse rounded" style={{ height: h, width: w, background: '#e6e6e6' }} />;
}
function ErrorLine({ error }: { error: unknown }) {
	return (
		<p role="alert" style={{ color: T.red, fontSize: 12, fontFamily: T.mono }}>
			{String(error)}
		</p>
	);
}
function Empty({ children }: { children: React.ReactNode }) {
	return (
		<div style={{ fontSize: 12, color: T.muted, padding: '14px 12px', border: `1px dashed ${T.line}`, borderRadius: 4 }}>{children}</div>
	);
}
function SectionTitle({ children, note }: { children: React.ReactNode; note?: React.ReactNode }) {
	return (
		<div className="flex items-baseline justify-between" style={{ marginBottom: 8, gap: 12 }}>
			<h2 style={{ fontSize: 12, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: T.text }}>{children}</h2>
			{note ? <span style={{ fontSize: 11, color: T.muted, textAlign: 'right' }}>{note}</span> : null}
		</div>
	);
}
function Section({ children }: { children: React.ReactNode }) {
	return <section style={{ borderTop: `1px solid ${T.line}`, paddingTop: 12, marginTop: 18 }}>{children}</section>;
}
const th: React.CSSProperties = {
	fontSize: 9,
	fontWeight: 700,
	color: T.muted,
	textTransform: 'uppercase',
	letterSpacing: '0.03em',
	padding: '5px 6px',
	borderBottom: `1px solid ${T.text}`,
	textAlign: 'left',
	whiteSpace: 'nowrap',
};
const td: React.CSSProperties = { padding: '5px 6px', borderBottom: `1px solid ${T.grid}`, fontSize: 12, color: T.text, verticalAlign: 'top' };
const btn = (active = false): React.CSSProperties => ({
	fontSize: 11,
	padding: '2px 8px',
	borderRadius: 3,
	border: `1px solid ${active ? T.text : T.line}`,
	background: active ? T.text : T.panel,
	color: active ? '#fff' : T.text,
	cursor: 'pointer',
});
const sel: React.CSSProperties = { fontSize: 11, padding: '2px 4px', border: `1px solid ${T.line}`, borderRadius: 3, background: T.panel, color: T.text };
const linkStyle: React.CSSProperties = { color: T.blue, textDecoration: 'underline', cursor: 'pointer' };
const Ext = ({ href, children }: { href: string; children: React.ReactNode }) => (
	<a href={href} target="_blank" rel="noopener noreferrer" style={linkStyle}>
		{children}
	</a>
);

function useWidth<E extends HTMLElement>(): [React.RefObject<E>, number] {
	const ref = useRef<E>(null);
	const [w, setW] = useState(760);
	useEffect(() => {
		if (!ref.current) return;
		const ro = new ResizeObserver((es) => setW(Math.max(320, Math.floor(es[0].contentRect.width))));
		ro.observe(ref.current);
		return () => ro.disconnect();
	}, []);
	return [ref, w];
}

// ---------- SQL ----------
// Four tables: incidents (current state + any proposal), log (append-only facts), runs, leases. VARIANT columns are
// read as detail.field::TYPE, or as x::JSON when a query wants the JSON operators (always for runs.config: see schema.sql).
const L = `${H}."log"`;
const HEALTH_SQL = `
WITH inc AS (SELECT status FROM ${H}."incidents"),
cfg AS (SELECT arg_max(config::JSON, created_at) AS c FROM ${H}."runs" WHERE kind = 'healer' AND config IS NOT NULL)
SELECT
  (SELECT count(*) FROM inc WHERE status IN ('open', 'watching'))::INT AS n_open,
  (SELECT count(*) FROM inc WHERE status = 'investigating')::INT AS n_inv,
  (SELECT count(*) FROM inc WHERE status IN ('merged', 'verifying'))::INT AS n_ver,
  (SELECT count(*) FROM inc WHERE status IN ('escalated', 'regressed'))::INT AS n_esc,
  (SELECT count(*) FROM inc WHERE status IN ('proposed', 'deciding'))::INT AS n_prop,
  (SELECT count(*) FROM inc WHERE status = 'resolved')::INT AS n_res,
  (SELECT count(*) FROM inc)::INT AS n_all,
  (SELECT epoch_ms(max(renewed_at))::DOUBLE FROM ${H}."leases" WHERE name = 'healer') AS checker_ms,
  (SELECT c->>'AUTONOMY' FROM cfg) AS autonomy,
  (SELECT c->>'DAILY_BUDGET_USD' FROM cfg) AS spend_cap,
  (SELECT count(*) FROM inc WHERE status = 'proposed')::INT AS n_pending,
  (SELECT count(*) FROM ${H}."leases" WHERE name LIKE 'agent:%')::INT AS n_agents,
  (SELECT count(*) FROM ${H}."leases" WHERE name LIKE 'agent:%' AND holder IS NOT NULL AND expires_at > now())::INT AS n_agents_busy,
  (SELECT count(*) FROM ${L} WHERE kind = 'alert' AND ts > now() - INTERVAL 24 HOUR AND detail.severity::VARCHAR <> 'info')::INT AS n_alerts,
  (SELECT coalesce(sum(detail.usd::DOUBLE), 0) FROM ${L}
     WHERE kind = 'cost' AND (ts AT TIME ZONE 'UTC')::DATE = (now() AT TIME ZONE 'UTC')::DATE)::DOUBLE AS spend_today,
  (SELECT epoch_ms(max(updated_at))::DOUBLE FROM ${H}."runs") AS snapshot_ms,
  epoch_ms(now())::DOUBLE AS now_ms`;

// The pipelines are the ones with runs; their autonomy is the healer's.
const REG_SQL = `
WITH r AS (SELECT pipeline AS name, any_value(kind) AS kind FROM ${H}."runs" GROUP BY ALL),
a AS (SELECT arg_max(config::JSON->>'AUTONOMY', created_at) AS autonomy FROM ${H}."runs" WHERE kind = 'healer')
SELECT r.name, r.kind, CASE WHEN r.kind = 'pipeline' THEN (SELECT autonomy FROM a) END AS autonomy,
  (SELECT string_agg(i.incident_id::VARCHAR || '|' || i.status || '|' || coalesce(i.category, '') || '|true', ';' ORDER BY i.incident_id DESC)
     FROM ${H}."incidents" i WHERE i.pipeline = r.name AND i.status NOT IN ('resolved')) AS active
FROM r
ORDER BY r.kind DESC, r.name`;

const RUNS_SQL = `
WITH att AS (SELECT healer_run, min(incident_id) AS incident_id, min(fix_id) AS fix_id FROM ${L}
             WHERE kind = 'attempt' AND verdict = 'started' GROUP BY ALL),
outc AS (SELECT fix_id, arg_max(verdict, ts) AS outcome FROM ${L} WHERE kind = 'attempt' AND verdict <> 'started' GROUP BY ALL)
SELECT f.pipeline AS component, f.kind, f.run_number::DOUBLE AS run_number, f.status, f.is_scheduled,
  epoch_ms(f.created_at)::DOUBLE AS created_ms, epoch_ms(f.started_at)::DOUBLE AS started_ms,
  epoch_ms(f.ended_at)::DOUBLE AS ended_ms,
  coalesce(a.incident_id, TRY_CAST(split_part(f.config::JSON->>'INCIDENT_ID', ':', 1) AS BIGINT))::DOUBLE AS incident_id,
  a.fix_id AS fixer_run_id, nullif(f.config::JSON->>'ACTION', '') AS mode, o.outcome,
  EXISTS (SELECT 1 FROM ${H}."incidents" i
          WHERE i.incident_id = coalesce(a.incident_id, TRY_CAST(split_part(f.config::JSON->>'INCIDENT_ID', ':', 1) AS BIGINT))) AS inc_exists
FROM ${H}."runs" f
LEFT JOIN att a ON f.kind = 'healer' AND a.healer_run = f.run_number
LEFT JOIN outc o ON o.fix_id = a.fix_id
ORDER BY f.pipeline, f.run_number`;

const INC_SQL = `
WITH ev AS (SELECT incident_id, count(*) AS n FROM ${L} WHERE kind = 'failure' GROUP BY ALL),
co AS (SELECT incident_id, sum(detail.usd::DOUBLE) AS usd FROM ${L} WHERE kind = 'cost' GROUP BY ALL),
rs AS (SELECT incident_id, max(ts) AS ts FROM ${L} WHERE kind = 'transition' AND verdict = 'resolved' GROUP BY ALL)
SELECT i.incident_id::DOUBLE AS id, i.status, i.class, i.category, i.disposition, i.pipeline, i.title,
  coalesce(ev.n, 0)::DOUBLE AS events, coalesce(i.attempts, 0)::DOUBLE AS attempts, coalesce(co.usd, 0)::DOUBLE AS cost,
  i.fixed_version::DOUBLE AS fixed_version, coalesce(i.green_runs, 0)::DOUBLE AS green_runs, i.guide_id::VARCHAR AS guide_id,
  i.root_id::DOUBLE AS root_id, i.fingerprint,
  epoch_ms(i.created_at)::DOUBLE AS created_ms, epoch_ms(i.updated_at)::DOUBLE AS updated_ms,
  epoch_ms(i.shipped_at)::DOUBLE AS merged_ms, CASE WHEN i.status = 'resolved' THEN epoch_ms(rs.ts)::DOUBLE END AS resolved_ms
FROM ${H}."incidents" i LEFT JOIN ev USING (incident_id) LEFT JOIN co USING (incident_id) LEFT JOIN rs USING (incident_id)
ORDER BY i.created_at DESC, i.incident_id DESC`;

const transSQL = (id: number) => `
SELECT epoch_ms(ts)::DOUBLE AS ts_ms, detail.prior::VARCHAR AS from_status, verdict AS to_status, message AS reason, 'healer' AS actor
FROM ${L} WHERE incident_id = ${id} AND kind = 'transition' ORDER BY ts`;

const eventsSQL = (id: number) => `
SELECT (row_number() OVER (ORDER BY ts))::DOUBLE AS event_id, epoch_ms(ts)::DOUBLE AS ts_ms, detail.pipeline::VARCHAR AS pipeline,
  detail.run_number::DOUBLE AS run_number, verdict AS error_class, message AS norm_msg, coalesce(detail.attach::VARCHAR, 'new') AS attach_reason
FROM ${L} WHERE incident_id = ${id} AND kind = 'failure' ORDER BY ts`;

// One row per fix attempt: its 'attempt' rows (started, then the outcome) and its 'cost' rows.
const fixerSQL = (id: number) => `
WITH a AS (
  SELECT fix_id, min(ts) FILTER (WHERE verdict = 'started') AS started, max(ts) FILTER (WHERE verdict <> 'started') AS ended,
    arg_max(verdict, ts) FILTER (WHERE verdict <> 'started') AS outcome,
    any_value(detail::JSON) FILTER (WHERE verdict = 'started') AS s, arg_max(detail::JSON, ts) FILTER (WHERE verdict <> 'started') AS e,
    any_value(healer_run) FILTER (WHERE verdict = 'started') AS frn
  FROM ${L} WHERE incident_id = ${id} AND kind = 'attempt' GROUP BY fix_id),
c AS (SELECT fix_id, sum(detail.usd::DOUBLE) AS usd, max(detail.rounds::INT) AS rounds FROM ${L}
      WHERE incident_id = ${id} AND kind = 'cost' GROUP BY fix_id)
SELECT a.fix_id AS fixer_run_id, TRY_CAST(a.s->>'attempt' AS DOUBLE) AS attempt, a.frn::DOUBLE AS frn,
  epoch_ms(a.started)::DOUBLE AS started_ms, epoch_ms(a.ended)::DOUBLE AS ended_ms,
  CASE WHEN a.ended IS NULL THEN 'running' ELSE 'done' END AS status, a.s->>'autonomy' AS level, a.s->>'model' AS model,
  a.s->>'approver_model' AS approver_model, coalesce(c.usd, 0)::DOUBLE AS cost,
  coalesce(c.rounds, TRY_CAST(a.e->>'rounds' AS INT))::DOUBLE AS approver_rounds, coalesce(a.outcome, 'running') AS outcome,
  a.e->'summary'->>'symptoms' AS symptoms, a.e->'summary'->>'root_cause' AS root_cause, a.e->'summary'->>'fix' AS fix,
  a.e->'summary'->>'verification' AS verification, a.e->'summary'->>'approver_notes' AS approver_notes,
  (SELECT any_value(f.status) FROM ${H}."runs" f WHERE f.kind = 'healer' AND f.run_number = a.frn) AS flight_status
FROM a LEFT JOIN c USING (fix_id)
ORDER BY attempt NULLS LAST, started_ms`;

const actionsSQL = (id: number) => `
SELECT fix_id AS fixer_run_id, (row_number() OVER (PARTITION BY fix_id ORDER BY ts))::DOUBLE AS seq, epoch_ms(ts)::DOUBLE AS ts_ms, kind,
  left(coalesce(message, ''), 600) AS command, coalesce(verdict, '') AS verdict,
  d->>'feedback' AS feedback, d->>'risk' AS risk,
  (d->'violations')::VARCHAR AS violations, (d->'l2_blocks')::VARCHAR AS l2_blocks,
  (d->'errors')::VARCHAR AS errors, (d->'exits')::VARCHAR AS exits,
  left(d->>'output', 300) AS output
FROM (SELECT *, detail::JSON AS d FROM ${L} WHERE incident_id = ${id} AND fix_id IS NOT NULL AND kind NOT IN ('attempt', 'cost'))
ORDER BY fixer_run_id, seq`;

// Guides live in MotherDuck: listed with MD_LIST_GUIDES, linked through incidents.guide_id and the log.
const GUIDE_LIST = `(SELECT id, title, description, topic, split_part(topic, '/', 3) AS category, current_version, created_at, updated_at
  FROM MD_LIST_GUIDES() WHERE topic LIKE 'self-healing/incidents/%')`;

const incGuidesSQL = (id: number) => `
WITH g AS ${GUIDE_LIST}
SELECT 'own' AS rel, i.guide_id::VARCHAR AS guide_id, g.title, g.category, NULL::VARCHAR AS how_used, NULL::VARCHAR AS reason
FROM ${H}."incidents" i JOIN g ON g.id = i.guide_id WHERE i.incident_id = ${id}
UNION ALL
SELECT 'link', l.detail.to_guide::VARCHAR, g.title, g.category, l.verdict, l.message
FROM ${L} l LEFT JOIN g ON g.id::VARCHAR = l.detail.to_guide::VARCHAR WHERE l.incident_id = ${id} AND l.kind = 'guide_link'
UNION ALL
SELECT DISTINCT 'read', coalesce(r.detail.guide_id::VARCHAR, replace(r.message, 'guide get ', '')) AS gid, g.title, g.category, r.fix_id, NULL
FROM ${L} r LEFT JOIN g ON g.id::VARCHAR = coalesce(r.detail.guide_id::VARCHAR, replace(r.message, 'guide get ', ''))
WHERE r.incident_id = ${id} AND r.kind = 'guide'`;

const GUIDES_SQL = `
WITH g AS ${GUIDE_LIST},
h AS (SELECT detail.to_guide::VARCHAR AS gid, count(*) AS n FROM ${L} WHERE kind = 'guide_link' GROUP BY ALL)
SELECT g.id::VARCHAR AS guide_id, g.current_version::DOUBLE AS version, g.topic, g.category, i.class, i.pipeline, 'fixed' AS outcome,
  i.incident_id::DOUBLE AS incident_id, g.title, g.description, coalesce(h.n, 0)::DOUBLE AS helpful,
  epoch_ms(g.created_at)::DOUBLE AS created_ms, epoch_ms(g.updated_at)::DOUBLE AS updated_ms
FROM g LEFT JOIN ${H}."incidents" i ON i.guide_id = g.id LEFT JOIN h ON h.gid = g.id::VARCHAR
ORDER BY g.category, g.created_at DESC`;

const LINKS_SQL = `
SELECT detail.from_guide::VARCHAR AS from_id, detail.to_guide::VARCHAR AS to_id, detail.to_version::DOUBLE AS to_version,
  verdict AS how_used, message AS reason, incident_id::DOUBLE AS incident_id, epoch_ms(ts)::DOUBLE AS created_ms
FROM ${L} WHERE kind = 'guide_link' ORDER BY ts`;

const ALERTS_SQL = `
SELECT (row_number() OVER (ORDER BY ts))::DOUBLE AS id, epoch_ms(ts)::DOUBLE AS ts_ms, detail.type::VARCHAR AS kind,
  detail.severity::VARCHAR AS severity, message, incident_id::DOUBLE AS incident_id, verdict AS webhook_status
FROM ${L} WHERE kind = 'alert' AND ts > now() - INTERVAL 7 DAY ORDER BY ts DESC LIMIT 50`;

// The fix waiting on each incident (the latest one it had). Approve/Reject send INCIDENT_ID = <incident>:<n>.
const PROPOSALS_SQL = `
SELECT (p->>'n')::DOUBLE AS id, i.incident_id::DOUBLE AS incident_id,
  CASE WHEN i.status = 'deciding' THEN 'deciding' ELSE p->>'status' END AS status,
  (p->>'base_version')::DOUBLE AS base_version, (p->>'shipped_version')::DOUBLE AS shipped_version, p->>'diff' AS diff, p->>'note' AS note,
  i.pipeline, i.title, p->'summary'->>'root_cause' AS root_cause, p->'summary'->>'fix' AS fix, p->'summary'->>'tests' AS tests,
  p->'summary'->'test_review'->>'verdict' AS test_verdict, p->'summary'->'test_review'->>'notes' AS test_notes,
  p->'summary'->>'approver_notes' AS approver_notes, (p->'summary'->'drift')::VARCHAR AS drift, (p->'summary'->'checks')::VARCHAR AS checks,
  (p->'summary'->'risk')::VARCHAR AS risk, (p->'summary'->'unseen_failures')::VARCHAR AS unseen, p->>'decided_by' AS decided_by,
  epoch_ms(TRY_CAST(p->>'created_at' AS TIMESTAMPTZ))::DOUBLE AS created_ms, epoch_ms(TRY_CAST(p->>'decided_at' AS TIMESTAMPTZ))::DOUBLE AS decided_ms
FROM (SELECT *, proposal::JSON AS p FROM ${H}."incidents" WHERE proposal IS NOT NULL) i
ORDER BY (i.status IN ('proposed', 'deciding')) DESC, i.updated_at DESC LIMIT 30`;

// The pool accounts are the agent:<name> leases; their shares are the owner's *_heal_agents shares.
const AGENTS_SQL = `
SELECT substr(l.name, 7) AS username, 'self_healing_pipeline_agent' AS role, l.holder, l.run_number::DOUBLE AS run_number, epoch_ms(l.acquired_at)::DOUBLE AS since_ms,
  (l.holder IS NOT NULL AND l.expires_at > now()) AS busy,
  (SELECT string_agg(name, ', ') FROM md_information_schema.owned_shares WHERE suffix(name, '_heal_agents')) AS shares
FROM ${H}."leases" l WHERE l.name LIKE 'agent:%'
ORDER BY l.name`;

// ---------- healing DAG derivation ----------
type Phase = 'repro' | 'investigate' | 'edit' | 'verify' | 'submit' | 'guard' | 'tests' | 'approver' | 'sync' | 'outcome';
const PHASES: Phase[] = ['repro', 'investigate', 'edit', 'verify', 'submit', 'guard', 'tests', 'approver', 'sync', 'outcome'];
const PHASE_LABEL: Record<Phase, string> = {
	repro: 'Reproduce',
	investigate: 'Investigate',
	edit: 'Edit',
	verify: 'Verify',
	submit: 'Submit',
	guard: 'Checks',
	tests: 'Test review',
	approver: 'Approver',
	sync: 'Sync',
	outcome: 'Outcome',
};
type Action = {
	seq: number;
	ts: number;
	kind: string;
	command: string;
	verdict: string;
	feedback?: string;
	risk?: string;
	violations?: string;
	l2_blocks?: string;
	errors?: string;
	exits?: string;
	output?: string;
};
type Step = { phase: Phase; start: number; end: number; actions: Action[]; verdicts: string[] };
type Loop = { idx: number; from: Phase; to: Phase; fix: boolean; reason: string; ts: number; round: number };
type PhaseAgg = {
	visits: number;
	ms: number;
	actions: number;
	calls: number;
	errors: number;
	sql: number;
	sqlBlocked: number;
	guides: string[];
	files: string[];
	verdicts: string[];
	chips: Record<string, number>;
};
type Healing = { steps: Step[]; agg: Partial<Record<Phase, PhaseAgg>>; forward: Record<string, number>; loops: Loop[]; lastPhase: Phase | null };

const toolName = (cmd: string) => cmd.split(':')[0].trim().toLowerCase();
function subagentOf(cmd: string): string {
	const body = cmd.replace(/^task:\s*/i, '');
	try {
		const j = JSON.parse(body);
		return S(j.subagent_type || j.agent || j.description || 'subagent');
	} catch {
		const m = body.match(/(localizer|verifier|reviewer|planner)/i);
		return m ? m[1].toLowerCase() : 'subagent';
	}
}
// Maps one action to a phase; 'echo' = output of a step already recorded, 'chip' = nested on the current phase
function classify(a: Action): Phase | 'echo' | 'chip' | null {
	const low = a.command.trim().toLowerCase();
	if (a.kind === 'run') return /repro/.test(low) ? 'repro' : 'verify';
	if (a.kind === 'submit') return null; // a proposal or a shipped version: the Outcome node shows it
	if (a.kind === 'guard') return 'guard';
	if (a.kind === 'test-review') return 'tests';
	if (a.kind === 'sync') return 'sync';
	if (a.kind === 'approver') return low.startsWith('command check') ? 'chip' : 'approver';
	if (a.kind === 'notice') return 'chip';
	if (a.kind === 'guide' || a.kind === 'mdsql') return 'investigate';
	if (a.kind === 'note' || a.kind === 'agent') return null;
	if (a.kind === 'tool') {
		if (/^(bash|shell):\s*(heal (repro|verify|submit)|verify)\b/.test(low)) return 'echo';
		if (low.startsWith('task:')) return 'chip';
		const t = toolName(low);
		if (['edit', 'write', 'patch', 'multiedit', 'apply_patch'].includes(t)) return /result\.json/.test(low) ? 'submit' : 'edit';
		return 'investigate';
	}
	return 'investigate';
}
const BAD_VERDICTS = new Set(['failed', 'rejected', 'violations', 'deny', 'error', 'blocked', 'denied_by_approver', 'conflict']);
const DECISIVE = ['run', 'guard', 'test-review', 'approver', 'sync'];
function stepVerdict(st: Step): string {
	const d = st.actions.filter((a) => DECISIVE.includes(a.kind) && !a.command.toLowerCase().startsWith('command check'));
	return d.length ? d[d.length - 1].verdict : '';
}
function loopReason(st: Step, to: Phase): { fix: boolean; reason: string } {
	const v = stepVerdict(st);
	const last = [...st.actions].reverse().find((a) => DECISIVE.includes(a.kind));
	if (st.phase === 'verify' && v === 'failed') return { fix: true, reason: `verify failed${last?.exits ? ` (exits ${last.exits})` : ''}` };
	if (st.phase === 'submit' && v === 'rejected') return { fix: true, reason: `submit rejected: ${parseList(last?.errors).join('; ') || 'validation errors'}` };
	if (st.phase === 'guard' && (v === 'violations' || v === 'rejected')) return { fix: true, reason: `healer checks: ${parseList(last?.errors).join('; ') || 'failed'}` };
	if (st.phase === 'tests' && v === 'deny') return { fix: true, reason: `test reviewer deny: ${S(last?.feedback) || 'no reason'}` };
	if (st.phase === 'sync') return { fix: v === 'conflict', reason: v === 'conflict' ? 'pipeline changed upstream: merge conflict for the agent' : 'pipeline changed upstream: merged, retesting' };
	if (st.phase === 'approver' && v === 'deny') return { fix: true, reason: `approver deny: ${S(last?.feedback) || 'no feedback'}` };
	if (to === 'repro') return { fix: false, reason: 're-ran repro' };
	return { fix: false, reason: `back to ${PHASE_LABEL[to].toLowerCase()}` };
}
function buildHealing(actions: Action[], endMs: number | null): Healing {
	const steps: Step[] = [];
	const chips: { phase: Phase | null; name: string }[] = [];
	for (const a of actions) {
		const c = classify(a);
		const cur = steps[steps.length - 1];
		if (c === 'echo' || c === null || c === 'chip') {
			if (c === 'chip') {
				const name = a.kind === 'approver' ? 'command check' : a.kind === 'notice' ? 'notice' : subagentOf(a.command);
				const ph: Phase | null = /verif/.test(name) ? 'verify' : /local/.test(name) ? 'investigate' : cur?.phase ?? 'investigate';
				chips.push({ phase: ph, name });
			}
			if (cur) {
				cur.actions.push(a);
				cur.end = a.ts;
			}
			continue;
		}
		if (cur && cur.phase === c) {
			cur.actions.push(a);
			cur.end = a.ts;
		} else steps.push({ phase: c, start: a.ts, end: a.ts, actions: [a], verdicts: [] });
	}
	const agg: Partial<Record<Phase, PhaseAgg>> = {};
	const forward: Record<string, number> = {};
	const loops: Loop[] = [];
	const rounds: Record<string, number> = {};
	steps.forEach((st, i) => {
		const next = steps[i + 1];
		const stop = next ? next.start : endMs ?? st.end;
		const g = (agg[st.phase] ??= { visits: 0, ms: 0, actions: 0, calls: 0, errors: 0, sql: 0, sqlBlocked: 0, guides: [], files: [], verdicts: [], chips: {} });
		g.visits += 1;
		g.ms += Math.max(0, stop - st.start);
		g.actions += st.actions.length;
		for (const a of st.actions) {
			if (a.kind === 'tool' && !/^bash:\s*heal (repro|verify|submit)\b/i.test(a.command) && !/^task:/i.test(a.command)) g.calls += 1;
			if (a.verdict === 'error') g.errors += 1;
			if (a.kind === 'mdsql') {
				g.sql += 1;
				if (a.verdict !== 'ok') g.sqlBlocked += 1;
			}
			if (a.kind === 'guide') g.guides.push(a.command.replace(/^guide\s*/i, ''));
			if (a.kind === 'tool' && ['edit', 'write', 'patch', 'multiedit', 'apply_patch'].includes(toolName(a.command)) && st.phase === 'edit') {
				const f = a.command.split(':').slice(1).join(':').trim().split('/').pop() || '';
				if (f && !g.files.includes(f)) g.files.push(f);
			}
		}
		const v = stepVerdict(st);
		if (v) g.verdicts.push(v);
		if (next) {
			const a = PHASES.indexOf(st.phase);
			const b = PHASES.indexOf(next.phase);
			if (b > a) forward[`${st.phase}>${next.phase}`] = (forward[`${st.phase}>${next.phase}`] ?? 0) + 1;
			else {
				const r = loopReason(st, next.phase);
				const key = `${st.phase}>${next.phase}`;
				rounds[key] = (rounds[key] ?? 0) + 1;
				loops.push({ idx: loops.length + 1, from: st.phase, to: next.phase, fix: r.fix, reason: r.reason, ts: next.start, round: rounds[key] });
			}
		}
	});
	for (const ch of chips) {
		const ph = ch.phase ?? 'investigate';
		const g = agg[ph];
		if (g) g.chips[ch.name] = (g.chips[ch.name] ?? 0) + 1;
	}
	return { steps, agg, forward, loops, lastPhase: steps.length ? steps[steps.length - 1].phase : null };
}

// ---------- example data (opt-in, never mixed with real rows) ----------
const EXAMPLE_T0 = Date.UTC(2026, 0, 1, 12, 0, 0);
const EXAMPLE_ACTIONS: Action[] = (
	[
		[0, 'run', 'heal repro', 'failed', { exits: '[1]' }],
		[8, 'tool', 'bash: heal context', 'completed'],
		[9, 'guide', 'guide list', 'ok'],
		[12, 'tool', 'read: pipelines/p2_nws/main.py', 'completed'],
		[15, 'tool', 'task: {"subagent_type": "localizer", "description": "find where temperature is parsed"}', 'completed'],
		[40, 'tool', 'grep: temperature', 'completed'],
		[52, 'tool', 'edit: pipelines/p2_nws/main.py', 'completed'],
		[60, 'run', 'heal verify', 'failed', { exits: '[1]' }],
		[61, 'tool', 'bash: heal verify', 'completed'],
		[75, 'tool', 'edit: pipelines/p2_nws/main.py', 'completed'],
		[84, 'run', 'heal verify', 'ok', { exits: '[0, 0]' }],
		[95, 'tool', 'write: heal_result.json', 'completed'],
		[96, 'submit', 'heal submit', 'accepted'],
		[104, 'guard', 'guard report', 'ok', { violations: '[]' }],
		[118, 'approver', 'review', 'deny', { feedback: 'Unknown unit codes are silently dropped; keep the data test failing loudly instead.' }],
		[130, 'tool', 'edit: pipelines/p2_nws/main.py', 'completed'],
		[136, 'tool', 'task: {"subagent_type": "verifier", "description": "re-check the unknown-unit path"}', 'completed'],
		[150, 'run', 'heal verify', 'ok', { exits: '[0, 0]' }],
		[160, 'submit', 'heal submit', 'accepted'],
		[172, 'approver', 'review', 'approve', { feedback: 'Unknown units now fail the data test; minimal change.' }],
	] as [number, string, string, string, Partial<Action>?][]
).map(([s, kind, command, verdict, extra], i) => ({ seq: i + 1, ts: EXAMPLE_T0 + s * 1000, kind, command, verdict, ...(extra ?? {}) }));

// ---------- healing DAG SVG ----------
function HealingDag({
	h,
	uid,
	outcome,
	running,
	prNumber,
}: {
	h: Healing;
	uid: string;
	outcome: string;
	running: boolean;
	prNumber: number | null;
}) {
	const colW = 112;
	const nodeW = 98;
	const nodeH = 68;
	const fixLoops = h.loops.filter((l) => l.fix);
	const revisits = h.loops.filter((l) => !l.fix);
	const arcStep = 24;
	const top = 26 + Math.max(1, h.loops.length) * arcStep;
	const skipEdges = Object.keys(h.forward).filter((k) => {
		const [a, b] = k.split('>') as Phase[];
		return PHASES.indexOf(b) - PHASES.indexOf(a) > 1;
	});
	const lastIdx = h.lastPhase ? PHASES.indexOf(h.lastPhase) : -1;
	const outIdx = PHASES.length - 1;
	const finalSkip = lastIdx >= 0 && outIdx - lastIdx > 1;
	const nSkip = skipEdges.length + (finalSkip ? 1 : 0);
	const hasChips = Object.values(h.agg).some((g) => g && Object.keys(g.chips).length);
	const bottom = top + nodeH + (hasChips ? 22 : 4) + (nSkip ? 12 + nSkip * 10 : 0) + 6;
	const W = PHASES.length * colW + 8;
	const x = (p: Phase) => 8 + PHASES.indexOf(p) * colW;
	const cx = (p: Phase) => x(p) + nodeW / 2;
	const mid = top + nodeH / 2;
	const outColor = running ? T.blue : OUTCOME_COLOR[outcome] ?? (outcome ? T.red : T.gray);
	const vColor = (v: string) => (BAD_VERDICTS.has(v) ? T.red : T.green);
	const mk = (c: string) => `url(#${uid}-${c.replace('#', '')})`;
	const colors = [T.muted, T.red, T.faint];
	let skipN = 0;
	const skipPath = (a: Phase, b: Phase) => {
		skipN += 1;
		const y0 = top + nodeH + (hasChips ? 22 : 2);
		const d = 10 + skipN * 10;
		return `M ${cx(a)} ${y0} C ${cx(a)} ${y0 + d}, ${cx(b)} ${y0 + d}, ${cx(b)} ${y0}`;
	};
	return (
		<div style={{ overflowX: 'auto' }}>
			<svg viewBox={`0 0 ${W} ${bottom}`} style={{ width: '100%', minWidth: 720, display: 'block', fontFamily: T.font }}>
				<defs>
					{colors.map((c) => (
						<marker key={c} id={`${uid}-${c.replace('#', '')}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
							<path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
						</marker>
					))}
				</defs>
				{PHASES.slice(0, -1).map((p, i) => {
					const n = h.forward[`${p}>${PHASES[i + 1]}`];
					const isFinal = h.lastPhase === p && PHASES[i + 1] === 'outcome';
					if (!n && !isFinal) return null;
					return (
						<g key={`f-${p}`}>
							<line x1={x(p) + nodeW} y1={mid} x2={x(PHASES[i + 1]) - 1} y2={mid} stroke={T.muted} strokeWidth={1.4} markerEnd={mk(T.muted)} />
							{n && n > 1 ? (
								<text x={x(p) + nodeW + (colW - nodeW) / 2} y={mid - 9} fontSize={9} textAnchor="middle" fill={T.muted}>
									×{n}
								</text>
							) : null}
						</g>
					);
				})}
				{skipEdges.map((k) => {
					const [a, b] = k.split('>') as Phase[];
					return <path key={k} d={skipPath(a, b)} fill="none" stroke={T.muted} strokeWidth={1.2} markerEnd={mk(T.muted)} />;
				})}
				{finalSkip && h.lastPhase ? <path d={skipPath(h.lastPhase, 'outcome')} fill="none" stroke={T.muted} strokeWidth={1.2} markerEnd={mk(T.muted)} /> : null}
				{h.loops.map((l, i) => {
					const hgt = 16 + i * arcStep;
					const sx = cx(l.from) + 8;
					const tx = cx(l.to) - 8;
					const c = l.fix ? T.red : T.faint;
					return (
						<g key={`l-${l.idx}`}>
							<title>{`Loop ${l.idx}: ${PHASE_LABEL[l.from]} -> ${PHASE_LABEL[l.to]} (round ${l.round}) - ${l.reason}`}</title>
							<path
								d={`M ${sx} ${top} C ${sx} ${top - hgt}, ${tx} ${top - hgt}, ${tx} ${top}`}
								fill="none"
								stroke={c}
								strokeWidth={l.fix ? 1.6 : 1}
								strokeDasharray={l.fix ? undefined : '3 3'}
								markerEnd={mk(c)}
							/>
							<text x={(sx + tx) / 2} y={top - hgt * 0.75 - 4} fontSize={9} textAnchor="middle" fill={c} fontWeight={l.fix ? 700 : 400}>
								{`loop ${l.idx}: ${trunc(l.fix ? l.reason.split(/[:(]/)[0].trim() : l.reason, 24)}`}
							</text>
						</g>
					);
				})}
				{PHASES.map((p) => {
					const g = h.agg[p];
					const isOut = p === 'outcome';
					const visited = !!g || isOut;
					const live = running && h.lastPhase === p;
					const lastV = g?.verdicts[g.verdicts.length - 1] ?? '';
					let stroke: string = visited ? T.text : T.line;
					if (g && lastV) stroke = p === 'repro' ? T.amber : BAD_VERDICTS.has(lastV) ? T.red : T.green;
					if (isOut) stroke = outColor;
					if (live) stroke = T.blue;
					const lines: string[] = [];
					if (isOut) {
						lines.push(running ? 'running…' : outcome || 'no result');
						lines.push(prNumber ? `PR #${prNumber}` : '');
					} else if (g) {
						if (p === 'repro') lines.push(g.verdicts.some((v) => v === 'failed') ? 'reproduced' : 'not reproduced', `${g.visits} run${g.visits > 1 ? 's' : ''}`);
						else if (p === 'investigate')
							lines.push(`${g.calls} call${g.calls === 1 ? '' : 's'}${g.errors ? ` · ${g.errors} err` : ''}`, [g.sql ? `${g.sql} SQL${g.sqlBlocked ? ` (${g.sqlBlocked} blocked)` : ''}` : '', g.guides.length ? `${g.guides.length} guide` : ''].filter(Boolean).join(' · '));
						else if (p === 'edit') lines.push(`${g.files.length} file${g.files.length === 1 ? '' : 's'} · ${g.calls} edit${g.calls === 1 ? '' : 's'}`, trunc(g.files.join(', '), 18));
						else lines.push(lastV || '–', '');
					} else lines.push('not reached', '');
					return (
						<g key={p} opacity={visited ? 1 : 0.55}>
							<title>
								{isOut
									? `Outcome: ${outcome || 'none'}`
									: g
										? `${PHASE_LABEL[p]}: ${g.visits} visit(s), ${g.actions} action(s), ${fmtDur(g.ms)}${g.guides.length ? `\nguides: ${g.guides.join(', ')}` : ''}${g.files.length ? `\nfiles: ${g.files.join(', ')}` : ''}\nverdicts: ${g.verdicts.join(' -> ') || '-'}`
										: `${PHASE_LABEL[p]}: not reached`}
							</title>
							<rect
								x={x(p)}
								y={top}
								width={nodeW}
								height={nodeH}
								rx={7}
								fill={isOut && visited && !running && outcome ? `${outColor}14` : T.panel}
								stroke={stroke}
								strokeWidth={live ? 2.4 : visited && (g || isOut) ? 1.6 : 1}
								strokeDasharray={visited ? undefined : '4 3'}
							>
								{live ? <animate attributeName="stroke-opacity" values="1;0.25;1" dur="1.4s" repeatCount="indefinite" /> : null}
							</rect>
							<text x={x(p) + 8} y={top + 15} fontSize={11} fontWeight={700} fill={visited ? T.text : T.faint}>
								{PHASE_LABEL[p]}
							</text>
							{g && g.visits > 1 ? (
								<text x={x(p) + nodeW - 7} y={top + 15} fontSize={9} textAnchor="end" fill={T.muted} fontWeight={700}>
									×{g.visits}
								</text>
							) : null}
							<text x={x(p) + 8} y={top + 30} fontSize={10} fill={isOut ? outColor : T.text} fontWeight={isOut ? 700 : 400}>
								{trunc(lines[0] ?? '', 19)}
							</text>
							{g && ['verify', 'submit', 'guard', 'approver'].includes(p) && g.verdicts.length ? (
								g.verdicts.slice(0, 8).map((v, i) => <circle key={i} cx={x(p) + 12 + i * 11} cy={top + 42} r={4} fill={vColor(v)}><title>{`visit ${i + 1}: ${v}`}</title></circle>)
							) : (
								<text x={x(p) + 8} y={top + 45} fontSize={9} fill={T.muted}>
									{trunc(lines[1] ?? '', 21)}
								</text>
							)}
							<text x={x(p) + 8} y={top + 60} fontSize={9} fill={T.muted}>
								{g ? fmtDur(g.ms) : ''}
							</text>
							{g
								? Object.entries(g.chips).map(([name, n], i) => (
										<g key={name}>
											<rect x={x(p) + 4} y={top + nodeH + 4 + i * 0} width={nodeW - 8} height={15} rx={7} fill="#eef4f9" stroke={T.blue} strokeWidth={0.8} />
											<text x={x(p) + nodeW / 2} y={top + nodeH + 15} fontSize={9} textAnchor="middle" fill={T.blue}>
												{trunc(`⧉ ${name}${n > 1 ? ` ×${n}` : ''}`, 18)}
											</text>
										</g>
									))
								: null}
						</g>
					);
				})}
			</svg>
			{fixLoops.length || revisits.length ? (
				<div style={{ fontSize: 11, marginTop: 4 }}>
					{fixLoops.map((l) => (
						<div key={l.idx} style={{ color: T.text, marginBottom: 2 }}>
							<b style={{ color: T.red }}>Loop {l.idx}</b> {PHASE_LABEL[l.from]} → {PHASE_LABEL[l.to]} (round {l.round} on this edge) · {clock(l.ts)} · <span style={{ color: T.muted }}>{l.reason}</span>
						</div>
					))}
					{revisits.length ? (
						<div style={{ color: T.faint }}>
							Revisits (not fix loops): {revisits.map((l) => `loop ${l.idx} ${PHASE_LABEL[l.from]} → ${PHASE_LABEL[l.to]} (${l.reason})`).join(', ')}
						</div>
					) : null}
				</div>
			) : (
				<div style={{ fontSize: 11, color: T.muted, marginTop: 4 }}>No loops: every phase passed on its first visit.</div>
			)}
		</div>
	);
}

function StepsTable({ h }: { h: Healing }) {
	return (
		<details style={{ marginTop: 6 }}>
			<summary style={{ fontSize: 11, color: T.blue, cursor: 'pointer' }}>
				{h.steps.length} phase steps, {h.steps.reduce((a, s) => a + s.actions.length, 0)} actions
			</summary>
			<div style={{ maxHeight: 320, overflowY: 'auto', marginTop: 4 }}>
				<table style={{ width: '100%', borderCollapse: 'collapse' }}>
					<thead>
						<tr>
							<th style={th}>Step</th>
							<th style={th}>Seq</th>
							<th style={th}>Time</th>
							<th style={th}>Kind</th>
							<th style={th}>Verdict</th>
							<th style={th}>Command</th>
						</tr>
					</thead>
					<tbody>
						{h.steps.flatMap((st, i) =>
							st.actions.map((a, j) => (
								<tr key={`${i}-${a.seq}`}>
									<td style={{ ...td, fontWeight: 700, whiteSpace: 'nowrap', borderTop: j === 0 ? `1px solid ${T.line}` : undefined }}>{j === 0 ? `${i + 1}. ${PHASE_LABEL[st.phase]}` : ''}</td>
									<td style={{ ...td, color: T.muted }}>{a.seq}</td>
									<td style={{ ...td, color: T.muted, whiteSpace: 'nowrap' }}>{clock(a.ts)}</td>
									<td style={td}>{a.kind}</td>
									<td style={{ ...td, color: BAD_VERDICTS.has(a.verdict) ? T.red : T.text }}>{a.verdict}</td>
									<td style={{ ...td, fontFamily: T.mono, fontSize: 10, wordBreak: 'break-all' }} title={a.output || a.feedback || ''}>
										{trunc(a.command, 140)}
										{a.feedback ? <div style={{ color: T.muted, fontFamily: T.font }}>{a.feedback}</div> : null}
									</td>
								</tr>
							)),
						)}
					</tbody>
				</table>
			</div>
		</details>
	);
}

// ---------- pipeline DAG ----------
type RunRow = {
	component: string;
	kind: string;
	run: number;
	status: string;
	scheduled: boolean;
	created: number | null;
	started: number | null;
	ended: number | null;
	sha: string;
	incident: number | null;
	incExists: boolean;
	fixerRunId: string;
	mode: string;
	attempt: string;
	outcome: string;
};

function Sparkline({ runs, x, y }: { runs: RunRow[]; x: number; y: number }) {
	return (
		<g>
			{runs.map((r, i) => (
				<rect key={r.run} x={x + i * 9} y={y} width={7} height={9} rx={1.5} fill={runColor(r.status)}>
					<title>{`run ${r.run}: ${r.status} ${stamp(r.started ?? r.created)}`}</title>
				</rect>
			))}
		</g>
	);
}

function PipelineDag({
	reg,
	runsBy,
	now,
	onOpen,
	onIncident,
}: {
	reg: Row[];
	runsBy: Record<string, RunRow[]>;
	now: number;
	onOpen: (c: string) => void;
	onIncident: (id: number) => void;
}) {
	const pipes = reg.filter((r) => S(r.kind) === 'pipeline');
	const healers = reg.filter((r) => S(r.kind) !== 'pipeline');
	const names = new Set(pipes.map((r) => S(r.name)));
	const up: Record<string, string[]> = Object.fromEntries(pipes.map((r) => [S(r.name), S(r.upstream).split(',').filter((u) => u && names.has(u))]));
	const depth: Record<string, number> = {};
	const dep = (n: string, seen: Set<string>): number => {
		if (depth[n] != null) return depth[n];
		if (seen.has(n)) return 0;
		seen.add(n);
		const d = up[n].length ? 1 + Math.max(...up[n].map((u) => dep(u, seen))) : 0;
		depth[n] = d;
		return d;
	};
	pipes.forEach((r) => dep(S(r.name), new Set()));
	const maxD = Math.max(0, ...Object.values(depth));
	const cols: string[][] = Array.from({ length: maxD + 1 }, () => []);
	pipes.forEach((r) => cols[depth[S(r.name)]].push(S(r.name)));
	const nodeW = 210;
	const nodeH = 62;
	const gapX = 90;
	const gapY = 12;
	const maxRows = Math.max(1, ...cols.map((c) => c.length));
	const pipeH = maxRows * (nodeH + gapY);
	const pos: Record<string, { x: number; y: number }> = {};
	cols.forEach((c, ci) => {
		const offset = ((maxRows - c.length) * (nodeH + gapY)) / 2;
		c.forEach((n, ri) => (pos[n] = { x: 4 + ci * (nodeW + gapX), y: 4 + offset + ri * (nodeH + gapY) }));
	});
	const activeOf = (r: Row) =>
		S(r.active)
			.split(';')
			.filter(Boolean)
			.map((s) => {
				const [id, status, category, root] = s.split('|');
				return { id: Number(id), status, category, root: root === 'true' };
			});
	const actBy: Record<string, ReturnType<typeof activeOf>> = Object.fromEntries(reg.map((r) => [S(r.name), activeOf(r)]));
	const laneY = pipeH + 34;
	const hW = 176;
	const hH = 46;
	const hOrder = ['watchdog', 'checker', 'fixer'].filter((n) => healers.some((r) => S(r.name) === n));
	healers.forEach((r) => !hOrder.includes(S(r.name)) && hOrder.push(S(r.name)));
	const hPos: Record<string, { x: number; y: number }> = Object.fromEntries(hOrder.map((n, i) => [n, { x: 4 + i * (hW + 70), y: laneY + 18 }]));
	const W = Math.max(4 + (maxD + 1) * (nodeW + gapX) - gapX + 8, 4 + hOrder.length * (hW + 70));
	const Hh = laneY + 18 + hH + 8;
	const healerEdges: [string, string, string][] = (
		[
			['watchdog', 'checker', 'keeps alive'],
			['checker', 'fixer', 'dispatches'],
		] as [string, string, string][]
	).filter(([a, b]) => hOrder.includes(a) && hOrder.includes(b));
	const node = (name: string, x: number, y: number, w: number, hgt: number, small: boolean) => {
		const runs = runsBy[name] ?? [];
		const last = runs[runs.length - 1];
		const act = actBy[name] ?? [];
		const badge = act[0];
		return (
			<g key={name} style={{ cursor: 'pointer' }} onClick={() => onOpen(name)}>
				<title>{`${name}\n${S(reg.find((r) => S(r.name) === name)?.description)}\nclick to open its runs`}</title>
				<rect x={x} y={y} width={w} height={hgt} rx={6} fill={T.panel} stroke={badge ? INC_STYLE[badge.status]?.c ?? T.red : T.line} strokeWidth={badge ? 1.8 : 1} />
				<rect x={x} y={y} width={4} height={hgt} rx={2} fill={last ? runColor(last.status) : T.gray} />
				<text x={x + 12} y={y + 16} fontSize={12} fontWeight={700} fill={T.text}>
					{name}
				</text>
				<text x={x + 12} y={y + 30} fontSize={10} fill={last ? runColor(last.status) : T.muted}>
					{trunc(last ? `${last.status.toLowerCase()} · run ${last.run} · ${ago(last.ended ?? last.started ?? last.created, now)}` : 'no runs in snapshot', Math.floor((w - 16) / 4.9))}
				</text>
				{!small ? <Sparkline runs={runs.slice(-15)} x={x + 12} y={y + 40} /> : null}
				{badge ? (
					<g
						onClick={(e) => {
							e.stopPropagation();
							onIncident(badge.id);
						}}
					>
						<title>{`incident #${badge.id}: ${badge.status} (${badge.category})${badge.root ? ' - root' : ' - downstream symptom'}${act.length > 1 ? `\n+${act.length - 1} more` : ''}`}</title>
						<rect x={x + w - 92} y={y + 5} width={86} height={15} rx={7} fill={badge.root ? INC_STYLE[badge.status]?.c ?? T.red : T.panel} stroke={INC_STYLE[badge.status]?.c ?? T.red} />
						<text x={x + w - 49} y={y + 16} fontSize={9} fontWeight={700} textAnchor="middle" fill={badge.root ? '#fff' : INC_STYLE[badge.status]?.c ?? T.red}>
							{trunc(`#${badge.id} ${badge.status}`, 17)}
						</text>
						<text x={x + w - 6} y={y + 30} fontSize={9} textAnchor="end" fill={T.muted}>
							{trunc(badge.category, 22)}
						</text>
					</g>
				) : null}
			</g>
		);
	};
	return (
		<div style={{ overflowX: 'auto' }}>
			<svg viewBox={`0 0 ${W} ${Hh}`} style={{ width: '100%', maxWidth: W * 1.25, minWidth: Math.min(W, 560), display: 'block', fontFamily: T.font }}>
				<defs>
					<marker id="pd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
						<path d="M 0 0 L 10 5 L 0 10 z" fill={T.faint} />
					</marker>
					<marker id="pd-arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
						<path d="M 0 0 L 10 5 L 0 10 z" fill={T.red} />
					</marker>
				</defs>
				{pipes.flatMap((r) =>
					up[S(r.name)].map((u) => {
						const a = pos[u];
						const b = pos[S(r.name)];
						const cascade = (actBy[u] ?? []).some((i) => i.root && (actBy[S(r.name)] ?? []).some((j) => j.id === i.id));
						const x1 = a.x + nodeW;
						const y1 = a.y + nodeH / 2;
						const x2 = b.x - 2;
						const y2 = b.y + nodeH / 2;
						return (
							<path
								key={`${u}-${S(r.name)}`}
								d={`M ${x1} ${y1} C ${x1 + gapX / 2} ${y1}, ${x2 - gapX / 2} ${y2}, ${x2} ${y2}`}
								fill="none"
								stroke={cascade ? T.red : T.faint}
								strokeWidth={cascade ? 1.8 : 1.1}
								markerEnd={cascade ? 'url(#pd-arrow-red)' : 'url(#pd-arrow)'}
							/>
						);
					}),
				)}
				{pipes.map((r) => node(S(r.name), pos[S(r.name)].x, pos[S(r.name)].y, nodeW, nodeH, false))}
				<line x1={0} x2={W} y1={laneY} y2={laneY} stroke={T.grid} />
				<text x={4} y={laneY + 12} fontSize={9} fontWeight={700} fill={T.muted} letterSpacing="0.05em">
					HEALER LANE
				</text>
				{healerEdges
					.filter(([a, b]) => hPos[a] && hPos[b])
					.map(([a, b, label]) => (
						<g key={`${a}-${b}`}>
							<line x1={hPos[a].x + hW} y1={hPos[a].y + hH / 2} x2={hPos[b].x - 2} y2={hPos[b].y + hH / 2} stroke={T.faint} markerEnd="url(#pd-arrow)" />
							<text x={(hPos[a].x + hW + hPos[b].x) / 2} y={hPos[a].y + hH / 2 - 5} fontSize={9} textAnchor="middle" fill={T.muted}>
								{label}
							</text>
						</g>
					))}
				{hOrder.map((n) => node(n, hPos[n].x, hPos[n].y, hW, hH, true))}
			</svg>
		</div>
	);
}

// ---------- Gantt ----------
const RANGES: Record<string, number | null> = { '1h': 3600e3, '6h': 6 * 3600e3, '24h': 86400e3, '7d': 7 * 86400e3, all: null };
const TICK_STEPS = [60e3, 120e3, 300e3, 600e3, 900e3, 1800e3, 3600e3, 7200e3, 10800e3, 21600e3, 43200e3, 86400e3, 172800e3];
function Gantt({
	runs,
	order,
	now,
	range,
	selected,
	onSelect,
}: {
	runs: RunRow[];
	order: string[];
	now: number;
	range: string;
	selected: string;
	onSelect: (r: RunRow) => void;
}) {
	const [ref, width] = useWidth<HTMLDivElement>();
	const span = RANGES[range];
	const starts = runs.map((r) => r.started ?? r.created ?? now);
	const t1 = now;
	const t0 = span ? now - span : Math.min(now - 600e3, ...starts) - Math.max(60e3, (now - Math.min(now, ...starts)) * 0.02);
	const labelW = 118;
	const rowH = 22;
	const W = Math.max(width, 480);
	const plotW = W - labelW - 10;
	const sx = (t: number) => labelW + ((t - t0) / (t1 - t0)) * plotW;
	const step = TICK_STEPS.find((s) => (t1 - t0) / s <= 8) ?? 7 * 86400e3;
	const ticks: number[] = [];
	const tz = new Date().getTimezoneOffset() * 60e3;
	for (let t = Math.ceil((t0 - tz) / step) * step + tz; t <= t1; t += step) ticks.push(t);
	const Hh = 20 + order.length * rowH + 4;
	const fmtTick = (t: number) => (step >= 86400e3 ? new Date(t).toLocaleDateString([], { month: 'short', day: 'numeric' }) : clock(t).slice(0, 5));
	return (
		<div ref={ref} style={{ width: '100%', overflowX: 'auto' }}>
			<svg width={W} height={Hh} style={{ display: 'block', fontFamily: T.font }}>
				{ticks.map((t) => (
					<g key={t}>
						<line x1={sx(t)} x2={sx(t)} y1={16} y2={Hh} stroke={T.grid} />
						<text x={sx(t)} y={11} fontSize={9} textAnchor="middle" fill={T.muted}>
							{fmtTick(t)}
						</text>
					</g>
				))}
				<line x1={sx(now)} x2={sx(now)} y1={14} y2={Hh} stroke={T.blue} strokeDasharray="2 2" />
				{order.map((c, i) => (
					<g key={c}>
						<rect x={0} y={18 + i * rowH} width={W} height={rowH} fill={i % 2 ? 'transparent' : '#f3f3f3'} />
						<text x={4} y={18 + i * rowH + 15} fontSize={11} fill={T.text} fontWeight={600}>
							{c}
						</text>
					</g>
				))}
				{runs.map((r) => {
					const i = order.indexOf(r.component);
					if (i < 0) return null;
					const a = r.started ?? r.created ?? now;
					const b = r.ended ?? (r.status === 'RUNNING' ? now : a);
					if (b < t0 || a > t1) return null;
					const x1 = Math.max(labelW, sx(a));
					const x2 = Math.min(labelW + plotW, sx(b));
					const key = `${r.component}#${r.run}`;
					return (
						<rect
							key={key}
							x={x1}
							y={18 + i * rowH + 4}
							width={Math.max(4, x2 - x1)}
							height={rowH - 8}
							rx={2}
							fill={runColor(r.status)}
							stroke={selected === key ? T.text : 'none'}
							strokeWidth={2}
							style={{ cursor: 'pointer' }}
							onClick={() => onSelect(r)}
						>
							<title>{`${r.component} run ${r.run}: ${r.status}\n${stamp(a)} · ${fmtDur((r.ended ?? (r.status === 'RUNNING' ? now : null) ?? a) - a)}${r.incident ? `\nincident #${r.incident}${r.incExists ? '' : ' (not in table)'}` : ''}${r.sha ? `\nsha ${r.sha.slice(0, 12)}` : ''}`}</title>
						</rect>
					);
				})}
			</svg>
		</div>
	);
}

// ---------- views ----------
function HealthStrip({ q, now }: { q: ReturnType<typeof useSQLQuery>; now: number }) {
	const r = rowsOf(q)[0] ?? {};
	const hbMs = NN(r.checker_ms);
	const hbStale = hbMs == null || now - hbMs > 2 * HB_EXPECTED_MIN * 60e3;
	// The kill switch is the healer's schedule: pause it (and cancel a running run) to stop everything.
	const schedQ = useSQLQuery(`SELECT schedule_cron FROM MD_GET_FLIGHT(flight_id := '${HEALER_FLIGHT_ID}'::UUID)`, { enabled: DEPLOYED });
	const cron = S(rowsOf(schedQ)[0]?.schedule_cron);
	const sched = !DEPLOYED || schedQ.isError ? '' : schedQ.isLoading ? '…' : cron ? `healer schedule on (${cron})` : 'healer schedule off: paused';
	const auto = S(r.autonomy);
	const tiles: { label: string; value: string; color?: string; sub?: string }[] = [
		{ label: 'Open', value: String(N(r.n_open)), color: N(r.n_open) ? T.red : undefined, sub: `${N(r.n_inv)} being fixed now` },
		{ label: 'Waiting for approval', value: String(N(r.n_pending)), color: N(r.n_pending) ? T.purple : undefined, sub: auto === 'L1' ? 'autonomy L1: fixes ship on their own' : 'autonomy L3: a human approves each fix' },
		{ label: 'Verifying', value: String(N(r.n_ver)), color: N(r.n_ver) ? T.teal : undefined, sub: `${N(r.n_res)} resolved of ${N(r.n_all)}` },
		{ label: 'Escalated', value: String(N(r.n_esc)), color: N(r.n_esc) ? T.orange : undefined, sub: 'need a human (Retry in the incident)' },
		{ label: 'Healer heartbeat', value: hbMs == null ? 'no heartbeat' : ago(hbMs, now), color: hbStale ? T.red : T.green, sub: hbStale ? `silent for over ${2 * HB_EXPECTED_MIN} min: is the schedule on?` : 'runs every 5 min; checks every minute while fixing' },
		{ label: 'Agent accounts busy', value: `${N(r.n_agents_busy)} / ${N(r.n_agents) || '–'}`, color: N(r.n_agents_busy) ? T.blue : undefined, sub: 'one fix per service account' },
		{ label: 'Alerts, last 24 h', value: String(N(r.n_alerts)), color: N(r.n_alerts) ? T.red : T.green, sub: sched || undefined },
		{ label: 'Fixer spend today', value: usd(N(r.spend_today)), sub: r.spend_cap ? `cap $${S(r.spend_cap)} (UTC day)` : 'UTC day' },
	];
	return (
		<div className="grid grid-cols-4" style={{ gap: '10px 16px' }}>
			{tiles.map((t) => (
				<div key={t.label} style={{ borderLeft: `3px solid ${t.color ?? T.line}`, paddingLeft: 8 }}>
					{q.isLoading && !q.data ? <Skeleton h={22} w={60} /> : <div style={{ fontSize: 20, fontWeight: 700, color: t.color ?? T.text, lineHeight: '24px' }}>{t.value}</div>}
					<div style={{ fontSize: 11, fontWeight: 700, color: T.text }}>{t.label}</div>
					{t.sub ? <div style={{ fontSize: 10, color: T.muted }}>{t.sub}</div> : null}
				</div>
			))}
		</div>
	);
}

function IncidentTable({ rows, selected, onSelect, now, limit }: { rows: Row[]; selected: number; onSelect: (id: number) => void; now: number; limit?: number }) {
	const list = limit ? rows.slice(0, limit) : rows;
	return (
		<div style={{ overflowX: 'auto' }}>
			<table style={{ width: '100%', borderCollapse: 'collapse' }}>
				<thead>
					<tr>
						{['#', 'Status', 'Incident', 'Category', 'Pipeline', 'Ev', 'Att', 'Cost', 'Fix', 'Created'].map((h) => (
							<th key={h} style={th}>
								{h}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{list.map((r) => {
						const id = N(r.id);
						const on = id === selected;
						return (
							<tr key={id} onClick={() => onSelect(id)} style={{ cursor: 'pointer', background: on ? '#e9f2f8' : undefined }}>
								<td style={{ ...td, fontWeight: 700 }}>{id}</td>
								<td style={td}>
									<IncPill status={S(r.status)} />
								</td>
								<td style={{ ...td, maxWidth: 260 }}>
									<div style={{ fontWeight: 600 }}>{S(r.class) || '–'}</div>
									<div style={{ fontSize: 11, color: T.muted }} title={S(r.title)}>
										{trunc(S(r.title), 80)}
									</div>
								</td>
								<td style={{ ...td, fontSize: 11 }}>{S(r.category) || '–'}</td>
								<td style={{ ...td, fontSize: 11 }}>
									<b>{S(r.pipeline)}</b>
								</td>
								<td style={td}>{N(r.events)}</td>
								<td style={td}>{N(r.attempts)}</td>
								<td style={{ ...td, whiteSpace: 'nowrap' }}>{usd(N(r.cost))}</td>
								<td style={{ ...td, whiteSpace: 'nowrap', fontSize: 11 }} onClick={(e) => e.stopPropagation()}>
									{NN(r.fixed_version) ? <span style={{ fontFamily: T.mono }}>v{N(r.fixed_version)}</span> : <span style={{ color: T.faint }}>–</span>}
								</td>
								<td style={{ ...td, whiteSpace: 'nowrap', fontSize: 11, color: T.muted }} title={stamp(NN(r.created_ms))}>
									{ago(NN(r.created_ms), now)}
								</td>
							</tr>
						);
					})}
				</tbody>
			</table>
		</div>
	);
}

function Lifecycle({ rows }: { rows: Row[] }) {
	if (!rows.length) return <Empty>No transitions recorded.</Empty>;
	const t0 = N(rows[0].ts_ms);
	return (
		<div className="flex" style={{ overflowX: 'auto', paddingBottom: 4 }}>
			{rows.map((r, i) => {
				const st = INC_STYLE[S(r.to_status)] ?? { c: T.gray };
				return (
					<div key={i} className="flex" style={{ alignItems: 'flex-start', minWidth: 0 }}>
						{i > 0 ? <div style={{ width: 22, height: 1, background: T.faint, marginTop: 8, flexShrink: 0 }} /> : null}
						<div style={{ width: 132, flexShrink: 0 }} title={S(r.reason)}>
							<IncPill status={S(r.to_status)} />
							<div style={{ fontSize: 10, color: T.muted, marginTop: 3 }}>
								{clock(N(r.ts_ms))} {i > 0 ? `(+${fmtDur(N(r.ts_ms) - t0)})` : ''}
							</div>
							<div style={{ fontSize: 10, color: st.c, fontWeight: 700 }}>{S(r.actor)}</div>
							<div style={{ fontSize: 10, color: T.text, lineHeight: '13px' }}>{trunc(S(r.reason), 90)}</div>
						</div>
					</div>
				);
			})}
		</div>
	);
}

function EventGroups({ rows, now }: { rows: Row[]; now: number }) {
	if (!rows.length) return <Empty>No events attached.</Empty>;
	const groups: { key: string; label: string; match: (a: string) => boolean }[] = [
		{ key: 'root', label: 'Root', match: (a) => a === 'new' },
		{ key: 'lineage', label: 'Lineage symptoms (downstream)', match: (a) => a === 'lineage' },
		{ key: 'repeat', label: 'Repeats (same fingerprint)', match: (a) => a === 'fingerprint' },
		{ key: 'other', label: 'Grouped by resource / similarity / regression', match: (a) => !['new', 'lineage', 'fingerprint'].includes(a) },
	];
	return (
		<div className="flex flex-col" style={{ gap: 8 }}>
			{groups.map((g) => {
				const evs = rows.filter((r) => g.match(S(r.attach_reason)));
				if (!evs.length) return null;
				return (
					<div key={g.key}>
						<div style={{ fontSize: 10, fontWeight: 700, color: T.muted, textTransform: 'uppercase', marginBottom: 2 }}>
							{g.label} · {evs.length}
						</div>
						{evs.slice(0, 12).map((e) => (
							<div key={N(e.event_id)} style={{ fontSize: 11, padding: '3px 0', borderBottom: `1px solid ${T.grid}`, marginLeft: g.key === 'root' ? 0 : 12 }}>
								<b>{S(e.pipeline)}</b> run {N(e.run_number)} · <span style={{ color: T.red }}>{S(e.error_class)}</span> · <span style={{ color: T.muted }}>{ago(N(e.ts_ms), now)}</span>
								{g.key === 'other' ? <span style={{ color: T.muted }}> · via {S(e.attach_reason)}</span> : null}
								<div style={{ fontFamily: T.mono, fontSize: 10, color: T.text }}>{trunc(S(e.norm_msg), 220)}</div>
								{S(e.frames) ? <div style={{ fontFamily: T.mono, fontSize: 9, color: T.muted }}>{trunc(S(e.frames), 160)}</div> : null}
							</div>
						))}
						{evs.length > 12 ? <div style={{ fontSize: 10, color: T.muted }}>+{evs.length - 12} more</div> : null}
					</div>
				);
			})}
		</div>
	);
}

function toActions(rows: Row[]): Action[] {
	return rows.map((r) => ({
		seq: N(r.seq),
		ts: N(r.ts_ms),
		kind: S(r.kind),
		command: S(r.command),
		verdict: S(r.verdict),
		feedback: S(r.feedback) || undefined,
		risk: S(r.risk) || undefined,
		violations: S(r.violations) || undefined,
		l2_blocks: S(r.l2_blocks) || undefined,
		errors: S(r.errors) || undefined,
		exits: S(r.exits) || undefined,
		output: S(r.output) || undefined,
	}));
}

function AttemptCard({
	fr,
	actions,
	example,
}: {
	fr: Row;
	actions: Action[];
	example?: boolean;
}) {
	const id = S(fr.fixer_run_id);
	// An attempt with an outcome row is finished, even if the runs snapshot still shows its healer run as running.
	const running = !NN(fr.ended_ms) && ['RUNNING', 'PENDING'].includes(S(fr.flight_status));
	const endMs = NN(fr.ended_ms);
	const h = useMemo(() => buildHealing(actions, endMs), [actions, endMs]);
	const outcome = S(fr.outcome);
	const total = endMs != null && NN(fr.started_ms) != null ? endMs - N(fr.started_ms) : null;
	const fixLoops = h.loops.filter((l) => l.fix).length;
	const pr = NN(fr.pr_number);
	return (
		<div style={{ border: `1px solid ${example ? T.orange : T.line}`, borderRadius: 6, padding: 10, background: T.panel, marginBottom: 10 }}>
			{example ? (
				<div style={{ fontSize: 11, fontWeight: 700, color: T.orange, marginBottom: 6 }}>
					EXAMPLE, hard-coded sequence, not real data: shows how verify and approver loops render.
				</div>
			) : null}
			<div className="flex flex-wrap items-baseline" style={{ gap: 10, marginBottom: 6, fontSize: 12 }}>
				<b>Attempt {NN(fr.attempt) ?? '?'}</b>
				<span style={{ fontFamily: T.mono, fontSize: 11, color: T.muted }}>{id}</span>
				{S(fr.level) ? <Pill label={S(fr.level)} color={T.slate} outline /> : null}
				{running ? <Pill label="running" color={T.blue} /> : outcome ? <Pill label={outcome} color={OUTCOME_COLOR[outcome] ?? T.red} /> : null}
				<span style={{ color: T.muted }}>
					{fmtDur(total)} · {usd(N(fr.cost))} · approver rounds {NN(fr.approver_rounds) ?? '–'} · {fixLoops} fix loop{fixLoops === 1 ? '' : 's'}
				</span>
				{S(fr.branch) && !pr ? <span style={{ fontSize: 11, color: T.muted }}>{S(fr.branch)}</span> : null}
				{S(fr.model) ? (
					<span style={{ fontSize: 10, color: T.faint }}>
						{S(fr.model).replace(/^~/, '')}
						{S(fr.approver_model) ? ` / approver ${S(fr.approver_model).replace(/^~/, '')}` : ''}
					</span>
				) : null}
			</div>
			{actions.length ? (
				<>
					<HealingDag h={h} uid={`hd-${id.replace(/[^a-zA-Z0-9]/g, '')}`} outcome={outcome} running={running} prNumber={pr} />
					<StepsTable h={h} />
				</>
			) : (
				<Empty>{running ? 'Fixer run is starting: no actions recorded yet.' : 'No actions recorded for this fixer run.'}</Empty>
			)}
		</div>
	);
}

const EXAMPLE_FR: Row = {
	fixer_run_id: 'example-a1',
	attempt: 1,
	level: 'L1',
	outcome: 'merged',
	cost: 0.61,
	approver_rounds: 2,
	started_ms: EXAMPLE_T0,
	ended_ms: EXAMPLE_T0 + 180e3,
	flight_status: 'SUCCEEDED',
	model: 'example-model',
	branch: 'example',
};

function SummaryBlock({ fr }: { fr: Row }) {
	const parts: [string, string][] = [
		['Symptoms', S(fr.symptoms)],
		['Root cause', S(fr.root_cause)],
		['Fix', S(fr.fix)],
		['Verification', S(fr.verification)],
		['Approver notes', S(fr.approver_notes)],
	];
	const present = parts.filter(([, v]) => v);
	if (!present.length) return null;
	return (
		<div style={{ fontSize: 12, lineHeight: '17px' }}>
			<div style={{ fontSize: 11, fontWeight: 700, color: T.muted, marginBottom: 4 }}>Attempt {NN(fr.attempt) ?? '?'} summary</div>
			{present.map(([k, v]) => (
				<div key={k} style={{ marginBottom: 6 }}>
					<b>{k}.</b> <span style={{ whiteSpace: 'pre-wrap' }}>{v.replace(/```[\s\S]*?```/g, '').trim()}</span>
				</div>
			))}
		</div>
	);
}

// ---------- actions: buttons that run the healer Flight ----------
const DEPLOYED = !HEALER_FLIGHT_ID.startsWith('__');
function HealerButton({ label, config, onDone, kind = 'normal' }: { label: string; config: Record<string, string>; onDone?: () => void; kind?: 'primary' | 'danger' | 'normal' }) {
	const start = useRunFlight(HEALER_FLIGHT_ID);
	const latest = useFlightRun(HEALER_FLIGHT_ID, start.data?.runNumber, {
		onFinished: (run: { status?: string }) => {
			if (run.status === 'SUCCEEDED') onDone?.();
		},
	});
	const busy = start.isPending || latest.isChecking;
	const color = kind === 'primary' ? T.green : kind === 'danger' ? T.red : T.blue;
	if (!DEPLOYED) return <span style={{ fontSize: 10, color: T.muted }}>({label}: deploy with deploy.py to enable)</span>;
	return (
		<span className="inline-flex flex-wrap items-center" style={{ gap: 6 }}>
			<button
				disabled={busy}
				onClick={() => start.run(config)}
				style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 4, cursor: busy ? 'wait' : 'pointer', border: `1px solid ${color}`, background: busy ? T.grid : color, color: busy ? T.muted : '#fff' }}
			>
				{busy ? `${label}…` : label}
			</button>
			{latest.status ? (
				<span style={{ fontSize: 10, color: latest.isFinished ? (latest.status === 'SUCCEEDED' ? T.green : T.red) : T.muted }}>
					{latest.isFinished ? (latest.status === 'SUCCEEDED' ? 'done' : `healer run ${S(latest.status).toLowerCase()}`) : S(latest.status).toLowerCase()}
				</span>
			) : null}
			{start.error ? <span style={{ fontSize: 10, color: T.red }}>Could not start it: {start.error.message}. Only the healer's owner, or someone given run access, can.</span> : null}
			{latest.error ? <span style={{ fontSize: 10, color: T.red }}>Could not check the run: {latest.error.message}</span> : null}
		</span>
	);
}

const SEV_COLOR: Record<string, string> = { error: T.red, warn: T.orange, info: T.blue };
function AlertsPanel({ now, onIncident }: { now: number; onIncident: (id: number) => void }) {
	const q = useSQLQuery(ALERTS_SQL);
	const [all, setAll] = useState(false);
	const rows = rowsOf(q);
	const recent = rows.filter((r) => now - N(r.ts_ms) < 86400e3);
	const list = all ? rows : recent.slice(0, 5);
	return (
		<Section>
			<SectionTitle
				note={
					<span>
						heal.main.log (kind alert) · also posted to the heal_webhook secret's URL when set ·{' '}
						<span style={linkStyle} onClick={() => setAll(!all)}>
							{all ? 'last 24 h only' : `all ${rows.length} from 7 days`}
						</span>
					</span>
				}
			>
				Alerts
			</SectionTitle>
			<div style={{ marginBottom: 6 }}>
				<HealerButton label="Send a test alert" config={{ ACTION: 'test_alert' }} onDone={() => q.refetch()} />
			</div>
			{q.isLoading && !q.data ? (
				<Skeleton h={40} />
			) : q.isError ? (
				<ErrorLine error={q.error} />
			) : !list.length ? (
				<Empty>No alerts in the last 24 hours. Stuck or crashed healer runs, escalations and fixes waiting for approval show up here.</Empty>
			) : (
				<table style={{ width: '100%', borderCollapse: 'collapse' }}>
					<tbody>
						{list.map((r) => (
							<tr key={N(r.id)}>
								<td style={{ ...td, width: 90, whiteSpace: 'nowrap', fontSize: 11, color: T.muted }} title={stamp(NN(r.ts_ms))}>
									{ago(NN(r.ts_ms), now)}
								</td>
								<td style={{ ...td, width: 120 }}>
									<Pill label={S(r.kind).replace('_', ' ')} color={SEV_COLOR[S(r.severity)] ?? T.gray} />
								</td>
								<td style={{ ...td, fontSize: 11 }}>
									{S(r.message)}
									{NN(r.incident_id) ? (
										<span style={{ ...linkStyle, marginLeft: 6 }} onClick={() => onIncident(N(r.incident_id))}>
											#{N(r.incident_id)}
										</span>
									) : null}
								</td>
								<td style={{ ...td, width: 90, fontSize: 10, color: T.faint }} title="webhook delivery">
									{S(r.webhook_status)}
								</td>
							</tr>
						))}
					</tbody>
				</table>
			)}
		</Section>
	);
}

function DiffBlock({ diff }: { diff: string }) {
	return (
		<pre style={{ fontFamily: T.mono, fontSize: 10.5, lineHeight: '14px', background: T.panel, border: `1px solid ${T.line}`, borderRadius: 4, padding: 8, overflowX: 'auto', maxHeight: 360, margin: '6px 0 0' }}>
			{diff.split('\n').map((l, i) => (
				<div key={i} style={{ color: l.startsWith('+') && !l.startsWith('+++') ? T.green : l.startsWith('-') && !l.startsWith('---') ? T.red : l.startsWith('@@') ? T.purple : T.text }}>
					{l || ' '}
				</div>
			))}
		</pre>
	);
}

const PROP_COLOR: Record<string, string> = { pending: T.purple, deciding: T.blue, shipped: T.green, rejected: T.gray, stale: T.orange };
function ProposalCard({ p, now, onDone, onIncident }: { p: Row; now: number; onDone: () => void; onIncident: (id: number) => void }) {
	const pending = S(p.status) === 'pending';
	const drift = parseList(p.drift);
	return (
		<div style={{ borderLeft: `3px solid ${PROP_COLOR[S(p.status)] ?? T.line}`, paddingLeft: 10, marginBottom: 16 }}>
			<div className="flex flex-wrap items-center" style={{ gap: 8 }}>
				<b style={{ fontSize: 13 }}>Proposal {N(p.incident_id)}:{N(p.id)}</b>
				<Pill label={S(p.status)} color={PROP_COLOR[S(p.status)] ?? T.gray} />
				<span style={{ fontSize: 11, color: T.muted }}>
					{S(p.pipeline)} · built on Flight version {N(p.base_version)} · {ago(NN(p.created_ms), now)}
					{NN(p.shipped_version) ? ` · shipped as v${N(p.shipped_version)}` : ''}
				</span>
				<span style={{ ...linkStyle, fontSize: 11 }} onClick={() => onIncident(N(p.incident_id))}>
					incident #{N(p.incident_id)}
				</span>
			</div>
			<div style={{ fontSize: 12, marginTop: 2 }}>{S(p.title)}</div>
			{S(p.note) ? <div style={{ fontSize: 11, color: T.muted }}>{S(p.note)}</div> : null}
			{pending && parseList(p.risk).length ? (
				<div style={{ fontSize: 11, color: T.red, marginTop: 4 }}>
					<b>Needs a human whatever the autonomy:</b> the fix adds {parseList(p.risk).join(', ')}. Check that each is needed.
				</div>
			) : null}
			{pending && parseList(p.unseen).length ? (
				<div style={{ fontSize: 11, color: T.orange, marginTop: 4 }}>
					<b>Failures the agent did not see:</b> {parseList(p.unseen).map((u) => trunc(u, 160)).join(' · ')}
				</div>
			) : null}
			{pending ? (
				<>
					<div className="grid grid-cols-2" style={{ gap: '4px 20px', fontSize: 11, lineHeight: '16px', marginTop: 6 }}>
						<div>
							<b>Root cause:</b> {S(p.root_cause) || '–'}
						</div>
						<div>
							<b>Fix:</b> {S(p.fix) || '–'}
						</div>
						<div>
							<b>Tests:</b> {S(p.tests) || '–'} · <b>independent test reviewer:</b> <span style={{ color: S(p.test_verdict) === 'approve' ? T.green : T.red }}>{S(p.test_verdict) || '–'}</span>
							{S(p.test_notes) ? <span style={{ color: T.muted }}> ({trunc(S(p.test_notes), 220)})</span> : null}
						</div>
						<div>
							<b>Approver:</b> {trunc(S(p.approver_notes), 260) || '–'}
						</div>
						<div>
							<b>Healer checks passed:</b> {parseList(p.checks).join(' · ') || '–'}
						</div>
						<div>
							<b>Output drift vs production:</b> {drift.length ? <span style={{ color: T.orange }}>{drift.join('; ')}</span> : 'none beyond tolerance'}
						</div>
					</div>
					<DiffBlock diff={S(p.diff)} />
					<div className="flex flex-wrap items-center" style={{ gap: 12, marginTop: 8 }}>
						<HealerButton label="Approve and ship" kind="primary" config={{ ACTION: 'approve', INCIDENT_ID: `${N(p.incident_id)}:${N(p.id)}` }} onDone={onDone} />
						<HealerButton label="Reject" kind="danger" config={{ ACTION: 'reject', INCIDENT_ID: `${N(p.incident_id)}:${N(p.id)}` }} onDone={onDone} />
						<span style={{ fontSize: 10, color: T.muted }}>If the pipeline changed since this fix was tested, Approve does not ship it: the healer redoes it on the new version, runs every check and review again, and proposes it again.</span>
					</div>
				</>
			) : null}
		</div>
	);
}

function ApprovalsView({ now, onIncident, onChange }: { now: number; onIncident: (id: number) => void; onChange: () => void }) {
	const q = useSQLQuery(PROPOSALS_SQL);
	const rows = rowsOf(q);
	const done = () => {
		q.refetch();
		onChange();
	};
	return (
		<Section>
			<SectionTitle note="heal.main.incidents.proposal · a fix that passed every check and review, waiting for a human (autonomy L3)">Fixes waiting for approval</SectionTitle>
			{q.isLoading && !q.data ? (
				<Skeleton h={120} />
			) : q.isError ? (
				<ErrorLine error={q.error} />
			) : !rows.length ? (
				<Empty>No proposals. With AUTONOMY=L3 (the default) every fix waits here for a human; this recipe's demo Flights run at L1, so their fixes ship on their own.</Empty>
			) : (
				rows.map((p) => <ProposalCard key={N(p.id)} p={p} now={now} onDone={done} onIncident={onIncident} />)
			)}
		</Section>
	);
}

function AgentsPanel({ now }: { now: number }) {
	const q = useSQLQuery(AGENTS_SQL);
	const rows = rowsOf(q);
	return (
		<Section>
			<SectionTitle note={rows.length ? `share${S(rows[0].shares).includes(',') ? 's' : ''}: ${S(rows[0].shares) || '–'}` : undefined}>Agent accounts</SectionTitle>
			{q.isLoading && !q.data ? (
				<Skeleton h={40} />
			) : q.isError ? (
				<ErrorLine error={q.error} />
			) : !rows.length ? (
				<Empty>No agent accounts yet. The healer creates them (and the restricted shares they read) the first time it fixes something.</Empty>
			) : (
				<table style={{ width: '100%', borderCollapse: 'collapse' }}>
					<thead>
						<tr>
							{['Service account', 'Role', 'Now'].map((h) => (
								<th key={h} style={th}>
									{h}
								</th>
							))}
						</tr>
					</thead>
					<tbody>
						{rows.map((r) => (
							<tr key={S(r.username)}>
								<td style={{ ...td, fontFamily: T.mono, fontSize: 11 }}>{S(r.username)}</td>
								<td style={{ ...td, fontFamily: T.mono, fontSize: 11 }}>{S(r.role)}</td>
								<td style={{ ...td, fontSize: 11 }}>{B(r.busy) ? <span style={{ color: T.blue }}>fixing for healer run {N(r.run_number)} · {ago(NN(r.since_ms), now)}</span> : <span style={{ color: T.muted }}>free</span>}</td>
							</tr>
						))}
					</tbody>
				</table>
			)}
		</Section>
	);
}

const incProposalsSQL = (id: number) => `
SELECT (p->>'n')::DOUBLE AS id, CASE WHEN status = 'deciding' THEN 'deciding' ELSE p->>'status' END AS status,
  (p->>'base_version')::DOUBLE AS base_version, (p->>'shipped_version')::DOUBLE AS shipped_version, p->>'note' AS note
FROM (SELECT status, proposal::JSON AS p FROM ${H}."incidents" WHERE incident_id = ${id} AND proposal IS NOT NULL)`;

function IncidentDetail({ inc, now, onGuides, onApprovals, onChange }: { inc: Row; now: number; onGuides: () => void; onApprovals: () => void; onChange: () => void }) {
	const id = N(inc.id);
	const transQ = useSQLQuery(transSQL(id));
	const eventsQ = useSQLQuery(eventsSQL(id));
	const fixQ = useSQLQuery(fixerSQL(id));
	const actQ = useSQLQuery(actionsSQL(id));
	const gQ = useSQLQuery(incGuidesSQL(id));
	const pQ = useSQLQuery(incProposalsSQL(id));
	const props = rowsOf(pQ);
	const status = S(inc.status);
	const fixers = rowsOf(fixQ);
	const actRows = rowsOf(actQ);
	const byRun = useMemo(() => {
		const m: Record<string, Action[]> = {};
		for (const r of actRows) (m[S(r.fixer_run_id)] ??= []).push(...toActions([r]));
		return m;
	}, [actRows]);
	const guides = rowsOf(gQ);
	const own = guides.find((g) => S(g.rel) === 'own');
	const links = guides.filter((g) => S(g.rel) === 'link');
	const reads = guides.filter((g) => S(g.rel) === 'read');
	return (
		<div>
			<div className="flex flex-wrap items-baseline" style={{ gap: 8 }}>
				<h3 style={{ fontSize: 16, fontWeight: 700 }}>
					#{id} {S(inc.class)}
				</h3>
				<IncPill status={S(inc.status)} />
				<span style={{ fontSize: 11, color: T.muted }}>
					{S(inc.category)} · {S(inc.disposition)} · {N(inc.events)} events · {N(inc.attempts)} attempts · {usd(N(inc.cost))}
				</span>
			</div>
			<div style={{ fontSize: 12, color: T.text, marginTop: 2 }}>{S(inc.title)}</div>
			<div className="flex flex-wrap" style={{ gap: 12, fontSize: 11, marginTop: 4, color: T.muted }}>
				<span>
					pipeline: <b style={{ color: T.text }}>{S(inc.pipeline)}</b>
				</span>
				{NN(inc.fixed_version) ? <span style={{ fontFamily: T.mono }}>fix shipped as Flight version {N(inc.fixed_version)}</span> : null}
				<span>green runs since the fix {N(inc.green_runs)}</span>
				{NN(inc.root_id) ? <span>waits behind the fix for #{N(inc.root_id)}</span> : null}
				<span>opened {stamp(NN(inc.created_ms))}</span>
				{NN(inc.resolved_ms) ? <span>resolved in {fmtDur(N(inc.resolved_ms) - N(inc.created_ms))}</span> : null}
			</div>
			{props.length || ['escalated', 'rejected', 'watching'].includes(status) ? (
				<div className="flex flex-wrap items-center" style={{ gap: 10, fontSize: 11, marginTop: 6 }}>
					{props.map((p) => (
						<span key={N(p.id)}>
							proposal {id}:{N(p.id)} <Pill label={S(p.status)} color={PROP_COLOR[S(p.status)] ?? T.gray} /> {S(p.note) ? <span style={{ color: T.muted }}>{trunc(S(p.note), 120)}</span> : null}
							{S(p.status) === 'pending' ? (
								<span style={{ ...linkStyle, marginLeft: 6 }} onClick={onApprovals}>
									review and approve
								</span>
							) : null}
						</span>
					))}
					{['escalated', 'rejected', 'watching'].includes(status) ? (
						<HealerButton
							label="Retry the fix"
							config={{ ACTION: 'retry', INCIDENT_ID: String(id) }}
							onDone={() => {
								transQ.refetch();
								onChange();
							}}
						/>
					) : null}
				</div>
			) : null}

			<Section>
				<SectionTitle note="log rows of kind transition">Lifecycle</SectionTitle>
				{transQ.isLoading && !transQ.data ? <Skeleton h={60} /> : transQ.isError ? <ErrorLine error={transQ.error} /> : <Lifecycle rows={rowsOf(transQ)} />}
			</Section>

			<Section>
				<SectionTitle note="phases derived from the log's fix steps; red arcs are fix loops, dashed arcs are revisits">Healing DAG per attempt</SectionTitle>
				{fixQ.isLoading && !fixQ.data ? (
					<Skeleton h={140} />
				) : fixQ.isError ? (
					<ErrorLine error={fixQ.error} />
				) : !fixers.length ? (
					<Empty>{S(inc.level) ? 'No fixer has been dispatched for this incident yet.' : 'No fixer for this class: transient incidents are watched, not fixed.'}</Empty>
				) : actQ.isError ? (
					<ErrorLine error={actQ.error} />
				) : (
					fixers.map((fr) => <AttemptCard key={S(fr.fixer_run_id)} fr={fr} actions={byRun[S(fr.fixer_run_id)] ?? []} />)
				)}
			</Section>

			<div className="grid grid-cols-2" style={{ gap: 20 }}>
				<Section>
					<SectionTitle note="log rows of kind failure">Grouped events</SectionTitle>
					{eventsQ.isLoading && !eventsQ.data ? <Skeleton h={60} /> : eventsQ.isError ? <ErrorLine error={eventsQ.error} /> : <EventGroups rows={rowsOf(eventsQ)} now={now} />}
				</Section>
				<Section>
					<SectionTitle note="MD_LIST_GUIDES · log rows guide, guide_link">Memory</SectionTitle>
					{gQ.isLoading && !gQ.data ? (
						<Skeleton h={40} />
					) : gQ.isError ? (
						<ErrorLine error={gQ.error} />
					) : (
						<div style={{ fontSize: 11, lineHeight: '16px' }}>
							<div>
								<b>Guide written:</b>{' '}
								{own ? (
									<>
										{trunc(S(own.title), 90)} <span style={{ fontFamily: T.mono, color: T.muted }}>{S(own.guide_id).slice(0, 8)}</span>
									</>
								) : S(inc.guide_id) ? (
									<span style={{ fontFamily: T.mono }}>{S(inc.guide_id)}</span>
								) : (
									<span style={{ color: T.muted }}>none yet</span>
								)}
							</div>
							<div style={{ marginTop: 4 }}>
								<b>Related guides used:</b> {links.length ? '' : <span style={{ color: T.muted }}>none</span>}
							</div>
							{links.map((l, i) => (
								<div key={i} style={{ marginLeft: 10 }}>
									<Pill label={S(l.how_used) || 'link'} color={T.slate} outline /> {trunc(S(l.title) || S(l.guide_id), 80)}
									{S(l.reason) ? <div style={{ color: T.muted }}>{trunc(S(l.reason), 160)}</div> : null}
								</div>
							))}
							<div style={{ marginTop: 4 }}>
								<b>Guides read by the fixer:</b> {reads.length ? reads.map((r) => trunc(S(r.title) || S(r.guide_id).slice(0, 8), 50)).join(', ') : <span style={{ color: T.muted }}>none</span>}
							</div>
							<button style={{ ...btn(), marginTop: 6 }} onClick={onGuides}>
								Open Memory view
							</button>
						</div>
					)}
				</Section>
			</div>

			{fixers.some((f) => S(f.root_cause) || S(f.fix)) ? (
				<Section>
					<SectionTitle note="log: the attempt's summary">Fixer summary</SectionTitle>
					{fixers.map((f) => (
						<SummaryBlock key={S(f.fixer_run_id)} fr={f} />
					))}
				</Section>
			) : null}
		</div>
	);
}

function MemoryView({ now }: { now: number }) {
	const gQ = useSQLQuery(GUIDES_SQL);
	const lQ = useSQLQuery(LINKS_SQL);
	const guides = rowsOf(gQ);
	const links = rowsOf(lQ);
	const cats = Array.from(new Set(guides.map((g) => S(g.category) || 'other')));
	const byId: Record<string, Row> = Object.fromEntries(guides.map((g) => [S(g.guide_id), g]));
	const colW = 200;
	const nodeH = 34;
	const pos: Record<string, { x: number; y: number }> = {};
	cats.forEach((c, ci) => guides.filter((g) => (S(g.category) || 'other') === c).forEach((g, ri) => (pos[S(g.guide_id)] = { x: 4 + ci * (colW + 30), y: 22 + ri * (nodeH + 14) })));
	const maxRows = Math.max(1, ...cats.map((c) => guides.filter((g) => (S(g.category) || 'other') === c).length));
	const W = Math.max(400, 4 + cats.length * (colW + 30));
	const Hh = 22 + maxRows * (nodeH + 14) + 10;
	return (
		<div>
			<Section>
				<SectionTitle note={`${guides.length} guides · ${links.length} links`}>Guide link graph</SectionTitle>
				{gQ.isLoading && !gQ.data ? (
					<Skeleton h={120} />
				) : gQ.isError ? (
					<ErrorLine error={gQ.error} />
				) : !guides.length ? (
					<Empty>No Guides yet. Each resolved incident writes one.</Empty>
				) : (
					<div style={{ overflowX: 'auto' }}>
						<svg viewBox={`0 0 ${W} ${Hh}`} style={{ width: '100%', maxWidth: W * 1.2, minWidth: Math.min(W, 560), display: 'block', fontFamily: T.font }}>
							<defs>
								<marker id="mg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
									<path d="M 0 0 L 10 5 L 0 10 z" fill={T.blue} />
								</marker>
							</defs>
							{cats.map((c, ci) => (
								<text key={c} x={4 + ci * (colW + 30)} y={12} fontSize={9} fontWeight={700} fill={T.muted}>
									{c.toUpperCase()}
								</text>
							))}
							{links.map((l, i) => {
								const a = pos[S(l.from_id)];
								const b = pos[S(l.to_id)];
								if (!a || !b) return null;
								const x1 = a.x + colW / 2;
								const y1 = a.y + nodeH / 2;
								const x2 = b.x + colW / 2;
								const y2 = b.y + nodeH / 2;
								const mx = (x1 + x2) / 2;
								const my = Math.min(y1, y2) - 18 - (i % 3) * 8;
								return (
									<g key={i}>
										<title>{`${S(l.how_used)}: ${S(l.reason)}`}</title>
										<path d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`} fill="none" stroke={T.blue} strokeWidth={1.2} markerEnd="url(#mg-arrow)" />
										<text x={mx} y={my + 6} fontSize={9} textAnchor="middle" fill={T.blue}>
											{S(l.how_used)}
										</text>
									</g>
								);
							})}
							{guides.map((g) => {
								const p = pos[S(g.guide_id)];
								return (
									<g key={S(g.guide_id)}>
										<title>{`${S(g.title)}\n${S(g.description).slice(0, 300)}`}</title>
										<rect x={p.x} y={p.y} width={colW} height={nodeH} rx={5} fill={T.panel} stroke={S(g.outcome) === 'fixed' ? T.green : T.line} />
										<text x={p.x + 7} y={p.y + 14} fontSize={10} fontWeight={700} fill={T.text}>
											{trunc(`#${N(g.incident_id)} ${S(g.class)}`, 30)}
										</text>
										<text x={p.x + 7} y={p.y + 27} fontSize={9} fill={T.muted}>
											{trunc(`${S(g.pipeline)} · ${S(g.outcome)} · helpful ${N(g.helpful)}`, 36)}
										</text>
									</g>
								);
							})}
						</svg>
						{!links.length ? <div style={{ fontSize: 11, color: T.muted }}>No links between Guides yet: links appear when a fixer reuses an earlier Guide.</div> : null}
					</div>
				)}
			</Section>
			<Section>
				<SectionTitle note="latest version of each guide">Guides by category</SectionTitle>
				{!guides.length ? (
					<Empty>No Guides yet.</Empty>
				) : (
					<table style={{ width: '100%', borderCollapse: 'collapse' }}>
						<thead>
							<tr>
								{['Category', 'Guide', 'Pipeline', 'Outcome', 'Helpful', 'Links in', 'Ver', 'Created'].map((h) => (
									<th key={h} style={th}>
										{h}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{guides.map((g) => (
								<tr key={S(g.guide_id)}>
									<td style={{ ...td, fontSize: 11 }}>{S(g.category)}</td>
									<td style={{ ...td, maxWidth: 360 }}>
										<div style={{ fontWeight: 600 }}>{trunc(S(g.title), 90)}</div>
										<div style={{ fontSize: 10, color: T.muted }}>{trunc(S(g.description), 180)}</div>
										<div style={{ fontSize: 9, color: T.faint, fontFamily: T.mono }}>{S(g.guide_id)}</div>
									</td>
									<td style={td}>{S(g.pipeline)}</td>
									<td style={td}>{S(g.outcome)}</td>
									<td style={td}>{N(g.helpful)}</td>
									<td style={td}>{links.filter((l) => S(l.to_id) === S(g.guide_id)).length}</td>
									<td style={td}>{N(g.version)}</td>
									<td style={{ ...td, fontSize: 11, color: T.muted, whiteSpace: 'nowrap' }}>{ago(NN(g.created_ms), now)}</td>
								</tr>
							))}
						</tbody>
					</table>
				)}
				{byId && links.length ? (
					<div style={{ marginTop: 10 }}>
						{links.map((l, i) => (
							<div key={i} style={{ fontSize: 11 }}>
								{trunc(S(byId[S(l.from_id)]?.title) || S(l.from_id), 50)} → <b>{S(l.how_used)}</b> → {trunc(S(byId[S(l.to_id)]?.title) || S(l.to_id), 50)}
								<span style={{ color: T.muted }}> · {trunc(S(l.reason), 120)}</span>
							</div>
						))}
					</div>
				) : null}
			</Section>
		</div>
	);
}

// ---------- architecture tabs (three candidate explainers) ----------
// MotherDuck brand palette for the explainer diagrams
const A = {
	ink: '#383838',
	muted: '#5f5f5f',
	paper: '#F4EFEA',
	paper2: '#ECE5DD',
	paper3: '#E1D6CB',
	white: '#FFFFFF',
	yellow: '#FFDE00',
	orange: '#FF9538',
	coral: '#FF7169',
	coralSoft: '#FFCBC8',
	teal: '#16AA98',
	tealSoft: '#53DBC9',
	sky: '#6FC2FF',
	skyPale: '#C2E5FF',
	mono: "'Aeonik Mono', 'Space Mono', Menlo, ui-monospace, monospace",
	sans: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
};

const ARCH_CSS = `
.ax-tok{offset-rotate:0deg;animation:ax-move 3s linear infinite}
@keyframes ax-move{0%{offset-distance:0%;opacity:0}8%{opacity:1}90%{opacity:1}100%{offset-distance:100%;opacity:0}}
.ax-play{offset-rotate:0deg;animation:ax-play 10s linear infinite}
@keyframes ax-play{0%{offset-distance:0%;opacity:1}85%{offset-distance:100%;opacity:1}95%{offset-distance:100%;opacity:0}100%{offset-distance:100%;opacity:0}}
.ax-march{stroke-dasharray:9 7;animation:ax-dash 1s linear infinite}
@keyframes ax-dash{to{stroke-dashoffset:-16}}
.ax-lit{animation:ax-lit 8s ease-in-out infinite}
@keyframes ax-lit{0%,30%,100%{fill:var(--base)}6%,24%{fill:var(--hi)}}
.ax-pop{transform-box:fill-box;transform-origin:center;animation:ax-pop 10s ease-out infinite}
@keyframes ax-pop{0%{transform:scale(1)}3%{transform:scale(1.28)}7%,100%{transform:scale(1)}}
.ax-pulse{transform-box:fill-box;transform-origin:center;animation:ax-pulse 2.4s ease-in-out infinite}
@keyframes ax-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.035)}}
.ax-ring{transform-box:fill-box;transform-origin:center;animation:ax-ring 2.4s ease-out infinite}
@keyframes ax-ring{0%{transform:scale(.85);opacity:.8}100%{transform:scale(1.5);opacity:0}}
.ax-beat{transform-box:fill-box;transform-origin:center;animation:ax-beat 1.2s ease-in-out infinite}
@keyframes ax-beat{0%,100%{transform:scale(1)}15%{transform:scale(1.25)}30%{transform:scale(1)}45%{transform:scale(1.15)}}
.ax-grow{transform-box:fill-box;transform-origin:left center;animation:ax-grow 10s linear infinite}
@keyframes ax-grow{0%{transform:scaleX(0)}85%,100%{transform:scaleX(1)}}
.ax-curtain{transform-box:fill-box;transform-origin:right center;animation:ax-curtain 10s linear infinite}
@keyframes ax-curtain{0%{transform:scaleX(1)}85%,100%{transform:scaleX(0)}}
.ax-curtain.ax-fast{animation-name:ax-curtain2}
@keyframes ax-curtain2{0%{transform:scaleX(1)}60%,100%{transform:scaleX(0)}}
.ax-play.ax-fast{animation-name:ax-play2}
@keyframes ax-play2{0%{offset-distance:0%;opacity:1}60%{offset-distance:100%;opacity:1}68%,100%{offset-distance:100%;opacity:0}}
.ax-blink{animation:ax-blink 6s ease-in-out infinite}
@keyframes ax-blink{0%,40%,100%{fill:${A.teal}}48%,70%{fill:${A.coral}}}
.ax-tab:hover{transform:translate(-1px,-1px)}
@media (prefers-reduced-motion: reduce){
.ax-root *{animation:none!important}
.ax-tok,.ax-play,.ax-curtain,.ax-ring{display:none}
}
`;

type CSSV = React.CSSProperties & Record<string, string | number>;

// Shared SVG primitives: text, card with hard offset shadow, icon, cylinder, pill
function Tx({
	x,
	y,
	children,
	s = 16,
	w = 700,
	a = 'middle',
	c = A.ink,
	f = 'mono',
}: {
	x: number;
	y: number;
	children: React.ReactNode;
	s?: number;
	w?: number;
	a?: 'start' | 'middle' | 'end';
	c?: string;
	f?: 'mono' | 'sans';
}) {
	return (
		<text x={x} y={y} fontSize={s} fontWeight={w} textAnchor={a} fill={c} fontFamily={f === 'mono' ? A.mono : A.sans}>
			{children}
		</text>
	);
}
function Card({
	x,
	y,
	w,
	h,
	fill = A.white,
	r = 16,
	shadow = 5,
	cls,
	style,
	dash,
}: {
	x: number;
	y: number;
	w: number;
	h: number;
	fill?: string;
	r?: number;
	shadow?: number;
	cls?: string;
	style?: CSSV;
	dash?: string;
}) {
	return (
		<g>
			{shadow ? <rect x={x + shadow} y={y + shadow} width={w} height={h} rx={r} fill={A.ink} /> : null}
			<rect x={x} y={y} width={w} height={h} rx={r} fill={fill} stroke={A.ink} strokeWidth={2.5} className={cls} style={style} strokeDasharray={dash} />
		</g>
	);
}
function Ic({ I, x, y, s = 30, c = A.ink }: { I: React.ElementType; x: number; y: number; s?: number; c?: string }) {
	return <I x={x - s / 2} y={y - s / 2} size={s} width={s} height={s} color={c} strokeWidth={2} />;
}
function Cyl({ cx, cy, w = 130, h = 70, fill = A.skyPale }: { cx: number; cy: number; w?: number; h?: number; fill?: string }) {
	const rx = w / 2;
	const top = cy - h / 2;
	const bot = cy + h / 2;
	return (
		<g>
			<path d={`M${cx - rx} ${top} L${cx - rx} ${bot} A${rx} 12 0 0 0 ${cx + rx} ${bot} L${cx + rx} ${top}`} fill={fill} stroke={A.ink} strokeWidth={2.5} />
			<ellipse cx={cx} cy={top} rx={rx} ry={12} fill={fill} stroke={A.ink} strokeWidth={2.5} />
		</g>
	);
}
function StatePill({ cx, cy, w = 170, h = 56, fill, label, dash, cls, style }: { cx: number; cy: number; w?: number; h?: number; fill: string; label: string; dash?: string; cls?: string; style?: CSSV }) {
	return (
		<g>
			{dash ? null : <rect x={cx - w / 2 + 4} y={cy - h / 2 + 4} width={w} height={h} rx={h / 2} fill={A.ink} />}
			<rect x={cx - w / 2} y={cy - h / 2} width={w} height={h} rx={h / 2} fill={fill} stroke={A.ink} strokeWidth={2.5} strokeDasharray={dash} className={cls} style={style} />
			<Tx x={cx} y={cy + 6} s={17}>
				{label}
			</Tx>
		</g>
	);
}
function Defs({ p }: { p: string }) {
	return (
		<defs>
			<marker id={`${p}-ah`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
				<path d="M0 0 L10 5 L0 10 z" fill={A.ink} />
			</marker>
		</defs>
	);
}
function Edge({ d, p, dash, c = A.ink, both, cls, w = 2.5 }: { d: string; p: string; dash?: boolean; c?: string; both?: boolean; cls?: string; w?: number }) {
	return (
		<path
			d={d}
			fill="none"
			stroke={c}
			strokeWidth={w}
			strokeLinecap="round"
			strokeDasharray={dash ? '7 7' : undefined}
			className={cls}
			markerEnd={`url(#${p}-ah)`}
			markerStart={both ? `url(#${p}-ah)` : undefined}
		/>
	);
}
// A token that travels along a path (CSS motion path; hidden when motion is reduced)
function Tok({ d, dur = 3, delay = 0, c = A.coral, r = 8, cls = 'ax-tok' }: { d: string; dur?: number; delay?: number; c?: string; r?: number; cls?: string }) {
	const st: CSSV = { offsetPath: `path('${d}')`, animationDuration: `${dur}s`, animationDelay: `${delay}s` };
	return <circle r={r} cx={0} cy={0} fill={c} stroke={A.ink} strokeWidth={2} className={cls} style={st} />;
}
const lit = (base: string, delay: number, hi = A.yellow, cycle = 8): CSSV => ({ '--base': base, '--hi': hi, animationDelay: `${delay}s`, animationDuration: `${cycle}s` });
// Friendly orange duck, centered on (x, y)
function Duck({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
	return (
		<g transform={`translate(${x} ${y}) scale(${s})`} stroke={A.ink} strokeWidth={2.5} strokeLinejoin="round">
			<path d="M34 6 L48 -6 L44 18 Z" fill={A.orange} />
			<ellipse cx={4} cy={14} rx={36} ry={21} fill={A.orange} />
			<circle cx={-16} cy={-14} r={18} fill={A.orange} />
			<path d="M-33 -17 L-50 -11 L-33 -5 Z" fill={A.yellow} />
			<circle cx={-20} cy={-18} r={2.8} fill={A.ink} stroke="none" />
			<path d="M-8 8 Q8 24 26 8" fill="none" />
		</g>
	);
}
const Check = ({ x, y, c = A.ink }: { x: number; y: number; c?: string }) => (
	<path d={`M${x - 7} ${y} L${x - 2} ${y + 5} L${x + 8} ${y - 6}`} fill="none" stroke={c} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
);
const Cross = ({ x, y, c = A.ink }: { x: number; y: number; c?: string }) => (
	<path d={`M${x - 6} ${y - 6} L${x + 6} ${y + 6} M${x + 6} ${y - 6} L${x - 6} ${y + 6}`} fill="none" stroke={c} strokeWidth={3} strokeLinecap="round" />
);

function Fig({ n, title, caption, h, y0 = 0, children }: { n: string; title: string; caption?: string; h: number; y0?: number; children: React.ReactNode }) {
	return (
		<figure style={{ margin: '0 0 28px', background: A.paper, border: `2px solid ${A.ink}`, borderRadius: 18, boxShadow: `6px 6px 0 ${A.ink}`, padding: '18px 22px 16px' }}>
			<div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: A.mono, fontWeight: 700, fontSize: 16, color: A.ink }}>
				<span style={{ background: A.yellow, border: `2px solid ${A.ink}`, borderRadius: 999, padding: '1px 10px', fontSize: 13 }}>{n}</span>
				{title}
			</div>
			<svg viewBox={`0 ${y0} 960 ${h}`} width="100%" role="img" aria-label={caption ? `${title}. ${caption}` : title} style={{ display: 'block', maxWidth: 1040, margin: '10px auto 0', overflow: 'visible' }}>
				{children}
			</svg>
			{caption ? <figcaption style={{ fontFamily: A.sans, fontSize: 14, lineHeight: 1.5, color: A.muted, marginTop: 10 }}>{caption}</figcaption> : null}
		</figure>
	);
}

const ARCH_SQL = `
SELECT
  (SELECT count(*) FROM ${H}."incidents" WHERE status = 'resolved')::INT AS n_res,
  (SELECT median(detail.cost_usd::DOUBLE) FROM ${L} WHERE kind = 'attempt' AND verdict = 'merged' AND detail.cost_usd::DOUBLE > 0)::DOUBLE AS fix_cost,
  (SELECT count(*) FROM MD_LIST_GUIDES() WHERE topic LIKE 'self-healing/incidents/%')::INT AS n_guides`;

function ArchHeader({ letter, title, sub, live }: { letter: string; title: string; sub: string; live: React.ReactNode }) {
	return (
		<header style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, margin: '22px 0 26px' }}>
			<div>
				<div style={{ fontFamily: A.mono, fontSize: 12, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: A.ink }}>Architecture · option {letter}</div>
				<h2 style={{ fontFamily: A.mono, fontSize: 30, fontWeight: 700, letterSpacing: '-0.02em', color: A.ink, margin: '6px 0 4px', lineHeight: 1.15 }}>{title}</h2>
				<p style={{ fontFamily: A.sans, fontSize: 15, color: A.muted, margin: 0 }}>{sub}</p>
			</div>
			{live}
		</header>
	);
}
function LiveChip({ q, value, label }: { q: ReturnType<typeof useSQLQuery>; value: (r: Row) => string | null; label: string }) {
	const r = rowsOf(q)[0];
	const v = r ? value(r) : null;
	if (!v) return null;
	return (
		<div style={{ display: 'flex', alignItems: 'center', gap: 10, background: A.white, border: `2px solid ${A.ink}`, boxShadow: `3px 3px 0 ${A.ink}`, borderRadius: 999, padding: '6px 16px 6px 8px' }}>
			<span style={{ width: 10, height: 10, borderRadius: 999, background: A.teal, border: `2px solid ${A.ink}`, marginLeft: 4 }} />
			<span style={{ fontFamily: A.mono, fontWeight: 700, fontSize: 20, color: A.ink }}>{v}</span>
			<span style={{ fontFamily: A.sans, fontSize: 13, color: A.muted }}>{label}</span>
		</div>
	);
}

// ----- Option A: a left-to-right story -----
function ArchA({ q }: { q: ReturnType<typeof useSQLQuery> }) {
	const xs = (m: number) => 70 + m * 70;
	const cyc = 10;
	const at = (m: number) => ((xs(m) - 70) / 830) * cyc * 0.85;
	const miles: { m: number; t: string; l1: string; l2?: string; fill: string; icon?: React.ElementType; mark?: 'x' | 'check' }[] = [
		{ m: 0, t: '2:00', l1: 'a run fails', fill: A.coral, mark: 'x' },
		{ m: 5, t: '2:05', l1: 'healer opens', l2: 'an incident', fill: A.yellow, icon: Search },
		{ m: 6.8, t: '2:07', l1: 'fix passes', l2: 'every review', fill: A.orange, icon: UserCheck },
		{ m: 8.9, t: '2:08', l1: 'ships (L1) or', l2: 'you approve (L3)', fill: A.tealSoft, icon: Rocket },
		{ m: 11, t: '2:11', l1: '2 green runs:', l2: 'resolved, Guide', fill: A.teal, icon: BookOpen },
	];
	return (
		<>
			<ArchHeader
				letter="A"
				title="A pipeline breaks at 2 a.m."
				sub="Follow one failure from left to right until it is fixed and remembered."
				live={<LiveChip q={q} value={(r) => (N(r.n_res) ? String(N(r.n_res)) : null)} label="incidents resolved so far" />}
			/>
			<Fig n="A1" title="The cast: three Flights, a pool of agent accounts and a Dive" h={410} caption="Two pipeline Flights load weather data. The healer Flight watches their runs; for each failure it leases an agent service account that can read production only through a restricted share. Fixes go back to the pipelines as new Flight versions (or wait for your approval), and every step is logged for this Dive.">
				<Defs p="a1" />
				<Card x={30} y={50} w={210} h={190} />
				<Tx x={135} y={88} s={20}>
					Pipelines
				</Tx>
				<rect x={46} y={108} width={178} height={46} rx={23} fill={A.skyPale} stroke={A.ink} strokeWidth={2} />
				<Ic I={CloudSun} x={72} y={131} s={24} />
				<Tx x={138} y={137} s={15}>
					weather
				</Tx>
				<circle cx={205} cy={131} r={8} fill={A.teal} stroke={A.ink} strokeWidth={2} className="ax-blink" />
				<rect x={46} y={168} width={178} height={46} rx={23} fill={A.skyPale} stroke={A.ink} strokeWidth={2} />
				<Ic I={Clock} x={72} y={191} s={24} />
				<Tx x={138} y={197} s={15}>
					hourly
				</Tx>
				<circle cx={205} cy={191} r={8} fill={A.teal} stroke={A.ink} strokeWidth={2} />
				<Edge p="a1" d="M135 248 L135 266" />
				<Cyl cx={135} cy={305} w={150} h={50} />
				<Tx x={135} y={318} s={14}>
					heal_demo
				</Tx>

				<Card x={380} y={50} w={200} h={190} fill={A.yellow} />
				<circle cx={480} cy={118} r={46} fill="none" stroke={A.ink} strokeWidth={2} className="ax-ring" />
				<Duck x={484} y={116} s={0.95} />
				<Tx x={480} y={192} s={22}>
					Healer
				</Tx>
				<Tx x={480} y={212} s={12} w={400} f="sans">
					every 5 min, and every
				</Tx>
				<Tx x={480} y={228} s={12} w={400} f="sans">
					minute while fixing
				</Tx>

				<Card x={370} y={300} w={220} h={88} fill={A.tealSoft} />
				<Ic I={Users} x={404} y={330} s={26} />
				<Tx x={496} y={336} s={16}>
					agent accounts
				</Tx>
				<Tx x={480} y={362} s={10.5} w={400} f="sans">
					role self_healing_pipeline_agent
				</Tx>
				<Edge p="a1" d="M480 246 L480 294" />
				<Tx x={490} y={274} s={12} w={500} f="sans" a="start">
					one per fix
				</Tx>
				<Edge p="a1" d="M212 318 C 280 330, 300 345, 364 345" dash />
				<Tx x={292} y={372} s={12} w={500} f="sans">
					restricted share
				</Tx>
				<Tok d="M212 318 C 280 330, 300 345, 364 345" c={A.sky} dur={2.6} />

				<Edge p="a1" d="M246 100 L372 100" />
				<Tx x={309} y={88} s={13} w={500} f="sans">
					failed run
				</Tx>
				<Tok d="M246 100 L372 100" c={A.coral} dur={3} />
				<Edge p="a1" d="M372 195 L246 195" />
				<Tx x={309} y={220} s={13} w={500} f="sans">
					fixed version
				</Tx>
				<Tok d="M372 195 L246 195" c={A.teal} dur={3} delay={1.5} />

				<Card x={720} y={50} w={210} h={190} />
				<Ic I={LayoutDashboard} x={825} y={110} s={44} />
				<Tx x={825} y={178} s={20}>
					This Dive
				</Tx>
				<Tx x={825} y={202} s={13} w={400} f="sans">
					every step · Approve
				</Tx>
				<Cyl cx={825} cy={322} w={150} h={50} fill={A.white} />
				<Tx x={825} y={335} s={14}>
					heal
				</Tx>
				<Edge p="a1" d="M825 282 L825 252" />
				<Edge p="a1" d="M586 160 C 660 160, 640 322, 742 322" />
				<Tx x={636} y={278} s={13} w={500} f="sans" a="end">
					logs it all
				</Tx>
				<Tok d="M586 160 C 660 160, 640 322, 742 322" c={A.sky} dur={3} delay={0.8} />
				<Edge p="a1" d="M714 90 L590 90" dash />
				<Tx x={652} y={80} s={12} w={500} f="sans">
					Approve, Retry
				</Tx>
			</Fig>

			<Fig n="A2" title="What one healer Flight does" h={290}>
				<Defs p="a2" />
				{[
					{ I: Lock, n: 'Lease', a: 'one coordinator', b: 'run at a time' },
					{ I: Search, n: 'Check', a: 'every minute,', b: 'even mid-fix' },
					{ I: Sparkles, n: 'Classify', a: 'Jev, or an LLM', b: 'if Jev is unsure' },
					{ I: Wrench, n: 'Fix', a: 'an agent account', b: 'per incident' },
					{ I: UserCheck, n: 'Review', a: 'checks, tests,', b: 'approver' },
					{ I: Rocket, n: 'Ship', a: 'merge, retest,', b: 'propose or ship' },
					{ I: BookOpen, n: 'Learn', a: 'a Guide with', b: 'a code snippet' },
				].map((s, i, all) => {
					// Seven equal boxes across the 900px row, 13px apart
					const x = 45 + i * 128;
					return (
						<g key={s.n}>
							<Card x={x} y={40} w={115} h={160} cls="ax-lit" style={lit(A.white, 0.32 + i * 1.14)} />
							<Ic I={s.I} x={x + 57.5} y={82} s={30} />
							<Tx x={x + 57.5} y={136} s={18}>
								{s.n}
							</Tx>
							<Tx x={x + 57.5} y={160} s={11.5} w={400} f="sans">
								{s.a}
							</Tx>
							<Tx x={x + 57.5} y={177} s={11.5} w={400} f="sans">
								{s.b}
							</Tx>
							{i < all.length - 1 ? <Edge p="a2" d={`M${x + 119} 120 L${x + 126} 120`} /> : null}
						</g>
					);
				})}
				<path d="M45 232 L945 232 Q 960 232 960 248 L960 256 Q 960 272 945 272 L60 272 Q 45 272 45 256 L45 232" fill="none" stroke={A.ink} strokeWidth={2} className="ax-march" opacity={0.5} />
				<rect x={270} y={260} width={420} height={24} fill={A.paper} />
				<Tx x={480} y={277} s={13} w={500} f="sans">
					a new run every 5 minutes · a check every minute while fixing
				</Tx>
				<Tok d="M45 232 L945 232" c={A.yellow} dur={8} r={9} />
			</Fig>

			<Fig n="A3" title="Two breaks, fixed side by side" y0={20} h={340} caption="The healer itself (plain code, no coordinator model) starts one Claude fixer per incident at once, each as its own agent service account on its own zero-copy clone. A new failure reaches a running fixer as at most one short notice a minute, and every fix passes the healer's checks, an independent test reviewer and an approver.">
				<Defs p="a3" />
				{[70, 250].map((y, i) => (
					<g key={y}>
						<Card x={30} y={y} w={170} h={80} fill={A.coralSoft} />
						<Tx x={115} y={y + 35} s={17}>
							{`incident ${i + 1}`}
						</Tx>
						<Tx x={115} y={y + 58} s={13} w={400} f="sans">
							{i ? 'hourly pipeline' : 'weather pipeline'}
						</Tx>
					</g>
				))}
				<Edge p="a3" d="M206 110 Q 250 110 268 160" />
				<Edge p="a3" d="M206 290 Q 250 290 268 240" />
				<circle cx={325} cy={200} r={64} fill={A.white} stroke={A.ink} strokeWidth={2.5} />
				<Duck x={327} y={180} s={0.42} />
				<Tx x={325} y={222} s={14}>
					healer
				</Tx>
				<Tx x={325} y={240} s={12} w={400} f="sans">
					plain code
				</Tx>
				<rect x={262} y={44} width={150} height={34} rx={17} fill={A.white} stroke={A.ink} strokeWidth={2} />
				<Ic I={Inbox} x={282} y={61} s={18} />
				<Tx x={346} y={66} s={12} w={600} f="sans">
					≤ 1 notice / min
				</Tx>
				<Edge p="a3" d="M412 61 C 430 61, 440 66, 446 74" />
				<Tok d="M412 61 C 430 61, 440 66, 446 74" c={A.coral} dur={2} />
				<Tx x={325} y={300} s={13} w={500} f="sans">
					fixers start
				</Tx>
				<Tx x={325} y={318} s={13} w={500} f="sans">
					at the same moment
				</Tx>
				{[105, 295].map((y, i) => {
					const d = i ? 'M386 212 C 420 212, 412 295, 444 295' : 'M386 188 C 420 188, 412 105, 444 105';
					return (
						<g key={y}>
							<Edge p="a3" d={d} />
							<Tok d={d} c={A.yellow} dur={2.4} delay={i * 0.05} />
							<Card x={450} y={y - 45} w={160} h={90} fill={A.yellow} />
							<Ic I={Bot} x={482} y={y} s={30} />
							<Tx x={556} y={y - 2} s={17}>
								fixer
							</Tx>
							<Tx x={556} y={y + 20} s={12} w={400} f="sans">
								{`agent account ${i + 1}`}
							</Tx>
							<path d={`M618 ${y} L668 ${y}`} stroke={A.ink} strokeWidth={2.5} className="ax-march" fill="none" />
							<Tx x={643} y={y - 12} s={12} w={500} f="sans">
								verify
							</Tx>
							<Cyl cx={730} cy={y} w={120} h={64} />
							<Tx x={730} y={y + 10} s={14}>
								clone
							</Tx>
							<Edge p="a3" d={i ? `M792 ${y} Q 830 ${y} 848 255` : `M792 ${y} Q 830 ${y} 848 145`} />
						</g>
					);
				})}
				<Tx x={730} y={190} s={12} w={400} f="sans" c={A.muted}>
					zero-copy, inside
				</Tx>
				<Tx x={730} y={208} s={12} w={400} f="sans" c={A.muted}>
					each agent account
				</Tx>
				<Card x={830} y={150} w={115} h={100} fill={A.tealSoft} />
				<Ic I={UserCheck} x={887} y={180} s={28} />
				<Tx x={887} y={216} s={14}>
					reviews
				</Tx>
				<Tx x={887} y={236} s={11.5} w={400} f="sans">
					tests · approver
				</Tx>
			</Fig>

			<Fig n="A4" title="2 a.m., start to finish" h={250} caption="On the 5-minute schedule an incident usually goes from open to a fully reviewed fix in 4 to 9 minutes; the fix itself takes about 2. At autonomy L1 it ships at once; at L3, the default, it waits for your Approve in this Dive (times illustrative).">
				<line x1={70} y1={110} x2={900} y2={110} stroke={A.paper3} strokeWidth={10} strokeLinecap="round" />
				<line x1={70} y1={110} x2={900} y2={110} stroke={A.teal} strokeWidth={10} strokeLinecap="round" className="ax-grow" />
				{miles.map((mi) => (
					<g key={mi.t}>
						<Tx x={xs(mi.m)} y={66} s={15}>
							{mi.t}
						</Tx>
						<g className="ax-pop" style={{ animationDelay: `${at(mi.m)}s`, animationDuration: `${cyc}s` }}>
							<circle cx={xs(mi.m)} cy={110} r={25} fill={mi.fill} stroke={A.ink} strokeWidth={2.5} />
							{mi.icon ? <Ic I={mi.icon} x={xs(mi.m)} y={110} s={24} /> : mi.mark === 'x' ? <Cross x={xs(mi.m)} y={110} /> : <Check x={xs(mi.m)} y={110} />}
						</g>
						<Tx x={xs(mi.m)} y={164} s={14} w={500} f="sans">
							{mi.l1}
						</Tx>
						{mi.l2 ? (
							<Tx x={xs(mi.m)} y={183} s={14} w={500} f="sans">
								{mi.l2}
							</Tx>
						) : null}
					</g>
				))}
				<Tok d="M70 110 L900 110" c={A.white} r={9} dur={10} cls="ax-play" />
				<Tx x={900} y={220} s={12} w={400} f="sans" a="end" c={A.muted}>
					the healer wakes at :00, :05, :10 …
				</Tx>
			</Fig>
		</>
	);
}

// ----- Option B: zoom from the system to the fix loop -----
function ArchB({ q }: { q: ReturnType<typeof useSQLQuery> }) {
	const cols = [
		{ I: Lock, n: 'Lease', chips: ['one coordinator', 'cancel stuck runs', 'heartbeat'] },
		{ I: Search, n: 'Check', chips: ['every minute', 'each run once', 'park bad runs'] },
		{ I: Sparkles, n: 'Classify', chips: ['fingerprint', 'Jev, else an LLM', 'open or attach'] },
		{ I: Wrench, n: 'Fix', chips: ['agent account', 'clone a share', 'forward failures'] },
		{ I: UserCheck, n: 'Review', chips: ['healer checks', 'test reviewer', 'approver'] },
		{ I: Rocket, n: 'Ship', chips: ['merge if moved', 'retest, recheck', 'propose or ship'] },
		{ I: BookOpen, n: 'Learn', chips: ['write a Guide', 'with a snippet', 'recall it later'] },
	];
	const loop = 'M120 190 A240 130 0 0 1 600 190 A240 130 0 0 1 120 190';
	return (
		<>
			<ArchHeader
				letter="B"
				title="Zoom in, one level at a time"
				sub="The whole system, then inside the healer Flight, then the fix loop at its heart."
				live={<LiveChip q={q} value={(r) => (NN(r.fix_cost) != null ? `$${N(r.fix_cost).toFixed(2)}` : null)} label="median cost of a shipped fix" />}
			/>
			<Fig n="B1" title="Level 1 · the system" h={440} caption="Everything except the weather API, the model APIs and an optional alert webhook lives in your MotherDuck organization: two databases, three Flights, this Dive, and a pool of agent service accounts that can read production only through a restricted share granted to their role.">
				<Defs p="b1" />
				<rect x={150} y={20} width={660} height={400} rx={26} fill={A.paper2} stroke={A.ink} strokeWidth={2} strokeDasharray="8 6" />
				<Tx x={176} y={52} s={13} a="start">
					YOUR MOTHERDUCK ORGANIZATION
				</Tx>
				<Card x={10} y={110} w={115} h={80} r={40} shadow={0} />
				<Ic I={CloudSun} x={67} y={136} s={26} />
				<Tx x={67} y={174} s={13}>
					NWS API
				</Tx>
				<Card x={835} y={110} w={115} h={80} r={40} shadow={0} />
				<Ic I={Sparkles} x={892} y={136} s={26} />
				<Tx x={892} y={174} s={13}>
					LLM APIs
				</Tx>
				<Card x={835} y={14} w={115} h={80} r={40} shadow={0} />
				<Ic I={Bell} x={892} y={40} s={24} />
				<Tx x={892} y={78} s={13}>
					webhook
				</Tx>

				<Card x={185} y={80} w={170} h={100} />
				<Tx x={270} y={124} s={18}>
					Pipelines
				</Tx>
				<Tx x={270} y={148} s={12} w={400} f="sans">
					2 Flights
				</Tx>
				<Cyl cx={270} cy={282} w={150} h={56} />
				<Tx x={270} y={296} s={14}>
					heal_demo
				</Tx>

				<rect x={408} y={68} width={204} height={124} rx={22} fill="none" stroke={A.orange} strokeWidth={3} strokeDasharray="6 6" className="ax-pulse" />
				<Card x={420} y={80} w={180} h={100} fill={A.yellow} />
				<Duck x={468} y={128} s={0.55} />
				<Tx x={548} y={126} s={18}>
					Healer
				</Tx>
				<Tx x={548} y={150} s={12} w={400} f="sans">
					Flight
				</Tx>
				<Cyl cx={510} cy={282} w={150} h={56} fill={A.white} />
				<Tx x={510} y={296} s={14}>
					heal
				</Tx>
				<Card x={660} y={196} w={130} h={100} />
				<Ic I={LayoutDashboard} x={725} y={230} s={30} />
				<Tx x={725} y={276} s={16}>
					Dive
				</Tx>
				<Card x={330} y={340} w={240} h={60} fill={A.tealSoft} />
				<Ic I={Users} x={360} y={370} s={24} />
				<Tx x={466} y={366} s={15}>
					agent accounts ×3
				</Tx>
				<Tx x={466} y={386} s={11.5} w={400} f="sans">
					own clones, read-only share
				</Tx>

				<Edge p="b1" d="M125 150 L179 150" />
				<Tok d="M125 150 L179 150" c={A.sky} dur={2} />
				<Edge p="b1" d="M270 186 L270 236" />
				<Edge p="b1" d="M361 108 L414 108" />
				<Tok d="M361 108 L414 108" c={A.coral} dur={2.4} />
				<Edge p="b1" d="M414 152 L361 152" />
				<Tok d="M414 152 L361 152" c={A.teal} dur={2.4} delay={1.2} />
				<Edge p="b1" d="M510 186 L510 236" />
				<Tok d="M510 186 L510 236" c={A.yellow} dur={2} delay={0.5} />
				<Edge p="b1" d="M588 282 L654 260" />
				<Edge p="b1" d="M606 128 L829 146" dash both />
				<Edge p="b1" d="M606 96 C 700 90, 760 60, 829 56" dash />
				<Edge p="b1" d="M654 214 L606 172" dash />
				<Tx x={668} y={190} s={11.5} w={500} f="sans" a="start">
					Approve, Retry
				</Tx>
				<Edge p="b1" d="M270 324 C 270 360, 290 370, 324 370" dash />
				<Tx x={200} y={384} s={12} w={500} f="sans">
					restricted share
				</Tx>
				<Tok d="M270 324 C 270 360, 290 370, 324 370" c={A.sky} dur={2.4} delay={0.6} />
				<Edge p="b1" d="M430 186 C 400 240, 400 300, 418 334" dash />
				<Tx x={394} y={226} s={11.5} w={500} f="sans" a="end">
					leases one
				</Tx>
				<Tx x={510} y={60} s={12} c={A.orange}>
					zoom in ↓
				</Tx>
			</Fig>

			<Fig n="B2" title="Level 2 · inside the healer Flight" h={400} caption="A run takes the healer lease, or exits because a live run holds it. It checks for failures every minute, even while fixes run. Jev classifies each new failure, and an LLM decides when Jev is unsure; the healer then hands each incident to a worker with its own agent account. A heartbeat renews the lease only while the run makes progress, so a stuck run is cancelled by the next one and an alert goes out.">
				<Defs p="b2" />
				<Card x={20} y={36} w={920} h={344} r={22} />
				<rect x={20} y={12} width={250} height={34} rx={10} fill={A.yellow} stroke={A.ink} strokeWidth={2.5} />
				<Tx x={145} y={35} s={15}>
					heal-demo-healer
				</Tx>
				{cols.map((c, i) => {
					// Seven stage boxes, 118px wide and 10px apart, each wrapping its own pills
					const x = 36 + i * 128;
					return (
						<g key={c.n}>
							<rect x={x} y={62} width={118} height={240} rx={16} fill={A.paper2} stroke={A.ink} strokeWidth={2.5} className="ax-lit" style={lit(A.paper2, i * 1.14)} />
							<Ic I={c.I} x={x + 20} y={88} s={20} />
							<Tx x={x + 36} y={94} s={15} a="start">
								{c.n}
							</Tx>
							{i < cols.length - 1 ? <Edge p="b2" d={`M${x + 120} 88 L${x + 126} 88`} w={2} /> : null}
							{c.chips.map((t, j) => {
								const beat = c.n === 'Lease' && j === 2;
								return (
									<g key={t}>
										<rect x={x + 6} y={118 + j * 58} width={106} height={44} rx={22} fill={A.paper} stroke={A.ink} strokeWidth={1.5} />
										{beat ? (
											<g className="ax-beat">
												<Ic I={HeartPulse} x={x + 22} y={140 + j * 58} s={14} c={A.coral} />
											</g>
										) : null}
										<Tx x={x + (beat ? 66 : 59)} y={145 + j * 58} s={11} w={500} f="sans">
											{t}
										</Tx>
									</g>
								);
							})}
						</g>
					);
				})}
				<path d="M50 340 L910 340" stroke={A.sky} strokeWidth={3} className="ax-march" fill="none" />
				<rect x={140} y={326} width={680} height={28} rx={14} fill={A.white} stroke={A.ink} strokeWidth={1.5} />
				<Ic I={Database} x={164} y={340} s={16} />
				<Tx x={490} y={345} s={13} w={500} f="sans">
					every fact is appended to heal.log; incidents, runs and leases hold the current state
				</Tx>
			</Fig>

			<Fig n="B3" title="Level 3 · the fix loop" h={390} caption="A fixer edits its copy and verifies it against a fresh clone in its agent account. The healer's own checks and contract, an independent test reviewer and an approver that sees only the evidence must all agree; a denial or a merge conflict goes back to the fixer, at most twice. Before it ships, the healer merges onto any newer version of the pipeline, retests, and checks again.">
				<Defs p="b3" />
				<path d={loop} fill="none" stroke={A.ink} strokeWidth={3} className="ax-march" />
				<path d="M360 320 A240 130 0 0 1 120 190" fill="none" stroke={A.coral} strokeWidth={3.5} strokeDasharray="9 7" />
				<Tok d={loop} c={A.yellow} dur={7} r={10} />
				{[
					{ cx: 120, cy: 190, I: Bot, a: 'edit code', b: 'Claude fixer', f: A.yellow },
					{ cx: 360, cy: 60, I: Database, a: 'verify', b: 'fresh clone, as nobody', f: A.skyPale },
					{ cx: 600, cy: 190, I: FlaskConical, a: 'checks', b: 'contract, test review', f: A.white },
					{ cx: 360, cy: 320, I: Eye, a: 'approver', b: 'sees evidence only', f: A.white },
				].map((n) => (
					<g key={n.a}>
						<Card x={n.cx - 105} y={n.cy - 34} w={210} h={68} fill={n.f} />
						<Ic I={n.I} x={n.cx - 74} y={n.cy} s={26} />
						<Tx x={n.cx - 52} y={n.cy - 4} s={16} a="start">
							{n.a}
						</Tx>
						<Tx x={n.cx - 52} y={n.cy + 16} s={11.5} w={400} f="sans" a="start">
							{n.b}
						</Tx>
					</g>
				))}
				<rect x={0} y={34} width={176} height={32} rx={16} fill={A.white} stroke={A.ink} strokeWidth={2} />
				<Ic I={Inbox} x={20} y={50} s={17} />
				<Tx x={98} y={55} s={11.5} w={600} f="sans">
					new failures: notices
				</Tx>
				<Edge p="b3" d="M88 70 L104 150" />
				<Tx x={360} y={182} s={24}>
					≤ 2 rounds
				</Tx>
				<Tx x={360} y={208} s={13} w={400} f="sans">
					a denial or a conflict comes back
				</Tx>
				<Tx x={150} y={300} s={13} w={500} f="sans" c={A.coral}>
					denied
				</Tx>
				<Edge p="b3" d="M470 320 C 640 320, 700 300, 752 262" />
				<Tok d="M470 320 C 640 320, 700 300, 752 262" c={A.teal} dur={3} delay={1} />
				<Tx x={640} y={344} s={13} w={500} f="sans" c={A.teal}>
					approved
				</Tx>
				<Card x={760} y={150} w={180} h={120} fill={A.tealSoft} />
				<Ic I={GitMerge} x={850} y={180} s={28} />
				<Tx x={850} y={214} s={18}>
					sync & ship
				</Tx>
				<Tx x={850} y={236} s={12} w={400} f="sans">
					merge, retest, recheck,
				</Tx>
				<Tx x={850} y={254} s={12} w={400} f="sans">
					then propose or ship
				</Tx>
			</Fig>

			<Fig n="B4" title="Level 3 · who holds the keys" h={360} caption="The fixer holds no token: it can edit its own directory and run verify and inbox, nothing else. verify asks a broker inside the healer, which runs the fixer's code as the unprivileged nobody user with a short-lived token for an agent service account. That account reads production only through a restricted share granted to its role, and writes only to its own zero-copy clone. The owner's token never leaves the healer process.">
				<Defs p="b4" />
				<rect x={20} y={40} width={300} height={260} rx={22} fill={A.paper2} stroke={A.coral} strokeWidth={3} strokeDasharray="10 7" />
				<Tx x={38} y={70} s={13} a="start">
					AGENT WORKSPACE · NO TOKEN
				</Tx>
				<Card x={40} y={96} w={120} h={90} fill={A.yellow} />
				<Ic I={Bot} x={100} y={126} s={28} />
				<Tx x={100} y={168} s={15}>
					fixer
				</Tx>
				<Card x={182} y={96} w={120} h={90} />
				<Ic I={FileCode} x={242} y={126} s={28} />
				<Tx x={242} y={168} s={13}>
					pipeline.py
				</Tx>
				<Edge p="b4" d="M164 141 L176 141" />
				<rect x={40} y={218} width={262} height={46} rx={23} fill={A.white} stroke={A.ink} strokeWidth={2} />
				<Tx x={171} y={246} s={13} w={600} f="sans">
					only commands: verify, inbox
				</Tx>
				<Edge p="b4" d="M306 241 L374 241" />
				<rect x={360} y={150} width={210} height={170} rx={20} fill="none" stroke={A.orange} strokeWidth={2} strokeDasharray="6 6" />
				<Tx x={465} y={142} s={11.5} w={500} f="sans" c={A.orange}>
					inside the healer Flight
				</Tx>
				<Card x={380} y={186} w={170} h={100} fill={A.yellow} />
				<Ic I={KeyRound} x={465} y={214} s={26} />
				<Tx x={465} y={252} s={17}>
					broker
				</Tx>
				<Tx x={465} y={272} s={12} w={400} f="sans">
					runs it as nobody
				</Tx>
				<Ic I={Lock} x={398} y={306} s={16} c={A.orange} />
				<Tx x={412} y={311} s={11} w={500} f="sans" a="start" c={A.orange}>
					owner token stays here
				</Tx>
				<Cyl cx={465} cy={70} w={150} h={50} fill={A.white} />
				<Tx x={465} y={82} s={14}>
					heal_demo
				</Tx>
				<rect x={600} y={40} width={340} height={280} rx={22} fill={A.paper2} stroke={A.teal} strokeWidth={3} strokeDasharray="10 7" />
				<Tx x={618} y={70} s={13} a="start">
					AGENT SERVICE ACCOUNT
				</Tx>
				<rect x={630} y={88} width={290} height={44} rx={22} fill={A.white} stroke={A.ink} strokeWidth={2} />
				<Ic I={Lock} x={652} y={110} s={18} />
				<Tx x={785} y={115} s={12} w={600} f="sans">
					restricted share, read-only
				</Tx>
				<Edge p="b4" d="M544 76 C 580 76, 600 100, 624 106" />
				<Tx x={592} y={132} s={11} w={500} f="sans" a="end">
					granted to the role
				</Tx>
				<Edge p="b4" d="M775 136 L775 182" dash />
				<Tx x={787} y={164} s={11.5} w={500} f="sans" a="start">
					zero-copy clone
				</Tx>
				<Cyl cx={775} cy={238} w={180} h={70} />
				<Tx x={775} y={246} s={14}>
					heal_demo (clone)
				</Tx>
				<Edge p="b4" d="M556 236 L679 236" />
				<Tx x={775} y={300} s={11.5} w={500} f="sans">
					written with a short-lived token
				</Tx>
				<Tok d="M306 241 L374 241" c={A.yellow} dur={2.6} />
				<Tok d="M556 236 L679 236" c={A.sky} dur={1.3} delay={1.3} />
			</Fig>
		</>
	);
}

// ----- Option C: states and time -----
function ArchC({ q }: { q: ReturnType<typeof useSQLQuery> }) {
	const gx = (t: number) => 210 + t * 5.9;
	const lanes = ['lease', 'check', 'fixer 1', 'fixer 2', 'reviews', 'ship'];
	const ly = (i: number) => 50 + i * 42;
	const bars: { lane: number; a: number; b: number; c: string; t?: string; dots?: number[] }[] = [
		{ lane: 0, a: 0, b: 3, c: A.yellow },
		{ lane: 1, a: 3, b: 12, c: A.yellow },
		{ lane: 1, a: 60, b: 63, c: A.yellow },
		{ lane: 1, a: 117, b: 120, c: A.yellow },
		{ lane: 2, a: 14, b: 90, c: A.orange, t: 'fixer · agent account 1', dots: [56, 70, 84] },
		{ lane: 3, a: 14.1, b: 76, c: A.orange, t: 'fixer · agent account 2', dots: [52, 68] },
		{ lane: 4, a: 76, b: 86, c: A.tealSoft },
		{ lane: 4, a: 90, b: 102, c: A.tealSoft },
		{ lane: 5, a: 86, b: 92, c: A.sky },
		{ lane: 5, a: 102, b: 112, c: A.sky },
	];
	const mx = (m: number) => 110 + m * 31;
	const cyc = 12;
	const tAt = (m: number) => (m / 25) * cyc * 0.85;
	const runs: [number, boolean][] = [
		[1, true],
		[4, false],
		[8, true],
		[9.5, true],
		[13, true],
		[17, true],
		[21, true],
		[24.5, true],
	];
	return (
		<>
			<ArchHeader
				letter="C"
				title="The life of an incident"
				sub="An incident as a state machine, a healer run as a timeline, and how it all adds up over time."
				live={<LiveChip q={q} value={(r) => (N(r.n_guides) ? String(N(r.n_guides)) : null)} label="Guides written to memory" />}
			/>
			<Fig n="C1" title="Every incident is a little state machine" h={410} caption="Most incidents walk the middle path. Blips are only watched; at autonomy L3 a reviewed fix waits as proposed until you Approve or Reject it; an escalated incident waits for a human, who can Retry it from the Dive; and a failure after a fix reopens the incident.">
				<Defs p="c1" />
				<Tok d="M110 190 L900 190" c={A.white} r={9} dur={8} />
				{[
					{ cx: 110, l: 'open', f: A.coralSoft },
					{ cx: 350, l: 'investigating', f: A.yellow },
					{ cx: 590, l: 'verifying', f: A.skyPale },
					{ cx: 830, l: 'resolved', f: A.tealSoft },
				].map((s, i) => (
					<StatePill key={s.l} cx={s.cx} cy={190} w={180} fill={s.f} label={s.l} cls="ax-lit" style={lit(s.f, ((i * 240) / 790) * 8 - 0.48, A.yellow === s.f ? A.orange : A.yellow)} />
				))}
				<Edge p="c1" d="M204 190 L256 190" />
				<Edge p="c1" d="M444 190 L496 190" />
				<Edge p="c1" d="M684 190 L736 190" />
				<Tx x={230} y={232} s={12} w={500} f="sans">
					fix starts
				</Tx>
				<Tx x={470} y={232} s={12} w={500} f="sans">
					fix ships
				</Tx>
				<Tx x={710} y={232} s={12} w={500} f="sans">
					2 green runs
				</Tx>

				<StatePill cx={470} cy={62} w={170} fill={A.white} label="watching" dash="7 6" />
				<Edge p="c1" d="M130 160 Q 170 62 380 62" />
				<Edge p="c1" d="M560 62 Q 790 62 812 158" />
				<Tx x={182} y={92} s={12} w={500} f="sans" a="end">
					API blip
				</Tx>
				<Tx x={806} y={100} s={12} w={500} f="sans" a="start">
					green, no fix
				</Tx>

				<StatePill cx={250} cy={312} w={170} fill={A.orange} label="escalated" />
				<StatePill cx={500} cy={312} w={170} fill={A.white} label="proposed" dash="7 6" />
				<Edge p="c1" d="M330 220 L272 280" />
				<Edge p="c1" d="M380 220 L466 280" />
				<Edge p="c1" d="M540 282 L578 222" />
				<Tx x={250} y={356} s={12} w={500} f="sans">
					needs a human · Retry
				</Tx>
				<Tx x={500} y={356} s={12} w={500} f="sans">
					L3: waits for Approve
				</Tx>
				<StatePill cx={735} cy={312} w={150} fill={A.white} label="rejected" />
				<Edge p="c1" d="M590 312 L654 312" />
				<Tx x={735} y={356} s={12} w={500} f="sans">
					Reject · Retry
				</Tx>

				<path d="M860 220 L860 380 Q 860 395 845 395 L 95 395 Q 80 395 80 380 L80 228" fill="none" stroke={A.coral} strokeWidth={2.5} strokeDasharray="7 7" markerEnd="url(#c1-ah)" />
				<rect x={690} y={384} width={130} height={22} fill={A.paper} />
				<Tx x={755} y={400} s={12} w={600} f="sans" c={A.coral}>
					fails again → reopen
				</Tx>
			</Fig>

			<Fig n="C2" title="One healer run, second by second" y0={25} h={310} caption="One healer run fixes two broken pipelines in about two minutes. It checks for new failures every minute while the fixers work, and one that matters to fixer 1 reaches it as a short notice. The fixers overlap, so both take about as long as the slower one.">
				<Defs p="c2" />
				{[0, 30, 60, 90, 120].map((t) => (
					<g key={t}>
						<line x1={gx(t)} y1={40} x2={gx(t)} y2={300} stroke={A.ink} strokeOpacity={0.12} strokeWidth={1.5} />
						<Tx x={gx(t)} y={322} s={13} w={500}>
							{t === 120 ? '2 min' : `${t}s`}
						</Tx>
					</g>
				))}
				{lanes.map((l, i) => (
					<Tx key={l} x={190} y={ly(i) + 22} s={14} a="end">
						{l}
					</Tx>
				))}
				{bars.map((b, i) => (
					<g key={i}>
						<rect x={gx(b.a)} y={ly(b.lane)} width={Math.max(6, gx(b.b) - gx(b.a))} height={32} rx={8} fill={b.c} stroke={A.ink} strokeWidth={2} />
						{b.t ? (
							<Tx x={gx(b.a) + 12} y={ly(b.lane) + 21} s={12} w={600} f="sans" a="start">
								{b.t}
							</Tx>
						) : null}
						{(b.dots ?? []).map((d) => (
							<circle key={d} cx={gx(d)} cy={ly(b.lane) + 16} r={5} fill={A.white} stroke={A.ink} strokeWidth={1.5} />
						))}
					</g>
				))}
				<Tx x={gx(30)} y={ly(1) + 22} s={12} w={400} f="sans" c={A.muted}>
					○ = a verify call
				</Tx>
				<path d={`M${gx(63)} ${ly(1) + 30} L${gx(64)} ${ly(2) + 4}`} stroke={A.coral} strokeWidth={2.5} markerEnd="url(#c2-ah)" />
				<Tx x={gx(66)} y={ly(1) + 22} s={11.5} w={600} f="sans" a="start" c={A.coral}>
					new failure → notice
				</Tx>
				<rect x={205} y={38} width={725} height={264} fill={A.paper} className="ax-curtain ax-fast" />
				<line x1={0} y1={38} x2={0} y2={302} stroke={A.ink} strokeWidth={2.5} className="ax-play ax-fast" style={{ offsetPath: `path('M${gx(0)} 0 L${gx(120)} 0')` } as CSSV} />
			</Fig>

			<Fig n="C3" title="Across healer ticks, and into memory" h={350} caption="The healer wakes every 5 minutes: one tick opens and fixes, the next sees two green runs and resolves, and the lesson becomes a Guide a later fix can recall.">
				{[0, 5, 10, 15, 20, 25].map((m) => (
					<g key={m}>
						<line x1={mx(m)} y1={88} x2={mx(m)} y2={300} stroke={A.ink} strokeOpacity={0.12} strokeWidth={1.5} strokeDasharray="4 5" />
						<Tx x={mx(m)} y={36} s={13} w={500}>
							{m ? `${m} min` : '0'}
						</Tx>
						<g className="ax-pop" style={{ animationDelay: `${tAt(m)}s`, animationDuration: `${cyc}s` }}>
							<circle cx={mx(m)} cy={66} r={18} fill={A.yellow} stroke={A.ink} strokeWidth={2.5} />
							<Ic I={Clock} x={mx(m)} y={66} s={20} />
						</g>
					</g>
				))}
				<Tx x={78} y={71} s={13} a="end">
					healer
				</Tx>
				<Tx x={78} y={145} s={13} a="end">
					pipeline
				</Tx>
				<Tx x={78} y={215} s={13} a="end">
					incident
				</Tx>
				<Tx x={78} y={289} s={13} a="end">
					memory
				</Tx>
				{runs.map(([m, ok]) => (
					<g key={m} className="ax-pop" style={{ animationDelay: `${tAt(m)}s`, animationDuration: `${cyc}s` }}>
						<rect x={mx(m) - 13} y={127} width={26} height={26} rx={7} fill={ok ? A.tealSoft : A.coral} stroke={A.ink} strokeWidth={2} />
						{ok ? <Check x={mx(m)} y={140} /> : <Cross x={mx(m)} y={140} />}
					</g>
				))}
				<rect x={mx(5)} y={194} width={mx(7) - mx(5)} height={32} fill={A.yellow} stroke={A.ink} strokeWidth={2} />
				<Tx x={(mx(5) + mx(7)) / 2} y={215} s={12} w={600} f="sans">
					fix
				</Tx>
				<rect x={mx(7)} y={194} width={mx(10) - mx(7)} height={32} fill={A.skyPale} stroke={A.ink} strokeWidth={2} />
				<Tx x={(mx(7) + mx(10)) / 2} y={215} s={12} w={600} f="sans">
					verifying
				</Tx>
				<rect x={mx(10)} y={194} width={mx(25) - mx(10)} height={32} rx={0} fill={A.tealSoft} stroke={A.ink} strokeWidth={2} />
				<Tx x={(mx(10) + mx(25)) / 2} y={215} s={12} w={600} f="sans">
					resolved
				</Tx>
				<circle cx={mx(5)} cy={210} r={9} fill={A.coral} stroke={A.ink} strokeWidth={2} />
				<Tx x={mx(5)} y={186} s={11.5} w={600} f="sans" c={A.coral}>
					open
				</Tx>
				<circle cx={mx(10)} cy={284} r={20} fill={A.orange} stroke={A.ink} strokeWidth={2.5} />
				<Ic I={BookOpen} x={mx(10)} y={284} s={20} />
				<Tx x={mx(10)} y={326} s={12} w={500} f="sans">
					Guide saved
				</Tx>
				<path d={`M${mx(10) + 24} 278 Q ${mx(15.5)} 236 ${mx(21) - 24} 278`} fill="none" stroke={A.orange} strokeWidth={3} strokeDasharray="7 7" className="ax-march" />
				<Tok d={`M${mx(10) + 24} 278 Q ${mx(15.5)} 236 ${mx(21) - 24} 278`} c={A.orange} dur={3} />
				<circle cx={mx(21)} cy={284} r={20} fill={A.white} stroke={A.ink} strokeWidth={2.5} />
				<Ic I={Search} x={mx(21)} y={284} s={20} />
				<Tx x={mx(21)} y={326} s={12} w={500} f="sans">
					next similar break recalls it
				</Tx>
				<line x1={0} y1={48} x2={0} y2={304} stroke={A.ink} strokeWidth={2.5} className="ax-play" style={{ offsetPath: `path('M${mx(0)} 0 L${mx(25)} 0')`, animationDuration: `${cyc}s` } as CSSV} />
			</Fig>
		</>
	);
}

const ARCH_SUB: Record<string, string> = {
	archA: 'the 2 a.m. story',
	archB: 'zoom in',
	archC: 'lifecycle & timeline',
};
function ArchView({ which }: { which: string }) {
	const q = useSQLQuery(ARCH_SQL);
	return (
		<div className="ax-root" style={{ background: A.paper, margin: '14px -20px 0', padding: '4px 28px 24px', fontFamily: A.sans, color: A.ink }}>
			<style>{ARCH_CSS}</style>
			<div style={{ maxWidth: 1080, margin: '0 auto' }}>
				{which === 'archA' ? <ArchA q={q} /> : which === 'archB' ? <ArchB q={q} /> : <ArchC q={q} />}
			</div>
		</div>
	);
}

// ---------- main ----------
export default function SelfHealingBoard() {
	const [tab, setTab] = useDiveState<string>('tab', 'overview');
	const [incSel, setIncSel] = useDiveState<number>('incident', 0);
	const [comp, setComp] = useDiveState<string>('component', 'all');
	const [statusF, setStatusF] = useDiveState<string>('status', 'all');
	const [range, setRange] = useDiveState<string>('range', 'all');
	const [selRun, setSelRun] = useState('');
	const [example, setExample] = useState(false);

	const healthQ = useSQLQuery(HEALTH_SQL);
	const regQ = useSQLQuery(REG_SQL, { enabled: tab === 'overview' || tab === 'runs' });
	const runsQ = useSQLQuery(RUNS_SQL, { enabled: tab === 'overview' || tab === 'runs' });
	const incQ = useSQLQuery(INC_SQL, { enabled: tab === 'overview' || tab === 'incidents' });

	const health = rowsOf(healthQ)[0] ?? {};
	const now = NN(health.now_ms) ?? Date.now();
	const reg = rowsOf(regQ);
	const incs = rowsOf(incQ);
	const runs: RunRow[] = useMemo(
		() =>
			rowsOf(runsQ).map((r) => ({
				component: S(r.component),
				kind: S(r.kind),
				run: N(r.run_number),
				status: S(r.status),
				scheduled: B(r.is_scheduled),
				created: NN(r.created_ms),
				started: NN(r.started_ms),
				ended: NN(r.ended_ms),
				sha: S(r.sha),
				incident: NN(r.incident_id),
				incExists: B(r.inc_exists),
				fixerRunId: S(r.fixer_run_id),
				mode: S(r.mode),
				attempt: S(r.attempt),
				outcome: S(r.outcome),
			})),
		[runsQ.data],
	);
	const runsBy = useMemo(() => {
		const m: Record<string, RunRow[]> = {};
		for (const r of runs) (m[r.component] ??= []).push(r);
		Object.values(m).forEach((l) => l.sort((a, b) => a.run - b.run));
		return m;
	}, [runs]);
	const order = useMemo(() => {
		const o = reg.map((r) => S(r.name));
		for (const r of runs) if (!o.includes(r.component)) o.push(r.component);
		return o;
	}, [reg, runs]);
	const selectedInc = incs.find((r) => N(r.id) === incSel) ?? incs[0];
	const tabs = ['overview', 'approvals', 'runs', 'incidents', 'memory', 'archA', 'archB', 'archC'];
	const openIncident = (id: number) => {
		setIncSel(id);
		setTab('incidents');
	};
	const openRuns = (c: string) => {
		setComp(c);
		setTab('runs');
	};
	const refresh = () => [healthQ, regQ, runsQ, incQ].forEach((q) => q.refetch());

	const filtered = runs
		.filter((r) => (comp === 'all' || r.component === comp) && (statusF === 'all' || r.status === statusF))
		.sort((a, b) => (b.started ?? b.created ?? 0) - (a.started ?? a.created ?? 0));
	const ganttOrder = comp === 'all' ? order.filter((c) => runsBy[c]?.length) : [comp];

	return (
		<main style={{ background: T.bg, color: T.text, fontFamily: T.font, minHeight: '100vh', padding: '16px 20px', maxWidth: 1280, margin: '0 auto' }}>
			<header className="flex flex-wrap items-end justify-between" style={{ gap: 8 }}>
				<div>
					<h1 className="text-2xl font-semibold" style={{ color: T.text }}>
						Self-healing pipelines
					</h1>
					<p style={{ fontSize: 12, color: T.muted }}>
						Flights that detect, diagnose and fix their own failures · runs snapshot {ago(NN(health.snapshot_ms), now)} · times in your local zone
					</p>
				</div>
				<button style={btn()} onClick={refresh}>
					Refresh
				</button>
			</header>
			<nav className="flex flex-wrap" style={{ gap: 4, marginTop: 12, borderBottom: `1px solid ${T.line}` }}>
				{tabs.map((t) => (
					<button
						key={t}
						onClick={() => setTab(t)}
						style={{
							fontSize: 12,
							fontWeight: tab === t ? 700 : 500,
							padding: '6px 12px',
							cursor: 'pointer',
							color: tab === t ? T.text : T.muted,
							background: 'transparent',
							borderBottom: `2px solid ${tab === t ? T.blue : 'transparent'}`,
							textTransform: 'capitalize',
						}}
					>
						{ARCH_SUB[t] ? `Architecture ${t.slice(4)}` : t}
						{ARCH_SUB[t] ? <span style={{ color: T.faint, fontWeight: 400, textTransform: 'none' }}> · {ARCH_SUB[t]}</span> : null}
						{t === 'incidents' && N(health.n_all) ? <span style={{ color: T.faint, fontWeight: 400 }}> {N(health.n_all)}</span> : null}
						{t === 'approvals' && N(health.n_pending) ? <span style={{ color: T.purple, fontWeight: 700 }}> {N(health.n_pending)}</span> : null}
					</button>
				))}
			</nav>

			{healthQ.isError ? <ErrorLine error={healthQ.error} /> : null}

			{tab === 'overview' ? (
				<>
					<div style={{ marginTop: 14 }}>
						<HealthStrip q={healthQ} now={now} />
					</div>
					{N(health.n_pending) ? (
						<div style={{ marginTop: 12, padding: '8px 12px', borderLeft: `3px solid ${T.purple}`, background: '#f4effa', fontSize: 12 }}>
							<b>
								{N(health.n_pending)} fix{N(health.n_pending) > 1 ? 'es' : ''} passed every check and review and wait{N(health.n_pending) > 1 ? '' : 's'} for your approval.
							</b>{' '}
							<span style={linkStyle} onClick={() => setTab('approvals')}>
								Review them
							</span>
						</div>
					) : null}
					<AlertsPanel now={now} onIncident={openIncident} />
					<Section>
						<SectionTitle note="click a node for its runs · badge = active incident (filled = root, outline = downstream symptom)">Pipeline DAG</SectionTitle>
						{(regQ.isLoading && !regQ.data) || (runsQ.isLoading && !runsQ.data) ? (
							<Skeleton h={300} />
						) : regQ.isError ? (
							<ErrorLine error={regQ.error} />
						) : runsQ.isError ? (
							<ErrorLine error={runsQ.error} />
						) : !reg.length ? (
							<Empty>No pipeline runs yet.</Empty>
						) : (
							<PipelineDag reg={reg} runsBy={runsBy} now={now} onOpen={openRuns} onIncident={openIncident} />
						)}
						<div className="flex flex-wrap" style={{ gap: 10, fontSize: 10, color: T.muted, marginTop: 4 }}>
							{Object.entries(RUN_COLOR).map(([k, c]) => (
								<span key={k}>
									<span style={{ display: 'inline-block', width: 8, height: 8, background: c, borderRadius: 1, marginRight: 3 }} />
									{k.toLowerCase()}
								</span>
							))}
							<span>· last 15 runs per node, oldest left</span>
						</div>
					</Section>
					<Section>
						<SectionTitle note={incs.length > 5 ? `5 of ${incs.length}` : undefined}>Recent incidents</SectionTitle>
						{incQ.isLoading && !incQ.data ? (
							<Skeleton h={80} />
						) : incQ.isError ? (
							<ErrorLine error={incQ.error} />
						) : !incs.length ? (
							<Empty>No incidents yet. Failed runs become incidents on the healer's next check (every 5 minutes, or every minute while it is fixing).</Empty>
						) : (
							<IncidentTable rows={incs} selected={-1} onSelect={openIncident} now={now} limit={5} />
						)}
					</Section>
					<AgentsPanel now={now} />
				</>
			) : null}

			{tab === 'approvals' ? <ApprovalsView now={now} onIncident={openIncident} onChange={refresh} /> : null}

			{tab === 'runs' ? (
				<>
					<Section>
						<SectionTitle
							note={
								<span className="flex flex-wrap items-center" style={{ gap: 4, justifyContent: 'flex-end' }}>
									<select style={sel} value={comp} onChange={(e) => setComp(e.target.value)}>
										<option value="all">all components</option>
										{order.map((c) => (
											<option key={c} value={c}>
												{c}
											</option>
										))}
									</select>
									<select style={sel} value={statusF} onChange={(e) => setStatusF(e.target.value)}>
										<option value="all">all statuses</option>
										{Object.keys(RUN_COLOR).map((s) => (
											<option key={s} value={s}>
												{s.toLowerCase()}
											</option>
										))}
									</select>
									{Object.keys(RANGES).map((k) => (
										<button key={k} style={btn(range === k)} onClick={() => setRange(k)}>
											{k}
										</button>
									))}
								</span>
							}
						>
							Run timeline
						</SectionTitle>
						{runsQ.isLoading && !runsQ.data ? (
							<Skeleton h={200} />
						) : runsQ.isError ? (
							<ErrorLine error={runsQ.error} />
						) : !filtered.length ? (
							<Empty>No runs match these filters.</Empty>
						) : (
							<Gantt
								runs={filtered}
								order={ganttOrder}
								now={now}
								range={range}
								selected={selRun}
								onSelect={(r) => (r.component === 'fixer' && r.incident && r.incExists ? openIncident(r.incident) : setSelRun(`${r.component}#${r.run}`))}
							/>
						)}
						<div style={{ fontSize: 10, color: T.muted, marginTop: 4 }}>
							Bars run from start to end (running bars extend to now, dashed line). Fixer bars open their incident. heal.main.runs keeps the last ~25 runs per Flight.
						</div>
					</Section>
					<Section>
						<SectionTitle note={`${filtered.length} runs${filtered.length > 80 ? ', newest 80 shown' : ''}`}>Runs</SectionTitle>
						{filtered.length ? (
							<div style={{ overflowX: 'auto' }}>
								<table style={{ width: '100%', borderCollapse: 'collapse' }}>
									<thead>
										<tr>
											{['Component', 'Run', 'Status', 'Started', 'Duration', 'Trigger', 'SHA', 'Incident'].map((h) => (
												<th key={h} style={th}>
													{h}
												</th>
											))}
										</tr>
									</thead>
									<tbody>
										{filtered.slice(0, 80).map((r) => {
											const key = `${r.component}#${r.run}`;
											const dur = r.started != null ? (r.ended ?? (r.status === 'RUNNING' ? now : null) ?? r.started) - r.started : null;
											return (
												<tr key={key} style={{ background: selRun === key ? '#e9f2f8' : undefined }} onClick={() => setSelRun(key)}>
													<td style={{ ...td, fontWeight: 600 }}>{r.component}</td>
													<td style={td}>{r.run}</td>
													<td style={td}>
														<RunPill status={r.status} />
													</td>
													<td style={{ ...td, whiteSpace: 'nowrap', fontSize: 11 }} title={stamp(r.started ?? r.created)}>
														{stamp(r.started ?? r.created)} <span style={{ color: T.muted }}>({ago(r.started ?? r.created, now)})</span>
													</td>
													<td style={{ ...td, whiteSpace: 'nowrap' }}>{fmtDur(dur)}</td>
													<td style={{ ...td, fontSize: 11, color: T.muted }}>{r.scheduled ? 'schedule' : 'manual / API'}</td>
													<td style={{ ...td, fontFamily: T.mono, fontSize: 10 }} title={r.sha}>
														{r.sha.slice(0, 10)}
														{r.sha.includes('+p') ? <span style={{ color: T.purple }}> {r.sha.slice(r.sha.indexOf('+p'))}</span> : null}
													</td>
													<td style={{ ...td, fontSize: 11 }}>
														{r.incident ? (
															r.incExists ? (
																<span style={linkStyle} onClick={() => openIncident(r.incident as number)}>
																	#{r.incident}
																	{r.attempt ? ` a${r.attempt}` : ''}
																	{r.outcome ? ` · ${r.outcome}` : ''}
																</span>
															) : (
																<span style={{ color: T.faint }} title="incident row no longer in the incidents table">
																	#{r.incident} (gone)
																</span>
															)
														) : (
															''
														)}
													</td>
												</tr>
											);
										})}
									</tbody>
								</table>
							</div>
						) : null}
					</Section>
				</>
			) : null}

			{tab === 'incidents' ? (
				<>
					<div className="flex items-center justify-end" style={{ gap: 6, marginTop: 10, fontSize: 11, color: T.muted }}>
						<label className="flex items-center" style={{ gap: 4, cursor: 'pointer' }}>
							<input type="checkbox" checked={example} onChange={(e) => setExample(e.target.checked)} style={{ width: 12, height: 12, appearance: 'auto' as never }} />
							Show example loop
						</label>
					</div>
					{example ? (
						<Section>
							<SectionTitle note="hard-coded example, not from the database">Example healing DAG</SectionTitle>
							<AttemptCard fr={EXAMPLE_FR} actions={EXAMPLE_ACTIONS} example />
						</Section>
					) : null}
					<Section>
						<SectionTitle note="newest first · click a row for its detail">Incidents</SectionTitle>
						{incQ.isLoading && !incQ.data ? (
							<Skeleton h={100} />
						) : incQ.isError ? (
							<ErrorLine error={incQ.error} />
						) : !incs.length ? (
							<Empty>No incidents yet. Failed runs become incidents on the healer's next check (every 5 minutes, or every minute while it is fixing). Tick "Show example loop" to see how a healing DAG renders.</Empty>
						) : (
							<IncidentTable rows={incs} selected={N(selectedInc?.id)} onSelect={setIncSel} now={now} />
						)}
					</Section>
					{selectedInc ? (
						<Section>
							<IncidentDetail key={N(selectedInc.id)} inc={selectedInc} now={now} onGuides={() => setTab('memory')} onApprovals={() => setTab('approvals')} onChange={refresh} />
						</Section>
					) : null}
				</>
			) : null}

			{tab === 'memory' ? <MemoryView now={now} /> : null}
			{ARCH_SUB[tab] ? <ArchView which={tab} /> : null}
		</main>
	);
}
