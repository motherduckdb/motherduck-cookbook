import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useDiveState, useSQLQuery } from '@motherduck/react-sql-query';
import { ChevronRight, Monitor, Moon, Star, Sun } from 'lucide-react';

// `record_results.py setup` replaces this entry with the results share; keep it on one line.
export const REQUIRED_DATABASES = [
	{ type: 'database', path: 'md:optimization_results', alias: 'optimization_results' },
];

const DB = '"optimization_results"."main"';

// ── theme ────────────────────────────────────────────────────────────────────

type ThemeMode = 'auto' | 'light' | 'dark';
type Theme = {
	dark: boolean;
	bg: string;
	surface: string;
	text: string;
	muted: string;
	faint: string;
	border: string;
	grid: string;
	ink: string; // card borders and pill outlines
	shadow: string; // solid offset shadow
	primary: string;
	positive: string;
	negative: string;
	neutral: string;
	accent: string; // sun
	onAccent: string; // text on sun
	duck: string;
	track: string;
};

// MotherDuck brand colors (sand, ink, sky, sun, garden, watermelon, duck). The dark theme is
// derived from ink and sand; the brand system defines no dark palette of its own.
const LIGHT: Theme = {
	dark: false,
	bg: '#F4EFEA',
	surface: '#FFFFFF',
	text: '#383838',
	muted: '#6B655F',
	faint: '#8F877F',
	border: '#D6CDBE',
	grid: '#E6DED4',
	ink: '#383838',
	shadow: '#383838',
	primary: '#2BA5FF',
	positive: '#068475',
	negative: '#E23F35',
	neutral: '#BDB3A8',
	accent: '#FFDE00',
	onAccent: '#383838',
	duck: '#FF9538',
	track: '#ECE6DF',
};

const DARK: Theme = {
	dark: true,
	bg: '#232120',
	surface: '#2D2A28',
	text: '#F4EFEA',
	muted: '#B4ACA3',
	faint: '#8A827A',
	border: '#47423D',
	grid: '#36322F',
	ink: '#F4EFEA',
	shadow: '#0E0D0C',
	primary: '#6FC2FF',
	positive: '#50C7B7',
	negative: '#FF9A94',
	neutral: '#6F6862',
	accent: '#FFDE00',
	onAccent: '#383838',
	duck: '#FF9538',
	track: '#36322F',
};

// The host may mark its theme on the document (class, data attribute or color-scheme);
// otherwise fall back to the operating system preference.
function hostPrefersDark(): boolean {
	try {
		for (const el of [document.documentElement, document.body]) {
			if (!el) continue;
			const cls = el.classList;
			if (cls.contains('dark')) return true;
			if (cls.contains('light')) return false;
			const attr = (el.getAttribute('data-theme') || el.getAttribute('data-color-scheme') || '').toLowerCase();
			if (attr.includes('dark')) return true;
			if (attr.includes('light')) return false;
			const scheme = (el.style && el.style.colorScheme) || '';
			if (scheme === 'dark') return true;
			if (scheme === 'light') return false;
		}
		return window.matchMedia('(prefers-color-scheme: dark)').matches;
	} catch {
		return false;
	}
}

function useHostDark(): boolean {
	const [dark, setDark] = useState<boolean>(hostPrefersDark);
	useEffect(() => {
		const update = () => setDark(hostPrefersDark());
		let media: MediaQueryList | null = null;
		try {
			media = window.matchMedia('(prefers-color-scheme: dark)');
			media.addEventListener('change', update);
		} catch {
			media = null;
		}
		const observer = new MutationObserver(update);
		for (const el of [document.documentElement, document.body]) {
			if (el) observer.observe(el, { attributes: true, attributeFilter: ['class', 'style', 'data-theme', 'data-color-scheme'] });
		}
		return () => {
			observer.disconnect();
			if (media) media.removeEventListener('change', update);
		};
	}, []);
	return dark;
}

// ── value helpers ────────────────────────────────────────────────────────────

const N = (v: unknown): number => (v == null ? 0 : Number(v));
const NN = (v: unknown): number | null => (v == null ? null : Number(v));
const S = (v: unknown): string => (v == null ? '' : String(v));
const q1 = (v: string) => v.replace(/'/g, "''");
const rowsOf = (q: { data?: unknown }) => (Array.isArray(q.data) ? (q.data as Record<string, unknown>[]) : []);
const MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
const HEAD = "'Aeonik Mono', 'Cousine', ui-monospace, Menlo, monospace"; // brand headline face, uppercase
const SIZE_ORDER: Record<string, number> = { pulse: 0, standard: 1, jumbo: 2, mega: 3, giga: 4 };
const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

function dur(seconds: number | null): string {
	if (seconds == null || Number.isNaN(seconds)) return '–';
	const s = Math.abs(seconds);
	if (s < 10) return `${seconds.toFixed(1)} s`;
	if (s < 120) return `${Math.round(seconds)} s`;
	if (s < 3600) return `${(seconds / 60).toFixed(1)} min`;
	return `${(seconds / 3600).toFixed(1)} h`;
}
function secs(seconds: number | null): string {
	if (seconds == null) return '–';
	return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds).toLocaleString()} s`;
}
function pct(v: number | null, digits = 1): string {
	if (v == null || Number.isNaN(v)) return '–';
	return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}%`;
}
function bytes(v: number | null): string {
	if (v == null) return '–';
	if (v === 0) return '0';
	const gb = v / 1e9;
	return gb >= 1000 ? `${(gb / 1000).toFixed(1)} TB` : gb >= 1 ? `${gb.toFixed(1)} GB` : `${(v / 1e6).toFixed(0)} MB`;
}
function sizeLabel(size: string, threads: number | null): string {
	const t = threads ? ` · ${threads} thread${threads === 1 ? '' : 's'}` : '';
	return `${cap(size || '?')}${t}`;
}

// ── small UI pieces ──────────────────────────────────────────────────────────

function Skeleton({ t, h = 16, w = '100%' }: { t: Theme; h?: number; w?: number | string }) {
	return <div className="animate-pulse rounded" style={{ height: h, width: w, background: t.track }} />;
}

function ErrorLine({ t, error }: { t: Theme; error: unknown }) {
	return (
		<p role="alert" className="text-sm" style={{ color: t.negative }}>
			{String((error as Error)?.message || error)}
		</p>
	);
}

function Label({ t, children }: { t: Theme; children: ReactNode }) {
	return (
		<p className="text-xs uppercase" style={{ color: t.muted, fontFamily: HEAD, letterSpacing: '0.12em' }}>
			{children}
		</p>
	);
}

function SectionTitle({ t, title }: { t: Theme; title: string; note?: string }) {
	return (
		<h2 className="mb-3 text-sm uppercase" style={{ color: t.text, fontFamily: HEAD, letterSpacing: '0.08em', fontWeight: 700 }}>
			{title}
		</h2>
	);
}

const DECISION_COLOR = (t: Theme, d: string) =>
	d === 'accepted' ? t.positive : d === 'rejected' ? t.negative : d === 'pending' ? t.primary : t.faint;

function Decision({ t, decision }: { t: Theme; decision: string }) {
	const c = DECISION_COLOR(t, decision);
	return (
		<span className="inline-flex items-center gap-1 text-xs whitespace-nowrap" style={{ color: t.muted }}>
			<span style={{ width: 7, height: 7, borderRadius: 7, background: c, display: 'inline-block' }} />
			{decision || 'pending'}
		</span>
	);
}

const effectColor = (t: Theme, e: string) =>
	e === 'improved' ? t.positive : e === 'regressed' ? t.negative : t.neutral;

function ThemeToggle({ t, mode, setMode }: { t: Theme; mode: ThemeMode; setMode: (m: ThemeMode) => void }) {
	const options: { m: ThemeMode; icon: ReactNode; label: string }[] = [
		{ m: 'auto', icon: <Monitor size={13} />, label: 'Match MotherDuck' },
		{ m: 'light', icon: <Sun size={13} />, label: 'Light' },
		{ m: 'dark', icon: <Moon size={13} />, label: 'Dark' },
	];
	return (
		<div className="inline-flex" style={{ border: `2px solid ${t.ink}`, borderRadius: 999, overflow: 'hidden' }} role="group" aria-label="Theme">
			{options.map((o) => (
				<button
					key={o.m}
					type="button"
					title={o.label}
					aria-pressed={mode === o.m}
					onClick={() => setMode(o.m)}
					className="cursor-pointer"
					style={{
						color: mode === o.m ? t.onAccent : t.muted,
						background: mode === o.m ? t.accent : 'transparent',
						lineHeight: 0,
						padding: '6px 10px',
					}}
				>
					{o.icon}
				</button>
			))}
		</div>
	);
}

// ── main ─────────────────────────────────────────────────────────────────────

type Tab = 'overview' | 'experiments' | 'pipeline' | 'agent';

