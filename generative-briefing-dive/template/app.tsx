// ---------------------------------------------------------------------------
// Generative Dive app (template): renders specs written by the recipe's Flight
// with the kit in template/kit.tsx. Per-recipe values come from APP.
//
//   Flight  -> <db>.main.dive_specs (one JSON spec per briefing, question or investigation)
//   Dive    -> reads specs, renders blocks via REGISTRY, runs the Flight to ask
//   Viewer  -> drags blocks between preset rows, switches chart type, hides blocks;
//              stored per spec in useDiveState('layout')
//
// Precedence: viewer layout overlay > default layout derived from the spec.
// ---------------------------------------------------------------------------

// @imports
import { useEffect, useMemo, useRef, useState } from 'react';
import { useDiveState, useFlightRun, useRunFlight, useSQLQuery } from '@motherduck/react-sql-query';
import {
	ArrowDown,
	ArrowLeft,
	BookOpen,
	Brain,
	ShieldCheck,
	ArrowUp,
	CalendarDays,
	ChevronDown,
	ChevronRight,
	Clock,
	SearchCheck,
	MessageSquare,
	Newspaper,
	Eye,
	EyeOff,
	GripVertical,
	LayoutGrid,
	Loader2,
	Palette,
	RefreshCw,
	RotateCcw,
	Sparkles,
	Wand2,
	X,
} from 'lucide-react';
// @imports-end

// @recipe: build.py writes REQUIRED_DATABASES and APP from recipes/<name>/app.json here
// @recipe-end

const FLIGHT_ID = APP.flightId;
const SPECS = `"${APP.db}"."main"."dive_specs"`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const EXAMPLES: string[] = APP.examples;

const STEPS = [
	{ at: 0, label: 'Starting Flight' },
	{ at: 4, label: 'Choosing datasets and components' },
	{ at: 10, label: 'Running queries' },
	{ at: 13, label: 'Writing headline and annotations' },
	{ at: 24, label: 'Publishing' },
];

// Preset row containers. A row holds one block per column.
const TEMPLATES: Record<string, { label: string; cols: number[] }> = {
	full: { label: 'Full width', cols: [12] },
	halves: { label: 'Halves', cols: [6, 6] },
	wideLeft: { label: 'Two thirds + one third', cols: [8, 4] },
	wideRight: { label: 'One third + two thirds', cols: [4, 8] },
	thirds: { label: 'Thirds', cols: [4, 4, 4] },
};
const TEMPLATE_KEYS = Object.keys(TEMPLATES);

const newRequestId = () => `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

function since(iso: string) {
	const d = new Date(String(iso).replace(' ', 'T') + 'Z');
	const s = (Date.now() - d.getTime()) / 1000;
	if (!Number.isFinite(s)) return '';
	if (s < 90) return 'just now';
	if (s < 3600) return `${Math.round(s / 60)} min ago`;
	if (s < 86400) return `${Math.round(s / 3600)} h ago`;
	return `${Math.round(s / 86400)} d ago`;
}

// ---- Generation: ask a question, run the Flight, follow it ------------------------

function useGenerator(onDone: (requestId: string) => void) {
	const start = useRunFlight(FLIGHT_ID);
	const [pending, setPending] = useState<any>(null);
	const pendingRef = useRef<any>(null);
	pendingRef.current = pending;
	const [now, setNow] = useState(Date.now());
	const run = useFlightRun(FLIGHT_ID, start.data?.runNumber, {
		onFinished: (r: any) => {
			if (r.status === 'SUCCEEDED' && pendingRef.current) onDone(pendingRef.current.requestId);
		},
	});
	const busy = start.isPending || (pending != null && !run.isFinished && !start.error);
	useEffect(() => {
		if (!busy) return;
		const id = setInterval(() => setNow(Date.now()), 500);
		return () => clearInterval(id);
	}, [busy]);
	const generate = (mode: 'ask' | 'briefing' | 'investigate', prompt: string, parent = '', asOf = '', feedback = '') => {
		const requestId = newRequestId();
		setPending({ requestId, prompt, mode, parent, asOf, startedAt: Date.now() });
		// Viewer events ride along: a Dive can't write, the Flight records them.
		start.run({ MODE: mode, PROMPT: prompt, REQUEST_ID: requestId, PARENT: parent, AS_OF: asOf, FEEDBACK: feedback });
	};
	const startErr = start.error ? String(start.error.message || start.error) : null;
	const friendlyStartErr =
		startErr && /permission|forbidden|denied|not authori[sz]ed|403|not found|own/i.test(startErr)
			? `You can't run the generator. Ask the owner of this Dive for run access to Flight ${FLIGHT_ID}.`
			: startErr;
	return {
		generate,
		busy,
		pending,
		elapsed: pending ? (now - pending.startedAt) / 1000 : 0,
		status: run.status,
		failed: run.isFinished && run.status !== 'SUCCEEDED',
		error: friendlyStartErr || (run.error ? String(run.error.message || run.error) : null),
		clear: () => setPending(null),
	};
}

// ---- Floating ask bar --------------------------------------------------------------

function AskBar({ gen, draft, setDraft, ask, suggestions }: any) {
	const t = useTheme();
	const [focus, setFocus] = useState(false);
	const inputRef = useRef<HTMLInputElement>(null);
	const step = STEPS.filter((s) => gen.elapsed >= s.at).slice(-1)[0];
	const showSuggestions = focus && !gen.busy && !draft.trim();
	const failed = gen.pending && (gen.error || gen.failed);
	return (
		<div style={{ position: 'fixed', left: '50%', bottom: 20, transform: 'translateX(-50%)', width: 'min(720px, calc(100% - 32px))', zIndex: 40 }}>
			{showSuggestions && (
				<div className="mb-2 p-2" style={{ background: t.panel, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: t.radius, boxShadow: '0 12px 32px rgba(0,0,0,0.12)' }}>
					<div className="flex items-center gap-1.5 px-2 pb-1 pt-0.5 text-xs" style={{ color: t.muted }}><Sparkles size={12} /> Suggested questions
					</div>
					{suggestions.map((q: string) => (
						<button
							key={q}
							onMouseDown={(e) => {
								e.preventDefault();
								ask(q);
							}}
							className="block w-full px-2 py-1.5 text-left text-sm"
							style={{ color: t.text, cursor: 'pointer', borderRadius: Math.min(t.radius, 6) }}
							onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = t.name === 'Night' ? '#1c222b' : '#f3f3f3')}
							onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = 'transparent')}
						><span className="inline-flex items-center gap-2"><MessageSquare size={13} style={{ color: t.muted, flexShrink: 0 }} />{q}</span>
						</button>
					))}
				</div>
			)}
			<div style={{ background: t.panel, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: t.flat ? 0 : Math.max(t.radius, 10), boxShadow: '0 12px 32px rgba(0,0,0,0.14)', overflow: 'hidden' }}>
				{gen.pending && !failed && (
					<div className="flex items-center gap-2 px-4 pt-2.5 text-xs" style={{ color: t.muted }}>
						<Loader2 size={12} className="animate-spin" />
						<span style={{ color: t.text }}>{step?.label}</span>
						<span>· {gen.elapsed.toFixed(0)} s</span>
						<span className="flex-1" />
						<span className="truncate" style={{ maxWidth: 280 }}>
							{gen.pending.mode === 'briefing' ? (gen.pending.asOf ? `Briefing as of ${gen.pending.asOf}` : 'Daily briefing') : gen.pending.mode === 'investigate' ? `Investigating: ${gen.pending.prompt}` : gen.pending.prompt}
						</span>
					</div>
				)}
				{failed && (
					<div className="flex items-start gap-2 px-4 pt-2.5 text-xs" style={{ color: t.tones.critical.fg }}>
						<span className="flex-1">{gen.error || `The generator stopped (${gen.status}). Try rephrasing the question.`}</span>
						<button onClick={gen.clear} title="Dismiss" style={{ cursor: 'pointer' }}>
							<X size={13} />
						</button>
					</div>
				)}
				{/* No <form>: the Dive sandbox blocks form submission. */}
				<div className="flex items-center gap-2 py-2 pl-4 pr-2">
					<Sparkles size={15} style={{ color: t.flat ? t.accent : t.muted, flexShrink: 0 }} />
					<input
						ref={inputRef}
						value={draft}
						onChange={(e) => setDraft(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === 'Enter') ask(draft);
							if (e.key === 'Escape') inputRef.current?.blur();
						}}
						onFocus={() => setFocus(true)}
						onBlur={() => setFocus(false)}
						placeholder={APP.placeholder || 'Ask a question…'}
						className="min-w-0 flex-1 bg-transparent py-1.5 text-base outline-none"
						style={{ color: t.text }}
						maxLength={600}
					/>
					<Button variant="primary" disabled={gen.busy || !draft.trim()} onClick={() => ask(draft)}>
						{gen.busy ? <Loader2 size={14} className="animate-spin" /> : null}
						{gen.busy ? 'Generating' : 'Ask'}
					</Button>
				</div>
				{gen.pending && !failed && (
					<div style={{ height: 2, background: t.line }}>
						<div style={{ height: 2, width: `${Math.min(96, (gen.elapsed / 22) * 100)}%`, background: t.accent, transition: 'width 500ms linear' }} />
					</div>
				)}
			</div>
		</div>
	);
}

// ---- Pages menu (history) ----------------------------------------------------------

const KIND_ICON = (kind: string, size = 13) => (kind === 'briefing' ? <Newspaper size={size} /> : kind === 'investigation' ? <SearchCheck size={size} /> : <MessageSquare size={size} />);
const KIND_LABEL: Record<string, string> = { briefing: 'Daily briefing', question: 'Answer', investigation: 'Investigation' };

// Newest first, with each investigation listed under the page it came from.
function nestPages(rows: any[]) {
	const ids = new Set(rows.map((r) => r.id));
	const kids: Record<string, any[]> = {};
	rows.forEach((r) => r.parent && ids.has(r.parent) && (kids[r.parent] = [...(kids[r.parent] || []), r]));
	const out: any[] = [];
	const walk = (r: any, depth: number) => {
		out.push({ r, depth });
		(kids[r.id] || []).forEach((k) => walk(k, Math.min(depth + 1, 3)));
	};
	rows.filter((r) => !r.parent || !ids.has(r.parent)).forEach((r) => walk(r, 0));
	return out;
}

function PagesMenu({ rows, selected, onSelect }: any) {
	const t = useTheme();
	const [open, setOpen] = useState(false);
	const cur = rows.find((r: any) => r.id === selected);
	return (
		<div style={{ position: 'relative' }}>
			<Button variant="outline" size="sm" onClick={() => setOpen(!open)} style={{ maxWidth: 320 }}>
				<span className="truncate">{cur ? (cur.kind === 'briefing' ? cur.title : cur.prompt) : 'Pages'}</span>
				<ChevronDown size={13} />
			</Button>
			{open && (
				<>
					<div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
					<div className="p-1" style={{ position: 'absolute', right: 0, top: 'calc(100% + 6px)', width: 380, maxHeight: '60vh', overflowY: 'auto', zIndex: 31, background: t.panel, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: t.radius, boxShadow: '0 12px 32px rgba(0,0,0,0.14)' }}>
						{nestPages(rows).map(({ r, depth }: any) => {
							const on = r.id === selected;
							return (
								<button
									key={r.id}
									onClick={() => {
										onSelect(r.id);
										setOpen(false);
									}}
									className="flex w-full items-baseline gap-3 py-2 pr-3 text-left"
									style={{ cursor: 'pointer', borderLeft: `2px solid ${on ? t.accent : 'transparent'}`, color: t.text, paddingLeft: 12 + depth * 18 }}
								>
									<span className="shrink-0" style={{ color: t.muted, alignSelf: 'center' }}>{KIND_ICON(r.kind, 14)}</span>
									<span className="min-w-0 flex-1 text-sm leading-snug" style={{ fontWeight: on ? 700 : 400, color: r.status === 'failed' ? t.muted : t.text }}>
										{r.kind === 'briefing' ? r.title : r.prompt}
										{r.kind === 'briefing' && r.as_of && <span style={{ color: t.muted, fontWeight: 400 }}> · as of {fmtPeriod(r.as_of)}</span>}
										{r.status === 'failed' && ' (failed)'}
									</span>
									<span className="shrink-0 text-xs" style={{ color: t.muted }}>
										{since(r.created)}
									</span>
								</button>
							);
						})}
					</div>
				</>
			)}
		</div>
	);
}

// ---- Layout: rows of preset containers ---------------------------------------------

type Row = { t: string; slots: (string | null)[] };

// Reading order: numbers, then charts (any registered chart type), insights, tables, text.
const RANK: Record<string, number> = { kpi: 0, table: 3, text: 4, callouts: 2, timeline: 3 };
const isChart = (b: any) => !(b.type in RANK);

function templateFor(spans: number[]) {
	return TEMPLATE_KEYS.find((k) => spans.every((s, i) => TEMPLATES[k].cols[i] === s));
}