export default function OptimizationResults() {
	const [mode, setMode] = useDiveState<ThemeMode>('theme', 'auto');
	const hostDark = useHostDark();
	const t = mode === 'dark' || (mode === 'auto' && hostDark) ? DARK : LIGHT;
	const [tab, setTab] = useDiveState<Tab>('tab', 'overview');
	const [campaignChoice, setCampaign] = useDiveState<string>('campaign', '');
	// Same keys as the Experiments tab, so the Overview can open one experiment there.
	const [, setOpenExperiment] = useDiveState<string>('experiment', '');
	const [, setDecisionFilter] = useDiveState<string>('decision', 'all');
	const openExperiment = (id: string) => {
		setOpenExperiment(id);
		setDecisionFilter('all');
		setTab('experiments');
	};

	const campaigns = useSQLQuery(`
		SELECT campaign_id, coalesce(title, campaign_id) AS title
		FROM ${DB}.v_campaign_summary
		ORDER BY started_at DESC NULLS LAST, campaign_id`);
	const campaignRows = rowsOf(campaigns);
	const campaignId =
		campaignRows.find((r) => S(r.campaign_id) === campaignChoice) != null
			? campaignChoice
			: S(campaignRows[0]?.campaign_id);
	const cid = q1(campaignId);
	const ready = campaignId !== '';

	const summary = useSQLQuery(
		`SELECT *, array_to_string(sizes, ', ') AS sizes_text,
		        strftime(started_at, '%b %d %H:%M') AS started_label,
		        strftime(finished_at, '%b %d %H:%M') AS finished_label,
		        epoch(finished_at - started_at) AS elapsed_s
		 FROM ${DB}.v_campaign_summary WHERE campaign_id = '${cid}'`,
		{ enabled: ready },
	);
	const s = rowsOf(summary)[0] || {};

	// Optional modules appear only when the campaign logged their data.
	const modules = useSQLQuery(
		`SELECT (SELECT count(*) FROM ${DB}.run_steps WHERE campaign_id = '${cid}') AS steps,
		        (SELECT count(*) FROM ${DB}.agent_events WHERE campaign_id = '${cid}') AS events`,
		{ enabled: ready },
	);
	const hasSteps = N(rowsOf(modules)[0]?.steps) > 0;
	const hasEvents = N(rowsOf(modules)[0]?.events) > 0;
	const tabs: { id: Tab; label: string }[] = [
		{ id: 'overview', label: 'Overview' },
		{ id: 'experiments', label: 'Experiments' },
		...(hasSteps ? [{ id: 'pipeline' as Tab, label: 'Pipeline' }] : []),
	];
	const view: Tab = tabs.some((x) => x.id === tab) || (tab === 'agent' && hasEvents) ? tab : 'overview';

	return (
		<div
			className="p-6 min-h-screen"
			style={{
				background: t.bg,
				color: t.text,
				colorScheme: t.dark ? 'dark' : 'light',
				fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
				fontVariantNumeric: 'tabular-nums',
			}}
		>
			<header className="flex items-start justify-between gap-4">
				<div style={{ minWidth: 0 }}>
					<div className="flex items-center gap-2">
						<DuckFeet size={22} />
						<Label t={t}>Optimization results</Label>
					</div>
					{summary.isLoading || campaigns.isLoading ? (
						<div className="mt-2 space-y-2">
							<Skeleton t={t} h={28} w={320} />
							<Skeleton t={t} h={14} w={480} />
						</div>
					) : campaigns.isError ? (
						<ErrorLine t={t} error={campaigns.error} />
					) : !ready ? (
						<p className="mt-2 text-sm" style={{ color: t.muted }}>
							No campaigns recorded yet. Log one with <code style={{ fontFamily: MONO }}>record_results.py log</code>.
						</p>
					) : (
						<>
							<h1 title={S(s.goal)} className="mt-2 uppercase" style={{ color: t.text, fontFamily: HEAD, fontSize: 26, lineHeight: '32px', letterSpacing: '0.02em' }}>
								{S(s.title) || campaignId}
							</h1>
							<p className="text-xs mt-2" style={{ color: t.faint, fontFamily: MONO }}>
								{[S(s.workload_kind), S(s.region), s.started_label && `${S(s.started_label)} → ${S(s.finished_label)} UTC`]
									.filter(Boolean)
									.join('  ·  ')}
							</p>
						</>
					)}
				</div>
				<div className="flex items-center gap-2" style={{ flexShrink: 0 }}>
					{campaignRows.length > 1 && (
						<select
							value={campaignId}
							onChange={(e) => setCampaign(e.target.value)}
							className="text-sm cursor-pointer"
							style={{ background: t.surface, color: t.text, border: `2px solid ${t.ink}`, borderRadius: 999, padding: '4px 12px', maxWidth: 260 }}
							aria-label="Campaign"
						>
							{campaignRows.map((r) => (
								<option key={S(r.campaign_id)} value={S(r.campaign_id)}>
									{S(r.title)}
								</option>
							))}
						</select>
					)}
					<ThemeToggle t={t} mode={mode} setMode={setMode} />
				</div>
			</header>

			<nav className="flex flex-wrap items-center gap-2 mt-6 mb-8">
				{tabs.map((x) => (
					<button
						key={x.id}
						type="button"
						onClick={() => setTab(x.id)}
						className="cursor-pointer uppercase"
						style={{
							fontFamily: HEAD,
							fontSize: 12,
							letterSpacing: '0.08em',
							padding: '7px 16px',
							borderRadius: 999,
							border: `2px solid ${view === x.id ? t.ink : t.border}`,
							background: view === x.id ? t.accent : 'transparent',
							color: view === x.id ? t.onAccent : t.muted,
							fontWeight: view === x.id ? 700 : 400,
						}}
					>
						{x.label}
					</button>
				))}
				{hasEvents && (
					<button
						type="button"
						onClick={() => setTab('agent')}
						className="cursor-pointer text-xs ml-auto"
						style={{
							color: view === 'agent' ? t.muted : t.faint,
							textDecoration: view === 'agent' ? 'underline' : 'none',
							textUnderlineOffset: 4,
							padding: '7px 4px',
						}}
					>
						Agent log
					</button>
				)}
			</nav>

			{ready && view === 'overview' && <Overview t={t} cid={cid} s={s} loading={summary.isLoading} onOpen={openExperiment} />}
			{ready && view === 'experiments' && <Experiments t={t} cid={cid} />}
			{ready && view === 'pipeline' && <PipelineGantt t={t} cid={cid} s={s} />}
			{ready && view === 'agent' && <AgentTimeline t={t} cid={cid} />}
		</div>
	);
}

// ── overview ─────────────────────────────────────────────────────────────────

function Kpi({ t, value, label, sub, color, loading, accent }: { t: Theme; value: string; label: string; sub?: string; color?: string; loading: boolean; accent?: boolean }) {
	const fg = accent ? t.onAccent : t.text;
	return (
		<div
			style={{
				minWidth: 0,
				background: accent ? t.accent : t.surface,
				border: `2px solid ${t.ink}`,
				borderRadius: 14,
				boxShadow: `-6px 6px 0 ${t.shadow}`,
				padding: '14px 18px',
			}}
		>
			<p className="text-xs uppercase truncate" style={{ color: fg, fontFamily: HEAD, letterSpacing: '0.12em', opacity: 0.85 }} title={label}>
				{label}
			</p>
			{loading ? (
				<div className="mt-2">
					<Skeleton t={t} h={36} w={110} />
				</div>
			) : (
				<p className="mt-1" style={{ fontFamily: HEAD, fontSize: 36, lineHeight: '44px', letterSpacing: '-0.01em', color: accent ? fg : color || fg }}>
					{value}
				</p>
			)}
			{sub && (
				<p className="text-xs mt-1" style={{ color: fg, opacity: accent ? 0.85 : 1, ...(accent ? {} : { color: t.muted }) }}>
					{sub}
				</p>
			)}
		</div>
	);
}

function DuckFeet({ size = 20 }: { size?: number }) {
	return (
		<svg width={size} height={(size * 124) / 170} viewBox="0 0 170 124" fill="none" aria-hidden="true">
			<path d="M4.3 86.3S52 120.4 54.9 122.1c2.8 1.7 6.9 2.1 10.5.7 3.6-1.4 6.3-4.6 7.1-7.8.9-3.2 11.8-60.8 11.8-60.8.1-.8.4-3.1-.8-5.3-1.7-3-5.5-4.6-8.9-3.6-2.7.8-4.1 2.8-4.5 3.5-.6 1.1-1.7 2.9-3.7 4.5-1.5 1.2-3 1.9-3.7 2.2-6.1 2.5-12.9.4-16.8-4.6-2.4-3.1-6.7-4.3-10.5-2.8-3.8 1.5-6 5.4-5.7 9.3.7 6.2-2.8 12.4-9 14.9-3.3 1.3-6.8 1.3-9.9.3-.7-.2-3.2-.8-5.7.6C2 74.8.4 78.6 1.2 82c.6 2.3 2.4 3.8 3.1 4.3Z" fill="#FF9538" />
			<path d="M83.8 11.6s17 56.2 18.2 59.2c1.2 3.1 4.2 5.9 7.9 7 3.7 1.1 7.8.2 10.4-1.8 2.6-2 46.5-40.9 46.5-40.9.6-.5 2.2-2.2 2.6-4.7.5-3.4-1.6-7-4.8-8.3-2.6-1-5-.3-5.7 0-1.1.5-3.1 1.2-5.6 1.3-2 .1-3.5-.3-4.3-.5-6.3-1.8-10.5-7.6-10.5-13.9-.1-3.9-2.7-7.5-6.6-8.6-4-1.1-8.1.6-10.2 3.9-3.3 5.4-9.8 8.1-16.2 6.3-3.4-1-6.2-3.1-8-5.8-.5-.6-2.1-2.6-4.9-3.1-3.4-.6-7.1 1.4-8.5 4.5-1.1 2.4-.5 4.6-.3 5.4Z" fill="#FF9538" />
		</svg>
	);
}