// Default arrangement: KPIs, then charts, callouts after the first chart row, then tables.
function defaultRows(blocks: any[], kind = ''): Row[] {
	const ordered = [...blocks].sort((a, b) => (RANK[a.type] ?? 1) - (RANK[b.type] ?? 1));
	const callouts = ordered.filter((b) => b.type === 'callouts');
	const rest = ordered.filter((b) => b.type !== 'callouts');
	const rows: Row[] = [];
	let cur: any[] = [];
	const flush = () => {
		if (!cur.length) return;
		const spans = cur.map((b) => b.span);
		const t = templateFor(spans) || (cur.length === 1 ? 'full' : cur.length === 2 ? 'halves' : 'thirds');
		const slots = TEMPLATES[t].cols.map((_, i) => cur[i]?.id ?? null);
		rows.push({ t, slots });
		cur = [];
	};
	for (const b of rest) {
		const span = [4, 6, 8, 12].includes(b.span) ? b.span : 12;
		const b2 = { ...b, span };
		if (span === 12 || b.type === 'kpi') {
			flush();
			cur = [b2];
			flush();
			continue;
		}
		if (!templateFor([...cur.map((x) => x.span), span])) flush();
		cur.push(b2);
		if (cur.reduce((s, x) => s + x.span, 0) >= 12) flush();
	}
	flush();
	if (callouts.length) {
		// The overview leads with its insights; other pages show evidence first.
		const firstChart = rows.findIndex((r) => r.slots.some((id) => blocks.find((b) => b.id === id && isChart(b))));
		const kpiRow = rows.findIndex((r) => r.slots.some((id) => blocks.find((b) => b.id === id && b.type === 'kpi')));
		const at = kind === 'briefing' && kpiRow >= 0 ? kpiRow + 1 : firstChart >= 0 ? firstChart + 1 : Math.min(1, rows.length);
		callouts.forEach((c, i) => rows.splice(at + i, 0, { t: 'full', slots: [c.id] }));
	}
	return rows;
}

function normalize(rows: Row[], ids: string[], hidden: string[]): Row[] {
	const seen = new Set<string>();
	const out: Row[] = [];
	for (const r of rows) {
		if (!TEMPLATES[r.t]) continue;
		const slots = TEMPLATES[r.t].cols.map((_, i) => {
			const id = r.slots[i] ?? null;
			if (!id || !ids.includes(id) || hidden.includes(id) || seen.has(id)) return null;
			seen.add(id);
			return id;
		});
		if (slots.some(Boolean)) out.push({ t: r.t, slots });
	}
	ids.filter((id) => !seen.has(id) && !hidden.includes(id)).forEach((id) => out.push({ t: 'full', slots: [id] }));
	return out;
}

function useLayout(specId: string | null, blocks: any[], kind = '', learned: Record<string, string> = {}, signal: (e: any) => void = () => {}) {
	const [all, setAll] = useDiveState<Record<string, any>>('layout', {});
	const mine = (specId && all?.[specId]) || {};
	const ids = blocks.map((b) => b.id);
	const hidden: string[] = (mine.hidden || []).filter((id: string) => ids.includes(id));
	const rows = normalize(mine.rows || defaultRows(blocks, kind), ids, hidden);
	const typeOf = (id: string) => blocks.find((b) => b.id === id)?.type || '';
	const save = (patch: any) => {
		if (!specId) return;
		const next = { ...mine, rows, hidden, ...patch };
		const trimmed: Record<string, any> = {};
		Object.keys(all || {})
			.filter((k) => k !== specId)
			.slice(-8)
			.forEach((k) => (trimmed[k] = all[k]));
		trimmed[specId] = next;
		setAll(trimmed);
	};
	const clone = () => rows.map((r) => ({ t: r.t, slots: [...r.slots] }));
	const locate = (rs: Row[], id: string) => {
		for (let r = 0; r < rs.length; r++) {
			const s = rs[r].slots.indexOf(id);
			if (s >= 0) return [r, s];
		}
		return null;
	};
	return {
		rows,
		hidden,
		dirty: Object.keys(mine).length > 0,
		// The viewer's choice wins; then what viewers chose most for this block type; then the generated type.
		variant: (b: any) => mine.variant?.[b.id] ?? (learned[b.type] && variantsFor(b).includes(learned[b.type]) ? learned[b.type] : b.type),
		setVariant: (id: string, v: string) => {
			signal({ e: 'variant', t: typeOf(id), v });
			save({ variant: { ...(mine.variant || {}), [id]: v } });
		},
		// Drop on a slot: move there; if it holds another block, swap the two.
		moveTo: (id: string, r: number, s: number) => {
			signal({ e: 'layout', t: typeOf(id), v: `row ${r + 1}` });
			const rs = clone();
			const from = locate(rs, id);
			const other = rs[r]?.slots[s] ?? null;
			if (from && from[0] === r && from[1] === s) return;
			if (from) rs[from[0]].slots[from[1]] = other;
			else if (other) rs.push({ t: 'full', slots: [other] });
			rs[r].slots[s] = id;
			save({ rows: rs, hidden: hidden.filter((h) => h !== id) });
		},
		// Drop between rows: a new full-width row with this block.
		newRow: (id: string, at: number) => {
			signal({ e: 'layout', t: typeOf(id), v: `row ${at + 1}` });
			const rs = clone();
			const from = locate(rs, id);
			if (from) rs[from[0]].slots[from[1]] = null;
			rs.splice(at, 0, { t: 'full', slots: [id] });
			save({ rows: rs, hidden: hidden.filter((h) => h !== id) });
		},
		setTemplate: (r: number, key: string) => {
			const rs = clone();
			const filled = rs[r].slots.filter(Boolean) as string[];
			const n = TEMPLATES[key].cols.length;
			rs[r] = { t: key, slots: TEMPLATES[key].cols.map((_, i) => filled[i] ?? null) };
			const overflow = filled.slice(n);
			if (overflow.length) rs.splice(r + 1, 0, { t: overflow.length === 1 ? 'full' : 'halves', slots: overflow });
			save({ rows: rs });
		},
		moveRow: (r: number, dir: -1 | 1) => {
			const rs = clone();
			const j = r + dir;
			if (j < 0 || j >= rs.length) return;
			[rs[r], rs[j]] = [rs[j], rs[r]];
			save({ rows: rs });
		},
		hide: (id: string) => {
			signal({ e: 'hide', t: typeOf(id) });
			save({ hidden: [...hidden, id] });
		},
		show: (id: string) => {
			save({ hidden: hidden.filter((h) => h !== id), rows: [...rows, { t: 'full', slots: [id] }] });
		},
		reset: () => {
			if (!specId) return;
			const next = { ...(all || {}) };
			delete next[specId];
			setAll(next);
		},
	};
}

function TemplateIcon({ cols, active }: any) {
	const t = useTheme();
	return (
		<span className="inline-flex gap-0.5" style={{ width: 30, height: 14 }}>
			{cols.map((c: number, i: number) => (
				<span key={i} style={{ flex: c, background: active ? t.text : 'transparent', border: `1px solid ${active ? t.text : t.muted}` }} />
			))}
		</span>
	);
}

// Spans to render a row with empty slots outside Arrange mode: the last filled
// block takes the width of the empty ones.
function viewSpans(row: Row) {
	const cols = TEMPLATES[row.t].cols;
	const items = row.slots.map((id, i) => ({ id, span: cols[i] })).filter((x) => x.id);
	const free = 12 - items.reduce((s, x) => s + x.span, 0);
	if (items.length) items[items.length - 1].span += free;
	return items as { id: string; span: number }[];
}

// ---- Page ----------------------------------------------------------------------------

function BlockCard({ b, layout, arrange, flash, onDraft, jump, nav }: any) {
	const t = useTheme();
	const entry = REGISTRY[b.type];
	const body = <BlockView block={b} variant={layout.variant(b)} onJump={jump} nav={arrange ? null : nav} />;
	if (!arrange && entry && !entry.chrome) return body;
	const variants = variantsFor(b);
	const isKpi = b.type === 'kpi';
	return (
		<Card
			title={isKpi && !arrange ? null : b.title}
			icon={componentIcon(layout.variant(b)) || componentIcon(b.type)}
			description={isKpi || arrange ? null : b.caption}
			highlighted={flash === b.id}
			style={{ height: '100%' }}
			actions={
				arrange ? (
					<>
						{variants.length > 1 && <Segmented options={variants.map((v: string) => ({ value: v, label: componentLabel(v) }))} value={layout.variant(b)} onChange={(v: string) => layout.setVariant(b.id, v)} />}
						<Button variant="ghost" size="icon" onClick={() => layout.hide(b.id)} title="Hide">
							<EyeOff size={13} />
						</Button>
					</>
				) : b.type !== 'callouts' && !isKpi ? (
					<Button variant="ghost" size="icon" title="Ask a follow-up about this" onClick={() => onDraft(`${b.title}: why, and what is driving it?`)}>
						<Wand2 size={13} />
					</Button>
				) : null
			}
			footer={!arrange && b.source ? <SourceFooter block={b} /> : null}
		>
			{arrange ? <div style={{ maxHeight: 150, overflow: 'hidden', pointerEvents: 'none', opacity: 0.55, maskImage: 'linear-gradient(black 60%, transparent)' }}>{body}</div> : body}
			{!arrange && <BlockInsight block={b} />}
		</Card>
	);
}

// What this page drew on: memory, Guides, the analyst's follow-up queries, the number check.
function ContextLine({ spec }: any) {
	const t = useTheme();
	const c = spec.context;
	const k = spec.checks;
	if (!c && !k) return null;
	const item = (Icon: any, text: string, title: string) => (
		<span className="inline-flex items-center gap-1" title={title}>
			<Icon size={13} /> {text}
		</span>
	);
	return (
		<>
			{c?.briefings_remembered > 0 && item(Brain, `Remembers ${c.briefings_remembered} earlier briefing${c.briefings_remembered > 1 ? 's' : ''}`, `${c.earlier_insights} earlier insights and ${c.feedback_used} viewer signals informed this page`)}
			{c?.guides?.length > 0 && item(BookOpen, `${c.guides.length} Guide${c.guides.length > 1 ? 's' : ''}`, c.guides.join(', '))}
			{c?.probes_run > 0 && item(SearchCheck, `${c.probes_run} follow-up quer${c.probes_run > 1 ? 'ies' : 'y'}`, `${c.probes_shown} shown on the page`)}
			{k?.checked > 0 && (
				<span className="inline-flex items-center gap-1" title={k.ungrounded?.length ? `Not found in the data: ${k.ungrounded.join(', ')}` : 'Every number in the text matches the data'} style={{ color: k.grounded === k.checked ? t.tones.positive.fg : t.tones.warning.fg }}>
					<ShieldCheck size={13} /> {k.grounded}/{k.checked} numbers checked
				</span>
			)}
		</>
	);
}