function Resources({ t, cid }: { t: Theme; cid: string }) {
	const q = useSQLQuery(`
		SELECT coalesce(service_account, '(not recorded)') AS account, count(*) AS runs,
		       array_to_string(list_sort(list_distinct(list(duckling_size) FILTER (WHERE duckling_size IS NOT NULL))), ', ') AS sizes,
		       sum(coalesce(wall_s, 0)) AS compute_s,
		       array_to_string(list_sort(list_distinct(flatten(list(databases) FILTER (WHERE databases IS NOT NULL)))), ', ') AS dbs
		FROM ${DB}.runs WHERE campaign_id = '${cid}'
		GROUP BY ALL ORDER BY runs DESC`);
	const rows = rowsOf(q);
	if (q.isLoading) return <Skeleton t={t} h={60} />;
	if (q.isError) return <ErrorLine t={t} error={q.error} />;
	if (!rows.some((r) => S(r.account) !== '(not recorded)' || S(r.dbs))) return null;
	return (
		<section>
			<SectionTitle t={t} title="Resources" />
			<table className="w-full text-sm">
				<thead>
					<tr style={{ color: t.muted }}>
						{['Service account', 'Runs', 'Sizes', 'Duckling time', 'Databases'].map((h, i) => (
							<th key={h} className={`font-normal pb-2 ${i && i < 4 ? 'text-right' : 'text-left'}`} style={{ borderBottom: `1px solid ${t.border}`, paddingLeft: i === 4 ? 16 : 0 }}>
								{h}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{rows.map((r) => (
						<tr key={S(r.account)} style={{ borderBottom: `1px solid ${t.grid}` }}>
							<td className="py-2" style={{ fontFamily: MONO, fontSize: 12 }}>
								{S(r.account)}
							</td>
							<td className="py-2 text-right">{N(r.runs)}</td>
							<td className="py-2 text-right">{S(r.sizes)}</td>
							<td className="py-2 text-right">{dur(N(r.compute_s))}</td>
							<td className="py-2" style={{ fontFamily: MONO, fontSize: 12, color: t.muted, paddingLeft: 16 }}>
								{S(r.dbs) || '–'}
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</section>
	);
}

// Best configurations (v_leaderboard): the fastest adoptable candidate per subject on any size,
// plus the fastest on every other Duckling size that was measured.
function useBest(cid: string) {
	const q = useSQLQuery(`
		SELECT l.subject, l.scope, l.duckling_size, l.threads, l.variant, l.candidate_s, l.original_s, l.vs_original_pct,
		       l.experiment_id, l.experiment_title, l.rank, l.size_rank, k.headline_size
		FROM ${DB}.v_leaderboard l JOIN ${DB}.campaigns k USING (campaign_id)
		WHERE l.campaign_id = '${cid}' AND (l.rank = 1 OR l.size_rank = 1)
		ORDER BY l.scope = 'pipeline' DESC, l.subject, l.rank`);
	const rows = rowsOf(q);
	// Stars mark the overall best and the best on the headline size.
	const starred = rows.filter((r) => N(r.rank) === 1 || S(r.duckling_size) === S(r.headline_size));
	const key = (r: Record<string, unknown>) =>
		[S(r.experiment_id), S(r.subject), S(r.duckling_size), N(r.threads), S(r.variant)].join('|');
	const keys = new Set(starred.map(key));
	const experiments = new Set(starred.map((r) => S(r.experiment_id)));
	return { q, rows, isBest: (r: Record<string, unknown>) => keys.has(key(r)), experiments };
}

function BestStar({ t, size = 13 }: { t: Theme; size?: number }) {
	return <Star size={size} fill={t.accent} stroke={t.ink} strokeWidth={1.5} aria-label="best" style={{ flexShrink: 0, display: 'inline-block', verticalAlign: '-2px' }} />;
}

function BestConfigs({ t, cid, onOpen }: { t: Theme; cid: string; onOpen: (id: string) => void }) {
	const { q, rows } = useBest(cid);
	if (q.isLoading) return <Skeleton t={t} h={60} />;
	if (q.isError) return <ErrorLine t={t} error={q.error} />;
	if (!rows.length) return null;
	const subjects = [...new Set(rows.map((r) => S(r.subject)))];
	const cell = 'py-2 text-sm';
	const Row = ({ r, alt, last }: { r: Record<string, unknown>; alt?: boolean; last: boolean }) => {
		const vs = NN(r.vs_original_pct);
		return (
			<tr
				onClick={() => onOpen(S(r.experiment_id))}
				className="cursor-pointer"
				title={`Open ${S(r.experiment_title)} in Experiments`}
				style={{ borderBottom: last ? `1px solid ${t.grid}` : undefined, color: alt ? t.muted : t.text }}
			>
				<td className={cell} style={{ paddingLeft: 2 }}>
					<span className="flex items-center gap-2" style={{ minWidth: 0 }}>
						<span style={{ width: 14, flexShrink: 0 }}>{!alt && <BestStar t={t} />}</span>
						<span className="truncate">{alt ? `↳ best on ${cap(S(r.duckling_size))}` : S(r.subject)}</span>
					</span>
				</td>
				<td className={`${cell} text-right whitespace-nowrap`} style={{ fontWeight: alt ? 400 : 600 }}>
					{secs(NN(r.candidate_s))}
				</td>
				<td className={`${cell} whitespace-nowrap`} style={{ paddingLeft: 16, color: t.muted }}>
					{sizeLabel(S(r.duckling_size), N(r.threads) || null)}
				</td>
				<td className={`${cell} text-right whitespace-nowrap`} style={{ color: vs != null && vs < 0 ? t.positive : t.muted }}>
					{vs == null ? '' : pct(vs, 0)}
				</td>
				<td className={cell} style={{ paddingLeft: 16, color: t.muted }}>
					<span className="flex items-center justify-between gap-2" style={{ minWidth: 0 }}>
						<span className="truncate">{S(r.experiment_title)}</span>
						<ChevronRight size={14} style={{ color: t.faint, flexShrink: 0 }} aria-hidden="true" />
					</span>
				</td>
			</tr>
		);
	};
	return (
		<section>
			<SectionTitle t={t} title="Best configuration" />
			<table className="w-full" style={{ tableLayout: 'fixed' }}>
				<colgroup>
					<col style={{ width: '32%' }} />
					<col style={{ width: '10%' }} />
					<col style={{ width: '16%' }} />
					<col style={{ width: '14%' }} />
					<col style={{ width: '28%' }} />
				</colgroup>
				<thead>
					<tr className="text-xs" style={{ color: t.muted }}>
						{['Subject', 'Best', 'Duckling', 'vs original, same size', 'Experiment'].map((h, i) => (
							<th
								key={h}
								className={`font-normal pb-1 ${i === 1 || i === 3 ? 'text-right' : 'text-left'}`}
								style={{ borderBottom: `1px solid ${t.border}`, paddingLeft: i === 0 ? 24 : i === 2 || i === 4 ? 16 : 0 }}
							>
								{h}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{subjects.map((sub) => {
						const best = rows.find((r) => S(r.subject) === sub && N(r.rank) === 1);
						const others = rows
							.filter((r) => S(r.subject) === sub && N(r.rank) !== 1 && S(r.duckling_size) !== S(best?.duckling_size))
							.sort((a, b) => (SIZE_ORDER[S(a.duckling_size)] ?? 9) - (SIZE_ORDER[S(b.duckling_size)] ?? 9));
						return (
							<Fragment key={sub}>
								{best && <Row r={best} alt={false} last={!others.length} />}
								{others.map((o, i) => (
									<Row key={S(o.duckling_size)} r={o} alt last={i === others.length - 1} />
								))}
							</Fragment>
						);
					})}
				</tbody>
			</table>
		</section>
	);
}

const SCOPES = [
	{ id: 'pipeline', label: 'Pipeline / job' },
	{ id: 'model', label: 'Isolated models' },
	{ id: 'query', label: 'Isolated queries' },
];
const EFFECT_COLS = 'minmax(0, 2fr) minmax(0, 1.3fr) 92px minmax(0, 1.6fr) 60px 14px';

function EffectRow({ t, r, maxAbs, onOpen }: { t: Theme; r: Record<string, unknown>; maxAbs: number; onOpen: (id: string) => void }) {
	const d = NN(r.delta_pct);
	const w = d == null ? 0 : (Math.min(Math.abs(d), maxAbs) / maxAbs) * 50;
	const c = effectColor(t, S(r.effect));
	return (
		<button
			type="button"
			onClick={() => onOpen(S(r.experiment_id))}
			aria-label={`Open ${S(r.title)} in Experiments`}
			className="w-full grid items-center gap-3 py-1 cursor-pointer text-left"
			style={{ gridTemplateColumns: EFFECT_COLS, borderBottom: `1px solid ${t.grid}` }}
			title={`${S(r.subject)} on ${sizeLabel(S(r.best_size), NN(r.best_threads))}: ${secs(NN(r.baseline_s))} (${S(r.baseline_source)}) → ${secs(NN(r.candidate_s))}`}
		>
			<span className="text-sm truncate" style={{ color: t.text }}>
				{S(r.title)}
			</span>
			<span className="text-xs truncate" style={{ color: t.muted, fontFamily: MONO }}>
				{S(r.subject)} · {cap(S(r.best_size))}
			</span>
			<Decision t={t} decision={S(r.decision)} />
			<span className="relative block" style={{ height: 14 }}>
				<span className="absolute" style={{ left: '50%', top: -3, bottom: -3, width: 1, background: t.border }} />
				<span className="absolute rounded-sm" style={{ top: 2, height: 10, width: `${Math.max(w, 0.6)}%`, left: d != null && d < 0 ? `${50 - w}%` : '50%', background: c }} />
				{d != null && Math.abs(d) > maxAbs && (
					<span className="absolute text-xs" style={{ right: 0, top: -2, color: t.muted }}>
						›
					</span>
				)}
			</span>
			<span className="text-sm text-right" style={{ color: c, fontFamily: MONO }}>
				{pct(d, 0)}
			</span>
			<ChevronRight size={14} style={{ color: t.faint }} aria-hidden="true" />
		</button>
	);
}

function Overview({ t, cid, s, loading, onOpen }: { t: Theme; cid: string; s: Record<string, unknown>; loading: boolean; onOpen: (id: string) => void }) {
	const headlinePct = NN(s.headline_pct);
	const exps = useSQLQuery(`
		WITH best AS (
		  SELECT * FROM ${DB}.v_comparisons WHERE campaign_id = '${cid}' AND effect <> 'no_baseline'
		  QUALIFY row_number() OVER (PARTITION BY experiment_id, scope ORDER BY delta_pct) = 1
		)
		SELECT e.experiment_id, e.title, e.decision, e.change_kind, b.scope, b.subject,
		       b.duckling_size AS best_size, b.threads AS best_threads, b.baseline_s, b.baseline_source,
		       b.candidate_s, b.delta_pct, b.effect
		FROM ${DB}.v_experiment_summary e LEFT JOIN best b USING (campaign_id, experiment_id)
		WHERE e.campaign_id = '${cid}'
		ORDER BY coalesce(e.first_run_at, e.created_at), e.experiment_id`);
	const sizes = useSQLQuery(`
		WITH r AS (
		  SELECT duckling_size, count(*) AS runs, count(*) FILTER (WHERE status <> 'succeeded') AS failed,
		         sum(coalesce(wall_s, 0)) AS compute_s
		  FROM ${DB}.runs WHERE campaign_id = '${cid}' AND duckling_size IS NOT NULL GROUP BY ALL
		), h AS (
		  SELECT c.duckling_size, min(c.original_s) AS original_s, min(c.candidate_s) AS best_s
		  FROM ${DB}.v_comparisons c JOIN ${DB}.campaigns k USING (campaign_id)
		  WHERE c.campaign_id = '${cid}' AND c.original_s IS NOT NULL
		    AND (k.headline_subject IS NULL OR c.subject = k.headline_subject)
		    AND (k.headline_threads IS NULL OR c.threads = k.headline_threads)
		  GROUP BY ALL
		)
		SELECT r.*, h.original_s, h.best_s FROM r LEFT JOIN h USING (duckling_size)`);

	const rows = rowsOf(exps);
	const maxAbs = Math.max(25, ...rows.map((r) => Math.min(100, Math.abs(N(r.delta_pct)))));
	const sizeRows = rowsOf(sizes).sort((a, b) => (SIZE_ORDER[S(a.duckling_size)] ?? 9) - (SIZE_ORDER[S(b.duckling_size)] ?? 9));

	return (
		<div className="space-y-10">
			<section className="grid grid-cols-4 gap-6 pl-2">
				<Kpi
					t={t}
					accent
					loading={loading}
					value={headlinePct == null ? '–' : pct(headlinePct)}
					label={S(s.headline_subject_measured) || S(s.headline_subject) || 'Best accepted result'}
					sub={
						headlinePct == null
							? undefined
							: `${secs(NN(s.headline_original_s))} → ${secs(NN(s.headline_best_s))} · ${sizeLabel(S(s.headline_size_measured), NN(s.headline_threads_measured))}`
					}
				/>
				<Kpi
					t={t}
					loading={loading}
					value={String(N(s.experiments))}
					label="Experiments"
					sub={`${N(s.accepted)} accepted · ${N(s.rejected)} rejected`}
				/>
				<Kpi
					t={t}
					loading={loading}
					value={String(N(s.runs))}
					label="Runs"
					sub={N(s.failed_runs) ? `${N(s.failed_runs)} failed` : undefined}
				/>
				<Kpi
					t={t}
					loading={loading}
					value={dur(NN(s.elapsed_s))}
					label="Wall-clock"
				/>
			</section>

			<BestConfigs t={t} cid={cid} onOpen={onOpen} />

			<section>
				<SectionTitle t={t} title="What each experiment did" />
				{exps.isLoading ? (
					<div className="space-y-2">
						{[0, 1, 2, 3, 4].map((i) => (
							<Skeleton key={i} t={t} h={20} />
						))}
					</div>
				) : exps.isError ? (
					<ErrorLine t={t} error={exps.error} />
				) : (
					<div className="space-y-6">
						{SCOPES.filter((sc) => rows.some((r) => S(r.scope) === sc.id)).map((sc) => (
							<div key={sc.id}>
								<Label t={t}>{sc.label}</Label>
								<div className="mt-1">
									{rows
										.filter((r) => S(r.scope) === sc.id)
										.map((r) => (
											<EffectRow key={S(r.experiment_id)} t={t} r={r} maxAbs={maxAbs} onOpen={onOpen} />
										))}
								</div>
							</div>
						))}
					</div>
				)}
			</section>

			<section>
				<SectionTitle t={t} title="By Duckling size" />
				{sizes.isLoading ? (
					<Skeleton t={t} h={80} />
				) : sizes.isError ? (
					<ErrorLine t={t} error={sizes.error} />
				) : (
					<table className="w-full text-sm">
						<thead>
							<tr style={{ color: t.muted }}>
								{['Size', 'Runs', 'Failed', 'Duckling time', 'Original', 'Best candidate', 'Change'].map((h, i) => (
									<th key={h} className={`font-normal pb-2 ${i ? 'text-right' : 'text-left'}`} style={{ borderBottom: `1px solid ${t.border}` }}>
										{h}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{sizeRows.map((r) => {
								const o = NN(r.original_s);
								const b = NN(r.best_s);
								const ch = o && b ? (100 * (b - o)) / o : null;
								return (
									<tr key={S(r.duckling_size)} style={{ borderBottom: `1px solid ${t.grid}` }}>
										<td className="py-2">{cap(S(r.duckling_size))}</td>
										<td className="py-2 text-right">{N(r.runs)}</td>
										<td className="py-2 text-right" style={{ color: N(r.failed) ? t.negative : t.muted }}>
											{N(r.failed)}
										</td>
										<td className="py-2 text-right">{dur(N(r.compute_s))}</td>
										<td className="py-2 text-right">{secs(o)}</td>
										<td className="py-2 text-right">{secs(b)}</td>
										<td className="py-2 text-right" style={{ color: ch == null ? t.faint : ch <= -5 ? t.positive : ch >= 5 ? t.negative : t.muted, fontFamily: MONO }}>
											{pct(ch, 0)}
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				)}
			</section>

			<Resources t={t} cid={cid} />
		</div>
	);
}

// ── experiments ──────────────────────────────────────────────────────────────

function Experiments({ t, cid }: { t: Theme; cid: string }) {
	const [open, setOpen] = useDiveState<string>('experiment', '');
	const [filter, setFilter] = useDiveState<string>('decision', 'all');
	const best = useBest(cid);
	const exps = useSQLQuery(`
		SELECT experiment_id, title, change_kind, change_summary, hypothesis, status, decision, reason,
		       validation_status, array_to_string(targets, ', ') AS targets_text, runs, failed_runs, compute_s,
		       array_to_string(sizes, ', ') AS sizes_text, strftime(first_run_at, '%b %d %H:%M') AS first_label,
		       CASE WHEN date_trunc('day', first_run_at) = date_trunc('day', last_run_at)
		            THEN strftime(last_run_at, '%H:%M') ELSE strftime(last_run_at, '%b %d %H:%M') END AS last_label,
		       epoch(last_run_at - first_run_at) AS span_s,
		       delta_pct, vs_original_pct, effect
		FROM ${DB}.v_experiment_summary WHERE campaign_id = '${cid}'
		ORDER BY coalesce(first_run_at, created_at), experiment_id`);
	const comps = useSQLQuery(`
		SELECT experiment_id, scope, subject, duckling_size, threads, variant, baseline_source, baseline_s, candidate_s,
		       candidate_samples, delta_pct, original_s, vs_original_pct, effect, candidate_spill_bytes, baseline_spill_bytes
		FROM ${DB}.v_comparisons WHERE campaign_id = '${cid}'
		ORDER BY subject, duckling_size, threads, variant`);
	const all = rowsOf(exps);
	const rows = all.filter((r) =>
		filter === 'all' ? true : filter === 'other' ? !['accepted', 'rejected'].includes(S(r.decision)) : S(r.decision) === filter,
	);
	const compRows = rowsOf(comps);

	// When opened from the Overview, bring the expanded experiment into view once the list has rendered.
	const listReady = all.length > 0;
	useEffect(() => {
		if (!open || !listReady) return;
		const el = document.getElementById(`exp-${open}`);
		if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
		// Only when the tab first renders: expanding rows inside the tab shouldn't scroll.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [listReady]);

	const filters = [
		{ id: 'all', label: `All ${all.length}` },
		{ id: 'accepted', label: `Accepted ${all.filter((r) => S(r.decision) === 'accepted').length}` },
		{ id: 'rejected', label: `Rejected ${all.filter((r) => S(r.decision) === 'rejected').length}` },
		{ id: 'other', label: `Other ${all.filter((r) => !['accepted', 'rejected'].includes(S(r.decision))).length}` },
	];

	return (
		<div>
			<div className="flex gap-2 mb-4">
				{filters.map((f) => (
					<button
						key={f.id}
						type="button"
						onClick={() => setFilter(f.id)}
						className="text-xs cursor-pointer"
						style={{
							padding: '4px 12px',
							borderRadius: 999,
							border: `2px solid ${filter === f.id ? t.ink : t.border}`,
							background: filter === f.id ? t.accent : 'transparent',
							color: filter === f.id ? t.onAccent : t.muted,
							fontWeight: filter === f.id ? 600 : 400,
						}}
					>
						{f.label}
					</button>
				))}
			</div>
			{exps.isLoading ? (
				<div className="space-y-3">
					{[0, 1, 2, 3].map((i) => (
						<Skeleton key={i} t={t} h={40} />
					))}
				</div>
			) : exps.isError ? (
				<ErrorLine t={t} error={exps.error} />
			) : (
				<div style={{ borderTop: `1px solid ${t.border}` }}>
					{rows.map((r, i) => {
						const id = S(r.experiment_id);
						const isOpen = open === id;
						const d = NN(r.delta_pct);
						const mine = compRows.filter((c) => S(c.experiment_id) === id);
						return (
							<div key={id} id={`exp-${id}`} style={{ borderBottom: `1px solid ${t.border}`, scrollMarginTop: 16 }}>
								<button
									type="button"
									onClick={() => setOpen(isOpen ? '' : id)}
									className="w-full grid items-center gap-3 py-3 text-left cursor-pointer"
									style={{ gridTemplateColumns: '28px minmax(0, 1fr) 110px 96px 72px 16px' }}
									aria-expanded={isOpen}
								>
									<span className="text-xs" style={{ color: t.faint, fontFamily: MONO }}>
										{String(all.indexOf(r) + 1).padStart(2, '0')}
									</span>
									<span style={{ minWidth: 0 }}>
										<span className="flex items-center gap-2 text-sm font-medium" style={{ color: t.text, minWidth: 0 }}>
											{best.experiments.has(id) && <BestStar t={t} />}
											<span className="truncate">{S(r.title)}</span>
										</span>
										<span className="block text-xs truncate" style={{ color: t.muted }}>
											{S(r.change_kind).replace(/_/g, ' ')}
										</span>
									</span>
									<span className="text-xs text-right" style={{ color: t.muted }}>
										{dur(N(r.compute_s))}
									</span>
									<Decision t={t} decision={S(r.decision)} />
									<span className="text-sm text-right" style={{ color: d == null ? t.faint : effectColor(t, S(r.effect)), fontFamily: MONO }}>
										{pct(d, 0)}
									</span>
									<span className="text-xs" style={{ color: t.faint }}>
										{isOpen ? '−' : '+'}
									</span>
								</button>
								{isOpen && (
									<div
										className="space-y-5"
										style={{
											margin: '4px 8px 24px 40px',
											padding: 20,
											background: t.surface,
											border: `2px solid ${t.ink}`,
											borderRadius: 14,
											boxShadow: `-6px 6px 0 ${t.shadow}`,
										}}
									>
										<div className="flex flex-wrap items-center gap-2">
											<Chip t={t} strong color={DECISION_COLOR(t, S(r.decision))} text={S(r.decision) || 'pending'} />
											{['passed', 'failed'].includes(S(r.validation_status)) && (
												<Chip t={t} color={S(r.validation_status) === 'passed' ? t.positive : t.negative} text={`validation ${S(r.validation_status)}`} />
											)}
											{N(r.failed_runs) > 0 && <Chip t={t} color={t.negative} text={`${N(r.failed_runs)} failed`} />}
											{S(r.first_label) && <Chip t={t} text={`${S(r.first_label)} → ${S(r.last_label)} UTC · ${dur(N(r.span_s))}`} />}
											{S(r.sizes_text) && <Chip t={t} text={S(r.sizes_text).split(', ').map(cap).join(' · ')} />}
										</div>
										<div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
											<Story t={t} label="Hypothesis" accent={t.dark ? '#6FC2FF' : '#2BA5FF'} text={S(r.hypothesis)} />
											<Story t={t} label="What changed" accent="#FFDE00" text={S(r.change_summary)} />
											<Story t={t} label="Outcome" accent={DECISION_COLOR(t, S(r.decision))} text={S(r.reason)} />
										</div>
										<DiffView t={t} cid={cid} experimentId={id} />
										{mine.length > 0 && (
											<div className="space-y-2">
											<Label t={t}>Comparison</Label>
											<table className="w-full text-xs">
												<thead>
													<tr style={{ color: t.muted }}>
														{['Scope', 'Subject', 'Size', 'Variant', 'Reference', 'Ref.', 'Candidate', 'Change', 'vs original', ...(mine.some((c) => c.candidate_spill_bytes != null) ? ['Spill'] : [])].map((h, j) => (
															<th key={h} className={`font-normal pb-1 ${j > 4 ? 'text-right' : 'text-left'}`} style={{ borderBottom: `1px solid ${t.border}` }}>
																{h}
															</th>
														))}
													</tr>
												</thead>
												<tbody>
													{mine.map((c, j) => (
														<tr key={j} style={{ borderBottom: `1px solid ${t.grid}` }}>
															<td className="py-1 pr-2" style={{ color: t.muted }}>
																{S(c.scope)}
															</td>
															<td className="py-1 pr-2" style={{ fontWeight: best.isBest(c) ? 600 : 400 }}>
																{best.isBest(c) && (
																	<span className="mr-1">
																		<BestStar t={t} size={11} />
																	</span>
																)}
																{S(c.subject)}
															</td>
															<td className="py-1 pr-2">{sizeLabel(S(c.duckling_size), N(c.threads) || null)}</td>
															<td className="py-1 pr-2">{S(c.variant)}</td>
															<td className="py-1 pr-2" style={{ color: t.muted }}>
																{S(c.baseline_source) || '–'}
															</td>
															<td className="py-1 text-right">{secs(NN(c.baseline_s))}</td>
															<td className="py-1 text-right">{secs(NN(c.candidate_s))}</td>
															<td className="py-1 text-right" style={{ color: effectColor(t, S(c.effect)), fontFamily: MONO }}>
																{pct(NN(c.delta_pct))}
															</td>
															<td className="py-1 text-right" style={{ color: t.muted, fontFamily: MONO }}>
																{pct(NN(c.vs_original_pct))}
															</td>
															{mine.some((x) => x.candidate_spill_bytes != null) && (
																<td className="py-1 text-right" style={{ color: t.muted }}>
																	{bytes(NN(c.baseline_spill_bytes))} → {bytes(NN(c.candidate_spill_bytes))}
																</td>
															)}
														</tr>
													))}
												</tbody>
											</table>
											</div>
										)}
										<ExperimentRuns t={t} cid={cid} experimentId={id} />
									</div>
								)}
							</div>
						);
					})}
				</div>
			)}
		</div>
	);
}

const CHROME = { bar: '#ECE5DD', body: '#FFFFFF', ink: '#383838', add: '#E3F5F2', addText: '#068475', del: '#FFE9E7', delText: '#C42B22', hunk: '#1F8FE0', muted: '#7A746E' };

function DiffView({ t, cid, experimentId }: { t: Theme; cid: string; experimentId: string }) {
	const q = useSQLQuery(`
		SELECT diff_path, diff_text FROM ${DB}.experiments
		WHERE campaign_id = '${cid}' AND experiment_id = '${q1(experimentId)}'`);
	const row = rowsOf(q)[0] || {};
	const text = S(row.diff_text);
	if (q.isLoading) return <Skeleton t={t} h={60} />;
	if (q.isError) return <ErrorLine t={t} error={q.error} />;
	if (!text) return null;
	const lines = text.replace(/\n$/, '').split('\n');
	const added = lines.filter((l) => l.startsWith('+') && !l.startsWith('+++')).length;
	const removed = lines.filter((l) => l.startsWith('-') && !l.startsWith('---')).length;
	const style = (l: string) => {
		if (l.startsWith('+++') || l.startsWith('---') || l.startsWith('diff ') || l.startsWith('index ')) return { color: CHROME.muted, fontWeight: 700 };
		if (l.startsWith('@@')) return { color: CHROME.hunk };
		if (l.startsWith('+')) return { color: CHROME.addText, background: CHROME.add };
		if (l.startsWith('-')) return { color: CHROME.delText, background: CHROME.del };
		return { color: CHROME.ink };
	};
	return (
		<div style={{ border: `2px solid ${t.ink}`, borderRadius: 14, overflow: 'hidden' }}>
			<div className="flex items-center gap-3" style={{ background: CHROME.bar, borderBottom: `2px solid ${CHROME.ink}`, padding: '8px 14px' }}>
				<span className="flex gap-1">
					{['#FF7169', '#FFDE00', '#16AA98'].map((c) => (
						<span key={c} style={{ width: 10, height: 10, borderRadius: 10, background: c, border: `1.5px solid ${CHROME.ink}`, display: 'inline-block' }} />
					))}
				</span>
				<span className="text-xs truncate" style={{ fontFamily: MONO, color: CHROME.ink, minWidth: 0, flex: 1 }}>
					{S(row.diff_path) || 'candidate.diff'}
				</span>
				<span className="text-xs" style={{ fontFamily: MONO, whiteSpace: 'nowrap' }}>
					<span style={{ color: CHROME.addText }}>+{added}</span> <span style={{ color: CHROME.delText }}>−{removed}</span>
				</span>
			</div>
			<pre style={{ background: CHROME.body, margin: 0, padding: '10px 0', maxHeight: 380, overflow: 'auto', fontFamily: MONO, fontSize: 12, lineHeight: '18px' }}>
				{lines.map((l, i) => (
					<div key={i} style={{ ...style(l), padding: '0 14px', whiteSpace: 'pre', minWidth: 'fit-content' }}>
						{l || ' '}
					</div>
				))}
			</pre>
		</div>
	);
}

function Chip({ t, text, color, strong, mono }: { t: Theme; text: string; color?: string; strong?: boolean; mono?: boolean }) {
	return (
		<span
			className="inline-flex items-center gap-1 text-xs whitespace-nowrap"
			style={{
				padding: '2px 10px',
				borderRadius: 999,
				border: `${strong ? 2 : 1}px solid ${strong ? t.ink : t.border}`,
				color: t.text,
				fontWeight: strong ? 700 : 400,
				fontFamily: mono ? MONO : undefined,
				textTransform: strong ? 'uppercase' : undefined,
				letterSpacing: strong ? '0.06em' : undefined,
			}}
		>
			{color && <span style={{ width: 7, height: 7, borderRadius: 7, background: color, display: 'inline-block' }} />}
			{text}
		</span>
	);
}

// One step of the experiment's story: a tinted panel with a colored rule. Markdown headings render bold.
function Story({ t, label, text, accent }: { t: Theme; label: string; text: string; accent: string }) {
	if (!text) return null;
	const lines = (text || '').replace(/\s+$/, '').split('\n');
	return (
		<div style={{ background: t.bg, borderLeft: `4px solid ${accent}`, borderRadius: 2, padding: '12px 14px', minWidth: 0 }}>
			<p className="text-xs uppercase" style={{ fontFamily: HEAD, letterSpacing: '0.12em', color: t.text, fontWeight: 700 }}>
				{label}
			</p>
			{text ? (
				<div className="mt-2 text-sm" style={{ color: t.text, maxHeight: 240, overflow: 'auto', lineHeight: '20px' }}>
					{lines.map((l, i) => {
						const heading = /^#{1,6}\s/.test(l);
						return (
							<p key={i} style={{ fontWeight: heading ? 600 : 400, minHeight: l ? undefined : 10, whiteSpace: 'pre-wrap' }}>
								{heading ? l.replace(/^#{1,6}\s/, '') : l}
							</p>
						);
					})}
				</div>
			) : null}
		</div>
	);
}

// ── timeline ─────────────────────────────────────────────────────────────────

function PipelineGantt({ t, cid, s }: { t: Theme; cid: string; s: Record<string, unknown> }) {
	const [pickA, setA] = useDiveState<string>('runA', '');
	const [pickB, setB] = useDiveState<string>('runB', '');
	const stepRuns = useSQLQuery(`
		SELECT r.run_id, r.experiment_id, r.variant, r.subject, r.duckling_size, coalesce(r.threads, 0) AS threads,
		       r.status, r.wall_s, r.spill_bytes, strftime(r.started_at, '%b %d %H:%M') AS started_label, count(*) AS steps
		FROM ${DB}.runs r JOIN ${DB}.run_steps st USING (campaign_id, run_id)
		WHERE r.campaign_id = '${cid}'
		GROUP BY ALL ORDER BY min(r.started_at)`);
	const runs = rowsOf(stepRuns);

	// Defaults: the original and the headline experiment's run of the headline subject, size and threads.
	const hSubject = S(s.headline_subject_measured) || S(s.headline_subject);
	const hSize = S(s.headline_size_measured);
	const hThreads = N(s.headline_threads_measured);
	const matches = (r: Record<string, unknown>) =>
		(!hSubject || S(r.subject) === hSubject) && (!hSize || S(r.duckling_size) === hSize) && (!hThreads || N(r.threads) === hThreads) && S(r.status) === 'succeeded';
	const defA = runs.find((r) => matches(r) && S(r.variant) === 'original') || runs.find((r) => S(r.variant) === 'original') || runs[0];
	const defB =
		runs.find((r) => matches(r) && S(r.experiment_id) === S(s.headline_experiment_id)) ||
		[...runs].reverse().find((r) => matches(r) && S(r.variant) !== 'original') ||
		runs[runs.length - 1];
	const idA = runs.some((r) => S(r.run_id) === pickA) ? pickA : S(defA?.run_id);
	const idB = runs.some((r) => S(r.run_id) === pickB) ? pickB : S(defB?.run_id);

	const steps = useSQLQuery(
		`WITH s AS (
		   SELECT run_id, step_name, step_kind, status, duration_s, spill_bytes,
		          epoch_ms(started_at) AS t0, epoch_ms(finished_at) AS t1
		   FROM ${DB}.run_steps WHERE campaign_id = '${cid}' AND run_id IN ('${q1(idA)}', '${q1(idB)}')
		 )
		 SELECT *, t0 - min(t0) OVER (PARTITION BY run_id) AS off_ms FROM s`,
		{ enabled: idA !== '' },
	);

	const model = useMemo(() => {
		const rs = rowsOf(steps);
		type Bar = { off: number; len: number; dur: number; status: string; spill: number | null };
		const byName = new Map<string, { name: string; kind: string; a?: Bar; b?: Bar; order: number }>();
		let spanA = 0;
		let spanB = 0;
		let totalA = 0;
		let totalB = 0;
		for (const r of rs) {
			const name = S(r.step_name);
			const off = N(r.off_ms) / 1000;
			const len = r.t1 != null && r.t0 != null ? (N(r.t1) - N(r.t0)) / 1000 : N(r.duration_s);
			const bar = { off, len, dur: NN(r.duration_s) ?? len, status: S(r.status), spill: NN(r.spill_bytes) };
			const e = byName.get(name) || { name, kind: S(r.step_kind), order: Number.MAX_SAFE_INTEGER };
			if (S(r.run_id) === idA) {
				e.a = bar;
				e.order = Math.min(e.order, off);
				spanA = Math.max(spanA, off + len);
				totalA += bar.dur;
			}
			if (S(r.run_id) === idB) {
				e.b = bar;
				if (e.order === Number.MAX_SAFE_INTEGER) e.order = 1e12 + off;
				spanB = Math.max(spanB, off + len);
				totalB += bar.dur;
			}
			byName.set(name, e);
		}
		const all = [...byName.values()].sort((x, y) => x.order - y.order);
		const span = Math.max(spanA, spanB, 1);
		const big = all.filter((e) => Math.max(e.a?.dur || 0, e.b?.dur || 0) >= span * 0.015);
		const shown = big.length > 18 ? [...big].sort((x, y) => Math.max(y.a?.dur || 0, y.b?.dur || 0) - Math.max(x.a?.dur || 0, x.b?.dur || 0)).slice(0, 18).sort((x, y) => x.order - y.order) : big;
		const hidden = all.filter((e) => !shown.includes(e));
		const top = [...all].sort((x, y) => (y.a?.dur || 0) - (x.a?.dur || 0)).slice(0, 5);
		const maxSpill = Math.max(0, ...all.flatMap((e) => [e.a?.spill || 0, e.b?.spill || 0]));
		return { shown, hidden, span, spanA, spanB, totalA, totalB, top, maxSpill };
	}, [steps.data, idA, idB]);

	const runA = runs.find((r) => S(r.run_id) === idA);
	const runB = runs.find((r) => S(r.run_id) === idB);
	const runLabel = (r?: Record<string, unknown>) =>
		r ? `${S(r.variant)} · ${S(r.subject)} · ${sizeLabel(S(r.duckling_size), N(r.threads) || null)} · ${secs(NN(r.wall_s))}` : '';
	const ticks = niceTicks(model.span);
	// A spill column appears when any drawn step recorded spill (filled from query_history by the loader).
	const hasSpill = model.maxSpill > 0;
	const cols = hasSpill ? 'minmax(0, 200px) minmax(0, 1fr) 132px 170px' : 'minmax(0, 220px) minmax(0, 1fr) 132px';
	const SpillBars = ({ a, b }: { a?: number | null; b?: number | null }) => (
		<span className="grid items-center gap-2" style={{ gridTemplateColumns: 'minmax(0, 1fr) auto' }} title={`Spill: ${bytes(a ?? null)} in A, ${bytes(b ?? null)} in B`}>
			<span className="block">
				{[a, b].map((v, i) => (
					<span key={i} className="block rounded-sm" style={{ height: 4, marginTop: i ? 2 : 0, background: t.track }}>
						<span className="block rounded-sm" style={{ height: 4, width: `${((Number(v) || 0) / model.maxSpill) * 100}%`, background: t.duck, opacity: i ? 1 : 0.45 }} />
					</span>
				))}
			</span>
			<span className="whitespace-nowrap" style={{ fontSize: 10, color: t.muted, fontFamily: MONO }}>
				{a || b ? `${a ? bytes(Number(a)) : '0'} → ${b ? bytes(Number(b)) : '0'}` : ''}
			</span>
		</span>
	);
	const colA = t.neutral;
	const colB = t.primary;

	const RunSelect = ({ value, onChange, color, label }: { value: string; onChange: (v: string) => void; color: string; label: string }) => (
		<label className="flex items-center gap-2 text-xs" style={{ color: t.muted, minWidth: 0 }}>
			<span style={{ width: 10, height: 10, background: color, borderRadius: 2, flexShrink: 0 }} />
			<span style={{ flexShrink: 0 }}>{label}</span>
			<select
				value={value}
				onChange={(e) => onChange(e.target.value)}
				className="text-xs cursor-pointer"
				style={{ background: t.surface, color: t.text, border: `2px solid ${t.ink}`, borderRadius: 999, padding: '3px 10px', minWidth: 0, maxWidth: 340 }}
			>
				{runs.map((r) => (
					<option key={S(r.run_id)} value={S(r.run_id)}>
						{`${S(r.experiment_id)} · ${runLabel(r)}${S(r.status) !== 'succeeded' ? ` (${S(r.status)})` : ''}`}
					</option>
				))}
			</select>
		</label>
	);

	return (
		<section>
			<SectionTitle
				t={t}
				title="Where pipeline time goes"
			/>
			{stepRuns.isLoading ? (
				<Skeleton t={t} h={200} />
			) : stepRuns.isError ? (
				<ErrorLine t={t} error={stepRuns.error} />
			) : runs.length === 0 ? (
				<p className="text-sm" style={{ color: t.faint }}>
					No run has step timings yet. Log runs with <code style={{ fontFamily: MONO }}>--dbt-run-results</code> or a <code style={{ fontFamily: MONO }}>steps</code> list.
				</p>
			) : (
				<>
					<div className="flex flex-wrap gap-4 mb-4">
						<RunSelect value={idA} onChange={setA} color={colA} label="A" />
						<RunSelect value={idB} onChange={setB} color={colB} label="B" />
					</div>
					{steps.isLoading ? (
						<Skeleton t={t} h={220} />
					) : steps.isError ? (
						<ErrorLine t={t} error={steps.error} />
					) : (
						<div>
							<div className="grid gap-3 text-xs pb-1" style={{ gridTemplateColumns: cols, color: t.faint }}>
								<span>Step</span>
								<span className="relative" style={{ height: 14 }}>
									{ticks.map((x, i) => (
										<span
											key={x}
											className="absolute whitespace-nowrap"
											style={{
												left: `${(x / model.span) * 100}%`,
												transform: i === 0 ? 'none' : x / model.span > 0.9 ? 'translateX(-100%)' : 'translateX(-50%)',
											}}
										>
											{dur(x)}
										</span>
									))}
								</span>
								<span className="text-right">A → B</span>
								{hasSpill && <span>Spill</span>}
							</div>
							{model.shown.map((e) => {
								const a = e.a;
								const b = e.b;
								const change = a && b && a.dur ? (100 * (b.dur - a.dur)) / a.dur : null;
								return (
									<div
										key={e.name}
										className="grid gap-3 items-center py-1"
										style={{ gridTemplateColumns: cols, borderTop: `1px solid ${t.grid}` }}
									>
										<span className="text-xs truncate" style={{ color: t.text, fontFamily: MONO }} title={`${e.name} (${e.kind})`}>
											{e.name}
										</span>
										<span className="relative block" style={{ height: 16 }}>
											{ticks.map((x) => (
												<span key={x} className="absolute" style={{ left: `${(x / model.span) * 100}%`, top: 0, bottom: 0, width: 1, background: t.grid }} />
											))}
											{a && (
												<span
													className="absolute rounded-sm"
													title={`A: ${secs(a.dur)} from ${dur(a.off)}`}
													style={{ left: `${(a.off / model.span) * 100}%`, width: `${Math.max(0.3, (a.len / model.span) * 100)}%`, top: 1, height: 6, background: a.status && !['success', 'succeeded', 'pass'].includes(a.status) ? t.negative : colA }}
												/>
											)}
											{b && (
												<span
													className="absolute rounded-sm"
													title={`B: ${secs(b.dur)} from ${dur(b.off)}`}
													style={{ left: `${(b.off / model.span) * 100}%`, width: `${Math.max(0.3, (b.len / model.span) * 100)}%`, top: 9, height: 6, background: b.status && !['success', 'succeeded', 'pass'].includes(b.status) ? t.negative : colB }}
												/>
											)}
										</span>
										<span className="text-xs text-right whitespace-nowrap" style={{ fontFamily: MONO, color: t.muted }}>
											{a ? secs(a.dur) : '–'} → {b ? secs(b.dur) : '–'}{' '}
											<span style={{ color: change == null ? t.faint : change <= -5 ? t.positive : change >= 5 ? t.negative : t.faint }}>{change == null ? '' : pct(change, 0)}</span>
										</span>
										{hasSpill && <SpillBars a={a?.spill} b={b?.spill} />}
									</div>
								);
							})}
							{model.hidden.length > 0 && (
								<p className="text-xs py-2" style={{ color: t.faint, borderTop: `1px solid ${t.grid}` }}>
									+{model.hidden.length} short steps: {secs(model.hidden.reduce((x, e) => x + (e.a?.dur || 0), 0))} /{' '}
									{secs(model.hidden.reduce((x, e) => x + (e.b?.dur || 0), 0))}
								</p>
							)}
							<div className="grid gap-3 text-xs pt-2" style={{ gridTemplateColumns: cols, borderTop: `1px solid ${t.border}` }}>
								<span style={{ color: t.text }}>End to end</span>
								<span />
								<span className="text-right" style={{ fontFamily: MONO, color: t.text }}>
									{secs(NN(runA?.wall_s) ?? model.spanA)} → {secs(NN(runB?.wall_s) ?? model.spanB)}
								</span>
								{hasSpill && (
									<span className="text-right" style={{ fontFamily: MONO, color: t.muted }}>
										{bytes(NN(runA?.spill_bytes))} → {bytes(NN(runB?.spill_bytes))}
									</span>
								)}
							</div>
						</div>
					)}
				</>
			)}
		</section>
	);
}

function niceTicks(span: number): number[] {
	const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];
	const step = steps.find((x) => span / x <= 6) || 43200;
	const out: number[] = [];
	for (let x = 0; x <= span; x += step) out.push(x);
	return out;
}

const LANES = ['plan', 'provision', 'warmup', 'profile', 'run', 'validate', 'analyze', 'decide', 'wait', 'cleanup', 'report', 'error'];

function laneColor(t: Theme, kind: string): string {
	switch (kind) {
		case 'run':
			return t.primary;
		case 'profile':
			return t.dark ? '#97D4FF' : '#6FC2FF';
		case 'warmup':
			return t.neutral;
		case 'validate':
			return t.dark ? '#50C7B7' : '#16AA98';
		case 'analyze':
			return t.dark ? '#FFDE00' : '#BFA800';
		case 'decide':
			return t.text;
		case 'error':
			return t.negative;
		default:
			return t.duck; // plan, provision, cleanup, report and anything custom
	}
}

function AgentTimeline({ t, cid }: { t: Theme; cid: string }) {
	const events = useSQLQuery(`
		SELECT event_id, experiment_id, kind, summary,
		       epoch_ms(started_at) AS t0, epoch_ms(coalesce(finished_at, started_at)) AS t1,
		       strftime(started_at, '%b %d %H:%M:%S') AS label
		FROM ${DB}.agent_events WHERE campaign_id = '${cid}' AND started_at IS NOT NULL
		ORDER BY started_at`);
	const totals = useSQLQuery(`
		SELECT kind, count(*) AS n, sum(epoch(coalesce(finished_at, started_at) - started_at)) AS secs
		FROM ${DB}.agent_events WHERE campaign_id = '${cid}' GROUP BY ALL ORDER BY secs DESC`);

	const model = useMemo(() => {
		const ev = rowsOf(events).map((r) => ({ ...r, t0: N(r.t0), t1: Math.max(N(r.t1), N(r.t0)) }));
		if (!ev.length) return null;
		// Compress idle gaps longer than 90 minutes so overnight pauses don't flatten the timeline.
		const GAP = 90 * 60 * 1000;
		const sorted = [...ev].sort((a, b) => a.t0 - b.t0);
		const segs: { a: number; b: number }[] = [];
		for (const e of sorted) {
			const last = segs[segs.length - 1];
			if (last && e.t0 - last.b <= GAP) last.b = Math.max(last.b, e.t1);
			else segs.push({ a: e.t0, b: e.t1 });
		}
		const BREAK = 3; // percent of width per compressed gap
		const active = segs.reduce((x, s) => x + Math.max(s.b - s.a, 1), 0);
		const avail = 100 - BREAK * (segs.length - 1);
		let x = 0;
		const mapped = segs.map((s, i) => {
			const w = (Math.max(s.b - s.a, 1) / active) * avail;
			const m = { ...s, x0: x, x1: x + w, gapAfter: i < segs.length - 1 ? segs[i + 1].a - s.b : 0 };
			x += w + BREAK;
			return m;
		});
		const toX = (ms: number) => {
			const s = mapped.find((m) => ms >= m.a && ms <= m.b) || mapped.reduce((p, m) => (ms >= m.a ? m : p), mapped[0]);
			const f = s.b > s.a ? (Math.min(Math.max(ms, s.a), s.b) - s.a) / (s.b - s.a) : 0;
			return s.x0 + f * (s.x1 - s.x0);
		};
		const lanes = LANES.filter((k) => ev.some((e) => S(e.kind) === k)).concat(
			[...new Set(ev.map((e) => S(e.kind)))].filter((k) => !LANES.includes(k)),
		);
		return { ev, mapped, toX, lanes };
	}, [events.data]);

	const tot = rowsOf(totals);
	const totalSecs = tot.reduce((x, r) => x + N(r.secs), 0) || 1;

	return (
		<section>
			<SectionTitle
				t={t}
				title="What the agent did, and when"
			/>
			{events.isLoading ? (
				<Skeleton t={t} h={180} />
			) : events.isError ? (
				<ErrorLine t={t} error={events.error} />
			) : !model ? (
				<p className="text-sm" style={{ color: t.faint }}>
					No agent events logged for this campaign.
				</p>
			) : (
				<div>
					{model.lanes.map((lane) => (
						<div key={lane} className="grid items-center gap-3" style={{ gridTemplateColumns: '80px minmax(0, 1fr)', height: 22 }}>
							<span className="text-xs" style={{ color: t.muted, fontFamily: MONO }}>
								{lane}
							</span>
							<span className="relative block" style={{ height: 22, borderTop: `1px solid ${t.grid}` }}>
								{model.mapped.slice(0, -1).map((m, i) => (
									<span key={i} className="absolute" style={{ left: `${m.x1}%`, width: '3%', top: 0, bottom: 0, background: `repeating-linear-gradient(135deg, ${t.grid} 0 2px, transparent 2px 5px)` }} />
								))}
								{model.ev
									.filter((e) => S(e.kind) === lane)
									.map((e) => {
										const x0 = model.toX(e.t0);
										const x1 = model.toX(e.t1);
										return (
											<span
												key={S(e.event_id)}
												className="absolute rounded-sm"
												title={`${S(e.label)} UTC · ${dur((e.t1 - e.t0) / 1000)}\n${S(e.experiment_id)}\n${S(e.summary)}`}
												style={{ left: `${x0}%`, width: `${Math.max(0.25, x1 - x0)}%`, top: 6, height: 10, background: laneColor(t, lane), opacity: 0.9 }}
											/>
										);
									})}
							</span>
						</div>
					))}
					<div className="grid gap-3 text-xs mt-1" style={{ gridTemplateColumns: '80px minmax(0, 1fr)', color: t.faint }}>
						<span />
						<span className="relative" style={{ height: 30 }}>
							{model.mapped.map((m, i) => (
								<span key={i}>
									<span className="absolute" style={{ left: `${m.x0}%` }}>
										{new Date(m.a).toISOString().slice(5, 16).replace('T', ' ')}
									</span>
									{m.gapAfter > 0 && (
										<span className="absolute" style={{ left: `${m.x1}%`, top: 14 }}>
											{dur(m.gapAfter / 1000)} idle
										</span>
									)}
								</span>
							))}
						</span>
					</div>

					<div className="mt-6">
						<Label t={t}>Time by activity</Label>
						{totals.isLoading ? (
							<Skeleton t={t} h={80} />
						) : (
							<div className="mt-2 space-y-1">
								{tot.map((r) => {
									const share = (N(r.secs) / totalSecs) * 100;
									return (
										<div key={S(r.kind)} className="grid items-center gap-3 text-xs" style={{ gridTemplateColumns: '80px minmax(0, 1fr) 64px 72px' }}>
											<span style={{ fontFamily: MONO, color: t.text }}>{S(r.kind)}</span>
											<span className="block rounded-sm" style={{ height: 6, background: t.track }}>
												<span className="block rounded-sm" style={{ height: 6, width: `${share}%`, background: laneColor(t, S(r.kind)) }} />
											</span>
											<span className="text-right" style={{ color: t.muted }}>
												{N(r.n)}×
											</span>
											<span className="text-right" style={{ color: t.muted, fontFamily: MONO }}>
												{dur(N(r.secs))}
											</span>
										</div>
									);
								})}
							</div>
						)}
					</div>
				</div>
			)}
		</section>
	);
}

// ── runs ─────────────────────────────────────────────────────────────────────

function RunDetails({ t, r }: { t: Theme; r: Record<string, unknown> }) {
	const items: [string, string, boolean?][] = [
		['Run id', S(r.run_id), true],
		['Label', S(r.label)],
		['Scope', S(r.scope)],
		['Service account', S(r.service_account), true],
		['Databases', S(r.databases_text), true],
		['Triggered', S(r.triggered_label) && `${S(r.triggered_label)} UTC`],
		['Started → finished', S(r.started_label) && `${S(r.started_label)} → ${S(r.finished_label)} UTC`],
		['Wait', NN(r.wait_s) == null ? '' : secs(NN(r.wait_s))],
		['Rows', NN(r.rows_out) == null ? '' : N(r.rows_out).toLocaleString()],
		['Query ids', S(r.query_ids_text), true],
		['Command', S(r.command), true],
		['Error', S(r.error)],
		['Notes', S(r.notes)],
	];
	return (
		<div className="grid gap-x-6 gap-y-1 text-xs" style={{ gridTemplateColumns: '130px minmax(0, 1fr)' }}>
			{items
				.filter(([, v]) => v)
				.map(([k, v, mono]) => (
					<Fragment key={k}>
						<span style={{ color: t.muted }}>{k}</span>
						<span style={{ color: k === 'Error' ? t.negative : t.text, fontFamily: mono ? MONO : undefined, wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>{v}</span>
					</Fragment>
				))}
		</div>
	);
}

function ExperimentRuns({ t, cid, experimentId }: { t: Theme; cid: string; experimentId: string }) {
	const [openRun, setOpenRun] = useState('');
	const runs = useSQLQuery(`
		SELECT run_id, experiment_id, variant, label, subject, scope, duckling_size, threads,
		       status, wall_s, server_s, wait_s, spill_bytes, rows_out, error, notes, service_account, command,
		       array_to_string(databases, ', ') AS databases_text, array_to_string(query_ids, ', ') AS query_ids_text,
		       strftime(coalesce(triggered_at, started_at), '%b %d %H:%M') AS at,
		       strftime(triggered_at, '%Y-%m-%d %H:%M:%S') AS triggered_label,
		       strftime(started_at, '%Y-%m-%d %H:%M:%S') AS started_label,
		       strftime(finished_at, '%Y-%m-%d %H:%M:%S') AS finished_label
		FROM ${DB}.runs
		WHERE campaign_id = '${cid}' AND experiment_id = '${q1(experimentId)}' AND coalesce(purpose, 'measure') = 'measure'
		ORDER BY coalesce(triggered_at, started_at, finished_at) NULLS LAST, run_id`);
	const all = rowsOf(runs);
	if (runs.isLoading) return <Skeleton t={t} h={60} />;
	if (runs.isError) return <ErrorLine t={t} error={runs.error} />;
	if (!all.length) return null;
	const statusColor = (st: string) => (st === 'succeeded' ? t.muted : t.negative);
	// Server time, wait and spill come from query_history; show a column only when some run has it.
	const optional = [
		{ key: 'server_s', label: 'Server', fmt: secs },
		{ key: 'wait_s', label: 'Wait', fmt: secs },
		{ key: 'spill_bytes', label: 'Spill', fmt: bytes },
	].filter((o) => all.some((r) => r[o.key] != null));
	const head = ['Started (UTC)', 'Variant', 'Subject', 'Duckling', 'Status', 'Wall', ...optional.map((o) => o.label)];
	// Fastest successful run per subject (a single sample; the comparison above uses medians).
	const fastest = new Set(
		[...new Set(all.map((r) => S(r.subject)))].map((sub) => {
			const ok = all.filter((r) => S(r.subject) === sub && S(r.status) === 'succeeded' && r.wall_s != null && !['original', 'baseline'].includes(S(r.variant)));
			return ok.length > 1 ? S(ok.reduce((a, b) => (N(b.wall_s) < N(a.wall_s) ? b : a)).run_id) : '';
		}),
	);

	return (
		<div className="space-y-2">
			<Label t={t}>Runs</Label>
			<table className="w-full text-xs">
				<thead>
					<tr style={{ color: t.muted }}>
						{head.map((h, i) => (
							<th key={h} className={`font-normal pb-1 ${i > 4 ? 'text-right' : 'text-left'}`} style={{ borderBottom: `1px solid ${t.border}` }}>
								{h}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{all.map((r) => (
						<Fragment key={S(r.run_id)}>
							<tr
								onClick={() => setOpenRun(openRun === S(r.run_id) ? '' : S(r.run_id))}
								className="cursor-pointer"
								style={{ borderBottom: `1px solid ${t.grid}`, background: openRun === S(r.run_id) ? t.track : 'transparent' }}
							>
								<td className="py-1 pr-2 whitespace-nowrap" style={{ color: t.muted }}>
									{S(r.at)}
								</td>
								<td className="py-1 pr-2">{S(r.variant)}</td>
								<td className="py-1 pr-2">{S(r.subject)}</td>
								<td className="py-1 pr-2 whitespace-nowrap">{sizeLabel(S(r.duckling_size), NN(r.threads))}</td>
								<td className="py-1 pr-2" style={{ color: statusColor(S(r.status)) }}>
									{S(r.status)}
								</td>
								<td className="py-1 text-right whitespace-nowrap" style={{ fontFamily: MONO, fontWeight: fastest.has(S(r.run_id)) ? 700 : 400 }} title={fastest.has(S(r.run_id)) ? 'Fastest run' : undefined}>
									{fastest.has(S(r.run_id)) && (
										<span className="mr-1">
											<BestStar t={t} size={11} />
										</span>
									)}
									{secs(NN(r.wall_s))}
								</td>
								{optional.map((o) => (
									<td key={o.key} className="py-1 text-right" style={{ fontFamily: MONO, color: t.muted }}>
										{o.fmt(NN(r[o.key]))}
									</td>
								))}
							</tr>
							{openRun === S(r.run_id) && (
								<tr style={{ borderBottom: `1px solid ${t.border}`, background: t.track }}>
									<td colSpan={head.length} className="px-3 pb-3 pt-1">
										<RunDetails t={t} r={r} />
									</td>
								</tr>
							)}
						</Fragment>
					))}
				</tbody>
			</table>
		</div>
	);
}