// What the Dive has learned, read straight from the Flight's log tables.
function MemoryPage({ db }: any) {
	const t = useTheme();
	const T = (name: string) => `"${db}"."main"."${name}"`;
	const runs = useSQLQuery(`
		SELECT count(*) FILTER (WHERE kind = 'briefing') AS briefings, count(*) FILTER (WHERE kind <> 'briefing') AS pages,
		       sum(TRY_CAST(json_extract(checks, '$.grounded') AS INTEGER)) AS grounded, sum(TRY_CAST(json_extract(checks, '$.checked') AS INTEGER)) AS checked
		FROM ${T('gen_runs')} WHERE status = 'ready'`);
	const fb = useSQLQuery(`SELECT count(*) AS events FROM ${T('feedback')}`);
	const ins = useSQLQuery(`
		SELECT strftime(as_of, '%Y-%m-%d') AS as_of, title, body, tone, coalesce(continuity, 'new') AS continuity, times_seen
		FROM ${T('insights')} WHERE kind = 'briefing' ORDER BY as_of DESC, rank LIMIT 40`);
	const qs = useSQLQuery(`
		SELECT prompt, count(*) AS asked, strftime(max(created_at), '%Y-%m-%d') AS last_asked
		FROM ${T('gen_runs')} WHERE kind = 'question' AND status = 'ready' GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 8`);
	const prefs = useSQLQuery(`
		SELECT event, target, value, count(*) AS n FROM ${T('feedback')}
		WHERE event IN ('variant', 'hide', 'rate_insight', 'rate_page', 'investigate') GROUP BY ALL ORDER BY n DESC LIMIT 10`);
	const versions = useSQLQuery(`
		SELECT r.prompt_version, r.model, count(DISTINCT r.run_id) AS runs, round(avg(r.duration_s), 1) AS seconds,
		       sum(TRY_CAST(json_extract(r.checks, '$.grounded') AS INTEGER)) / nullif(sum(TRY_CAST(json_extract(r.checks, '$.checked') AS INTEGER)), 0) AS grounded,
		       count(f.event) FILTER (WHERE f.value = 'up') AS thumbs_up, count(f.event) FILTER (WHERE f.value = 'down') AS thumbs_down,
		       any_value(e.plan_score) AS eval_plan, any_value(e.eval_grounded) AS eval_grounded
		FROM ${T('gen_runs')} r LEFT JOIN ${T('feedback')} f ON f.spec_id = r.run_id AND f.event IN ('rate_page', 'rate_insight')
		LEFT JOIN (SELECT prompt_version, model, avg(plan_score) AS plan_score, avg(grounded) AS eval_grounded FROM ${T('evals')} GROUP BY ALL) e
		  ON e.prompt_version = r.prompt_version AND e.model = r.model
		WHERE r.status = 'ready' GROUP BY 1, 2 ORDER BY max(r.created_at) DESC LIMIT 6`);
	const rows = (q: any) => (Array.isArray(q.data) ? q.data : []);
	const r0 = rows(runs)[0] || {};
	const kpi = {
		id: 'mem-kpi', type: 'kpi', title: 'Memory',
		items: [
			{ key: 'briefings', label: 'Briefings remembered', value: N(r0.briefings), format: 'count' },
			{ key: 'pages', label: 'Questions and investigations', value: N(r0.pages), format: 'count' },
			{ key: 'feedback', label: 'Viewer signals', value: N(rows(fb)[0]?.events), format: 'count' },
			{ key: 'grounded', label: 'Numbers checked against data', value: N(r0.checked) ? N(r0.grounded)! / N(r0.checked)! : null, format: 'pct' },
		],
	};
	const label: Record<string, string> = { new: 'New', ongoing: 'Ongoing', worsening: 'Worsening', improving: 'Improving', resolved: 'Resolved' };
	const timeline = {
		id: 'mem-timeline', type: 'timeline', title: 'Insights across briefings',
		items: rows(ins).slice().reverse().map((r: any) => ({
			date: String(r.as_of),
			title: `${label[String(r.continuity)] || 'New'} · ${r.title}`,
			body: String(r.body || ''),
			tone: r.continuity === 'worsening' ? 'critical' : r.continuity === 'resolved' || r.continuity === 'improving' ? 'positive' : undefined,
		})),
	};
	const table = (id: string, title: string, label: string, q: any, cols: [string, string, string][]) => ({
		id, type: 'table', title, x: { key: 'x', kind: 'category', label },
		series: cols.map(([key, l, format]) => ({ key, label: l, format })),
		data: rows(q).map((r: any) => ({ x: String(r[cols.length ? Object.keys(r)[0] : 'x']), ...Object.fromEntries(cols.map(([k]) => [k, typeof r[k] === 'bigint' || typeof r[k] === 'number' ? N(r[k]) : String(r[k] ?? '')])) })),
	});
	const blocks = [
		table('mem-questions', 'What viewers ask', 'question', qs, [['asked', 'Times asked', 'count'], ['last_asked', 'Last asked', 'text']]),
		{ ...table('mem-prefs', 'What viewers did', 'event', prefs, [['target', 'On', 'text'], ['value', 'Value', 'text'], ['n', 'Times', 'count']]) },
		table('mem-versions', 'Prompt versions', 'prompt version', versions, [['model', 'Model', 'text'], ['runs', 'Runs', 'count'], ['seconds', 'Seconds', 'number'], ['grounded', 'Numbers grounded', 'pct'], ['eval_plan', 'Eval: plan', 'pct'], ['eval_grounded', 'Eval: grounded', 'pct'], ['thumbs_up', 'Useful', 'count'], ['thumbs_down', 'Not useful', 'count']]),
	];
	const loading = runs.isLoading || ins.isLoading;
	return (
		<div className="space-y-10">
			<header className="space-y-2">
				<div className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: t.text }}>
					<Brain size={13} /> Memory
				</div>
				<h1 className={`${t.flat ? 'text-4xl font-bold' : 'text-2xl font-semibold'} leading-tight`} style={{ fontFamily: t.heading, letterSpacing: t.flat ? -1.2 : -0.3 }}>
					What this Dive has learned
				</h1>
				<p className="text-sm" style={{ color: t.muted, maxWidth: 760 }}>
					Every briefing, question, insight and viewer signal is logged. The next analysis reads it back: insights continue or resolve, ratings steer what gets reported, and the chart types viewers prefer become the defaults.
				</p>
			</header>
			{runs.isError || ins.isError ? (
				<Callout tone="info" title="No memory yet">
					The log tables appear after the first run of the updated Flight.
				</Callout>
			) : loading ? (
				<Skeleton h={200} />
			) : (
				<>
					<BlockView block={kpi} />
					<Card title={timeline.title} icon={componentIcon('timeline')}>
						<BlockView block={timeline} />
					</Card>
					<div className="grid gap-10 md:grid-cols-2">
						{blocks.slice(0, 2).map((b) => (
							<Card key={b.id} title={b.title} icon={componentIcon('table')}>
								{b.data.length ? (
									<BlockView block={b} />
								) : (
									<p className="text-sm" style={{ color: t.muted }}>
										{b.id === 'mem-questions' ? 'No questions yet. They appear here once viewers use the ask bar.' : 'No viewer signals yet. Ratings, chart switches and hidden blocks appear here.'}
									</p>
								)}
							</Card>
						))}
					</div>
					<Card title={blocks[2].title} icon={componentIcon('table')} description="Compare prompt versions and models on speed, grounded numbers and viewer ratings before changing the default.">
						<BlockView block={blocks[2]} />
					</Card>
				</>
			)}
		</div>
	);
}

function SpecPage({ spec, arrange, onDraft, nav }: any) {
	const t = useTheme();
	const ref = useRef<HTMLDivElement>(null);
	const width = useWidth(ref);
	const narrow = width < 760;
	const blocks: any[] = spec.blocks || [];
	const layout = useLayout(spec.id, blocks, spec.kind, nav?.learned || {}, (e: any) => nav?.signal?.({ ...e, s: spec.id }));
	const [flash, setFlash] = useState<string | null>(null);
	const [showSummary, setShowSummary] = useState(false);
	const [dragId, setDragId] = useState<string | null>(null);
	const [over, setOver] = useState<string | null>(null);
	const byId: Record<string, any> = Object.fromEntries(blocks.map((b) => [b.id, b]));
	const gap = t.flat ? 40 : 16;
	const jump = (id: string) => {
		const el = typeof document !== 'undefined' ? document.getElementById(`blk-${id}`) : null;
		if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
		setFlash(id);
		setTimeout(() => setFlash(null), 1800);
	};
	const dropProps = (key: string, onDrop: (id: string) => void) => ({
		onDragOver: (e: any) => {
			e.preventDefault();
			if (over !== key) setOver(key);
		},
		onDragLeave: () => setOver((o) => (o === key ? null : o)),
		onDrop: (e: any) => {
			e.preventDefault();
			const id = dragId || e.dataTransfer.getData('text/plain');
			setOver(null);
			setDragId(null);
			if (id) onDrop(id);
		},
	});
	const dragProps = (id: string) => ({
		draggable: true,
		onDragStart: (e: any) => {
			e.dataTransfer.setData('text/plain', id);
			e.dataTransfer.effectAllowed = 'move';
			setDragId(id);
		},
		onDragEnd: () => {
			setDragId(null);
			setOver(null);
		},
	});
	const gapZone = (at: number) => {
		const key = `gap-${at}`;
		const active = over === key;
		return (
			<div
				key={key}
				{...dropProps(key, (id) => layout.newRow(id, at))}
				className="flex items-center justify-center text-xs"
				style={{
					height: dragId ? (active ? 56 : 22) : 8,
					transition: 'height 120ms',
					border: dragId ? `1px dashed ${active ? t.accent : t.muted}` : '1px dashed transparent',
					color: active ? t.accent : t.muted,
					background: active ? (t.name === 'Night' ? '#1c222b' : '#fafafa') : 'transparent',
				}}
			>
				{dragId ? 'Drop here for a new row' : ''}
			</div>
		);
	};

	return (
		<div ref={ref}>
			<header className="mb-8 space-y-3">
				{spec.parent && (
					<nav className="flex flex-wrap items-center gap-1 text-sm" style={{ color: t.muted }}>
						<button onClick={() => nav?.open(spec.parent)} className="inline-flex items-center gap-1" style={{ cursor: 'pointer', color: t.text, borderBottom: `1px solid ${t.flat ? t.rule : t.line}` }}>
							<ArrowLeft size={13} /> {spec.parent_title || 'Back'}
						</button>
						{spec.insight?.title && (
							<>
								<ChevronRight size={13} />
								<span>{spec.insight.title}</span>
							</>
						)}
					</nav>
				)}
				<div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" style={{ color: t.muted }}>
					<span className="inline-flex items-center gap-1" style={{ color: t.text, fontWeight: 600 }}>
						{KIND_ICON(spec.kind)}
						{KIND_LABEL[spec.kind] || 'Page'}
					</span>
					{spec.kind === 'briefing' && nav?.briefAsOf ? (
						// Pick a date to generate the briefing as it would have read that day.
						<DatePicker mode="single" value={spec.as_of} max={nav.latestAsOf || spec.as_of} label="Data as of" disabled={nav.busy} onChange={(iso: string) => iso !== spec.as_of && nav.briefAsOf(iso)} />
					) : (
						<span className="inline-flex items-center gap-1">
							<CalendarDays size={13} /> Data as of {spec.as_of}
						</span>
					)}
					<ContextLine spec={spec} />
					{spec.generated_at && (
						<span className="inline-flex items-center gap-1">
							<Clock size={13} /> Generated {since(spec.generated_at.replace('T', ' ').slice(0, 19))}
						</span>
					)}
				</div>
				<h1 className={`${t.flat ? 'text-4xl font-bold' : 'text-2xl font-semibold'} leading-tight`} style={{ fontFamily: t.heading, letterSpacing: t.flat ? -1.2 : -0.3, maxWidth: 1000 }}>
					{spec.kind === 'briefing' ? spec.title : spec.prompt}
				</h1>
				{spec.headline && (
					<p className="text-lg leading-snug" style={{ maxWidth: 900, color: t.text }}>
						{spec.headline}{' '}
						{spec.summary && (
							<button className="whitespace-nowrap text-sm" style={{ color: t.muted, borderBottom: `1px solid ${t.muted}`, cursor: 'pointer' }} onClick={() => setShowSummary(!showSummary)}>
								{showSummary ? 'Less' : 'Summary'}
							</button>
						)}
					</p>
				)}
				{showSummary && (
					<p className="text-sm leading-relaxed" style={{ maxWidth: 900, color: t.muted }}>
						{spec.summary}
					</p>
				)}
			</header>

			{arrange && (
				<div className="mb-4 flex flex-wrap items-center gap-2 text-xs" style={{ color: t.muted }}>
					<span>Drag components between rows. Pick a row layout on the left of each row. Your arrangement is saved with the link.</span>
					{layout.hidden.map((id: string) => (
						<Button key={id} size="sm" variant="soft" onClick={() => layout.show(id)}>
							<Eye size={12} /> {byId[id]?.title || id}
						</Button>
					))}
					{layout.dirty && (
						<Button size="sm" variant="outline" onClick={layout.reset}>
							<RotateCcw size={12} /> Reset layout
						</Button>
					)}
				</div>
			)}

			<div>
				{layout.rows.map((row: Row, r: number) => {
					const cols = TEMPLATES[row.t].cols;
					return (
						<div key={r}>
							{arrange && gapZone(r)}
							{arrange ? (
								<div className="flex gap-3" style={{ padding: '10px 0' }}>
									<div className="flex shrink-0 flex-col items-center gap-1 pt-1" style={{ width: 44 }}>
										{TEMPLATE_KEYS.map((k) => (
											<button key={k} title={TEMPLATES[k].label} onClick={() => layout.setTemplate(r, k)} style={{ cursor: 'pointer', padding: 2 }}>
												<TemplateIcon cols={TEMPLATES[k].cols} active={k === row.t} />
											</button>
										))}
										<div className="mt-1 flex gap-0.5">
											<Button variant="ghost" size="icon" title="Move row up" disabled={r === 0} onClick={() => layout.moveRow(r, -1)}>
												<ArrowUp size={12} />
											</Button>
											<Button variant="ghost" size="icon" title="Move row down" disabled={r === layout.rows.length - 1} onClick={() => layout.moveRow(r, 1)}>
												<ArrowDown size={12} />
											</Button>
										</div>
									</div>
									<div className="grid min-w-0 flex-1" style={{ gridTemplateColumns: 'repeat(12, minmax(0, 1fr))', gap: 16 }}>
										{row.slots.map((id, s) => {
											const key = `slot-${r}-${s}`;
											const active = over === key;
											const b = id ? byId[id] : null;
											return (
												<div
													key={key}
													{...dropProps(key, (dropped) => layout.moveTo(dropped, r, s))}
													style={{
														gridColumn: `span ${cols[s]} / span ${cols[s]}`,
														minWidth: 0,
														minHeight: 120,
														outline: active ? `2px solid ${t.accent}` : b ? 'none' : `1px dashed ${t.muted}`,
														outlineOffset: 2,
														opacity: dragId === id ? 0.35 : 1,
													}}
												>
													{b ? (
														<div {...dragProps(b.id)} style={{ cursor: 'grab', height: '100%', position: 'relative' }}>
															<span style={{ position: 'absolute', left: -14, top: 14, color: t.muted }}>
																<GripVertical size={14} />
															</span>
															<BlockCard b={b} layout={layout} arrange flash={flash} onDraft={onDraft} jump={jump} nav={nav} />
														</div>
													) : (
														<div className="flex h-full items-center justify-center text-xs" style={{ color: active ? t.accent : t.muted, minHeight: 120 }}>
															Drop a component here
														</div>
													)}
												</div>
											);
										})}
									</div>
								</div>
							) : (
								<div className="grid" style={{ gridTemplateColumns: 'repeat(12, minmax(0, 1fr))', columnGap: gap, marginBottom: t.flat ? 36 : 16 }}>
									{viewSpans(row).map(({ id, span }) => (
										<div key={id} id={`blk-${id}`} style={{ gridColumn: `span ${narrow ? 12 : span} / span ${narrow ? 12 : span}`, minWidth: 0, marginBottom: narrow ? 24 : 0 }}>
											<BlockCard b={byId[id]} layout={layout} arrange={false} flash={flash} onDraft={onDraft} jump={jump} nav={nav} />
										</div>
									))}
								</div>
							)}
						</div>
					);
				})}
				{arrange && gapZone(layout.rows.length)}
			</div>

			<div className="mt-6 flex flex-wrap items-center gap-4 text-xs" style={{ color: t.muted }}>
				<RateButtons nav={nav} kind="page" target="page" label="Was this page useful?" />
				{(spec.problems || []).length > 0 && <span>Skipped while generating: {spec.problems.join('; ')}</span>}
			</div>
		</div>
	);
}

export default function GenerativeBriefing() {
	// APP.theme is a theme key, or a brand: { base, name, accent, font, radius, ... }.
	const brand = APP.theme && typeof APP.theme === 'object' ? makeTheme(APP.theme) : null;
	const [themeKey, setThemeKey] = useDiveState<string>('theme', brand ? 'brand' : APP.theme || 'swiss');
	const theme = themeKey === 'brand' && brand ? brand : THEMES[themeKey] || THEMES.swiss;
	const themeOptions = [...(brand ? [['brand', brand]] : []), ...Object.entries(THEMES)];
	const [specId, setSpecId] = useDiveState<string | null>('spec', null);
	const [arrange, setArrange] = useState(false);
	const [view, setView] = useDiveState<string>('view', 'page');
	const [draft, setDraft] = useState('');
	// Viewer signals queue here until the next Flight run carries them (a Dive can't write).
	const [signals, setSignals] = useState<any[]>([]);
	const [ratings, setRatings] = useState<Record<string, string>>({});
	const feedbackRun = useRunFlight(FLIGHT_ID);
	const takeSignals = (extra: any[] = []) => {
		const all = [...signals, ...extra].slice(-50);
		setSignals([]);
		return all.length ? JSON.stringify(all).slice(0, 6000) : '';
	};
	const sendFeedback = (extra: any[]) => {
		try {
			feedbackRun.run({ MODE: 'feedback', FEEDBACK: takeSignals(extra), PROMPT: '', REQUEST_ID: newRequestId(), PARENT: '', AS_OF: '' });
		} catch {
			/* viewers without run access still see the page */
		}
	};
	const [waitingFor, setWaitingFor] = useState<string | null>(null);

	const list = useSQLQuery(`
		SELECT id, request_id, kind, prompt, title, status, coalesce(error, '') AS error,
		       strftime(created_at, '%Y-%m-%d %H:%M:%S') AS created,
		       strftime(as_of, '%Y-%m-%d') AS as_of,
		       coalesce(json_extract_string(spec, '$.parent'), '') AS parent
		FROM ${SPECS}
		ORDER BY created_at DESC
		LIMIT 40
	`);
	const rows = (Array.isArray(list.data) ? list.data : []).map((r: any) => ({
		id: String(r.id),
		request_id: String(r.request_id),
		kind: String(r.kind),
		prompt: String(r.prompt),
		title: String(r.title),
		status: String(r.status),
		error: String(r.error),
		created: String(r.created),
		as_of: String(r.as_of || ''),
		parent: String(r.parent || ''),
	}));
	const latestBriefing = rows.find((r) => r.kind === 'briefing' && r.status === 'ready');
	const current = rows.find((r) => r.id === specId) || latestBriefing || rows[0];
	const currentId = current && UUID.test(current.id) ? current.id : null;

	const specQ = useSQLQuery(`SELECT spec FROM ${SPECS} WHERE id = '${currentId}'`, { enabled: currentId != null });
	const spec = useMemo(() => {
		const raw = Array.isArray(specQ.data) && specQ.data[0] ? specQ.data[0].spec : null;
		try {
			return raw ? JSON.parse(String(raw)) : null;
		} catch {
			return null;
		}
	}, [specQ.data]);

	// Chart types viewers chose at least 3 times for a block type become its default.
	const learnedQ = useSQLQuery(`SELECT target, value, count(*) AS n FROM "${APP.db}"."main"."feedback" WHERE event = 'variant' GROUP BY ALL HAVING count(*) >= 3 ORDER BY n DESC`);
	const learned = useMemo(() => {
		const out: Record<string, string> = {};
		(Array.isArray(learnedQ.data) ? learnedQ.data : []).forEach((r: any) => {
			if (!out[String(r.target)]) out[String(r.target)] = String(r.value);
		});
		return out;
	}, [learnedQ.data]);

	const gen = useGenerator((requestId) => {
		setWaitingFor(requestId);
		list.refetch();
	});

	// Once the new spec row shows up, open it.
	useEffect(() => {
		if (!waitingFor) return;
		const hit = rows.find((r) => r.request_id === waitingFor);
		if (hit) {
			setSpecId(hit.id);
			setWaitingFor(null);
			gen.clear();
			setDraft('');
			if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
		}
	}, [waitingFor, list.data]);

	const ask = (q: string) => {
		const text = q.trim();
		if (!text || gen.busy) return;
		setDraft(text);
		gen.generate('ask', text, '', '', takeSignals());
	};

	// What AI chat sees: the open page's question, headline and components. Chat
	// receives the Dive's state with every message, so this grounds its answers in
	// the generated page. Kept small: state over ~1.5 KB moves server-side.
	const [, setContext] = useDiveState<any>('context', null);
	useEffect(() => {
		if (!spec) return;
		const clip = (s: any, n: number) => String(s || '').slice(0, n);
		setContext({
			page: KIND_LABEL[spec.kind] || 'Page',
			question: clip(spec.kind === 'briefing' ? spec.title : spec.prompt, 200),
			parent: spec.parent_title ? clip(spec.parent_title, 120) : undefined,
			as_of: spec.as_of,
			headline: clip(spec.headline, 300),
			insights: (spec.blocks || []).filter((b: any) => b.type === 'callouts').flatMap((b: any) => b.items || []).slice(0, 4).map((c: any) => clip(c.title, 80)),
			components: (spec.blocks || []).filter((b: any) => b.type !== 'callouts').slice(0, 8).map((b: any) => clip(b.title, 70)),
		});
	}, [spec?.id]);

	const nav = {
		busy: gen.busy,
		latestAsOf: rows.filter((r) => r.kind === 'briefing' && r.status === 'ready').map((r) => r.as_of).sort().slice(-1)[0],
		briefAsOf: (iso: string) => {
			const have = rows.find((r) => r.kind === 'briefing' && r.status === 'ready' && r.as_of === iso);
			if (have) return setSpecId(have.id);
			if (!gen.busy) gen.generate('briefing', '', '', iso, takeSignals());
		},
		open: (id: string) => {
			setSpecId(id);
			if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
		},
		existing: (q: string) => rows.find((r) => r.parent === currentId && r.prompt === q && r.status === 'ready')?.id || null,
		running: (q: string) => gen.busy && gen.pending?.mode === 'investigate' && gen.pending?.prompt === q,
		investigate: (item: any) => {
			if (gen.busy || !currentId) return;
			gen.generate('investigate', String(item.question).slice(0, 600), currentId, '', takeSignals([{ e: 'investigate', s: currentId, t: String(item.title || '').slice(0, 120) }]));
		},
		learned,
		signal: (e: any) => setSignals((prev) => [...prev, e].slice(-50)),
		rating: (kind: string, target: string) => ratings[`${currentId}|${kind}|${target}`] || '',
		rate: (kind: string, target: string, value: string) => {
			setRatings({ ...ratings, [`${currentId}|${kind}|${target}`]: value });
			if (value) sendFeedback([{ e: kind === 'page' ? 'rate_page' : 'rate_insight', s: currentId, t: String(target).slice(0, 120), v: value }]);
		},
	};

	const t = theme;
	const suggestions = spec?.follow_ups?.length ? [...spec.follow_ups, ...EXAMPLES.slice(0, 2)] : EXAMPLES;

	return (
		<ThemeCtx.Provider value={theme}>
			<div style={{ background: t.bg, color: t.text, fontFamily: t.font, minHeight: '100vh' }}>
				<div className="mx-auto px-4 pt-4 md:px-8 md:pt-6" style={{ maxWidth: 1360, paddingBottom: 140 }}>
					{/* Top bar */}
					<div className="mb-8 flex flex-wrap items-center gap-3">
						<div className="flex items-center gap-2">
							<span style={{ width: t.flat ? 12 : 22, height: t.flat ? 12 : 22, background: t.name === 'Duck' ? '#ffde00' : t.accent, borderRadius: t.flat ? 0 : t.name === 'Duck' ? 2 : 6, border: t.name === 'Duck' ? t.border : 'none', display: 'inline-block' }} />
							<span className="text-sm font-bold" style={{ fontFamily: t.heading }}>
								{APP.brand}
							</span>
							<span className="text-sm" style={{ color: t.muted }}>
								{APP.tagline}
							</span>
						</div>
						<span className="flex-1" />
						{rows.length > 0 && <PagesMenu rows={rows} selected={currentId} onSelect={(id: string) => { setView('page'); setSpecId(id); }} />}
						<Segmented options={themeOptions.map(([k, v]: any) => ({ value: k, label: v.name, icon: !t.flat && k === themeKey ? <Palette size={11} /> : null }))} value={themeKey} onChange={setThemeKey} />
						<Button variant={arrange ? 'primary' : 'outline'} size="sm" onClick={() => setArrange(!arrange)}>
							<LayoutGrid size={13} /> {arrange ? 'Done' : 'Arrange'}
						</Button>
						<Button variant={view === 'memory' ? 'primary' : 'ghost'} size="sm" onClick={() => setView(view === 'memory' ? 'page' : 'memory')} title="What this Dive has learned">
							<Brain size={13} /> Memory
						</Button>
						<Button variant="ghost" size="sm" disabled={gen.busy} onClick={() => gen.generate('briefing', '', '', '', takeSignals())} title="Run the daily briefing Flight now">
							<RefreshCw size={13} /> New briefing
						</Button>
					</div>

					{view === 'memory' ? (
						<MemoryPage db={APP.db} />
					) : list.isError ? (
						<Callout tone="critical" title="Couldn't load briefings">
							{String(list.error)}
						</Callout>
					) : current?.status === 'failed' ? (
						<Callout tone="critical" title="This generation failed">
							{current.error || 'No error message was recorded.'} Ask again, or rephrase the question.
						</Callout>
					) : list.isLoading || specQ.isLoading ? (
						<div className="space-y-6">
							<Skeleton h={14} w="20%" />
							<Skeleton h={40} w="55%" />
							<Skeleton h={22} w="70%" />
							<div className="grid grid-cols-4 gap-8">
								<Skeleton h={90} />
								<Skeleton h={90} />
								<Skeleton h={90} />
								<Skeleton h={90} />
							</div>
							<div className="grid grid-cols-3 gap-8">
								<Skeleton h={260} className="col-span-2" />
								<Skeleton h={260} />
							</div>
						</div>
					) : specQ.isError ? (
						<Callout tone="critical" title="Couldn't load this page">
							{String(specQ.error)}
						</Callout>
					) : spec ? (
						<SpecPage key={spec.id} spec={spec} arrange={arrange} onDraft={(q: string) => setDraft(q)} nav={nav} />
					) : (
						<p className="text-sm" style={{ color: t.muted }}>
							No briefings yet. Click “New briefing”.
						</p>
					)}
				</div>
				<AskBar gen={gen} draft={draft} setDraft={setDraft} ask={ask} suggestions={suggestions} />
			</div>
		</ThemeCtx.Provider>
	);
}
