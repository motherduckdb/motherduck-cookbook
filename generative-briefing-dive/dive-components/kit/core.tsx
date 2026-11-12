// ---------------------------------------------------------------------------
// MotherDuck Dive components: core
// Themes, makeTheme, ThemeCtx, formatting, and the shadcn-shaped primitives every
// component uses (Card, Badge, Button, Segmented, Skeleton, Delta, Sparkline,
// ChartTooltip, Legend). Always included.
// ---------------------------------------------------------------------------

// @imports
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import {
	Bar,
	BarChart,
	CartesianGrid,
	Cell,
	Line,
	LineChart,
	ReferenceDot,
	ReferenceLine,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from 'recharts';
import {
	AlertOctagon,
	AlertTriangle,
	ArrowDown,
	ArrowDownRight,
	ArrowUp,
	ArrowUpRight,
	CheckCircle2,
	ChevronDown,
	ChevronRight,
	Code2,
	Eye,
	EyeOff,
	Info,
	Minus,
	MoveHorizontal,
	BarChart3,
	Database,
	DollarSign,
	FileText,
	Gauge,
	Hash,
	Package,
	Percent,
	Repeat,
	ShieldOff,
	ShoppingCart,
	Star,
	Table2,
	TrendingUp,
	Undo2,
	Users,
	MousePointerClick,
	Bot,
	Search,
	MessageCircleQuestion,
	ArrowRight,
	Loader2,
	SearchCheck,
} from 'lucide-react';
// @imports-end// ---- Themes ---------------------------------------------------------------

const THEMES: Record<string, any> = {
	// The default MotherDuck Dive look from get_dive_guide: flat, no card chrome,
	// #f8f8f8 page, MotherDuck palette, positive/negative only for directional values.
	motherduck: {
		name: 'MotherDuck',
		flat: true,
		bg: '#f8f8f8',
		panel: '#f8f8f8',
		text: '#231f20',
		muted: '#6a6a6a',
		line: '#e2e2e2',
		rule: '#231f20',
		grid: '#eeeeee',
		border: '1px solid #e2e2e2',
		radius: 0,
		shadow: 'none',
		accent: '#0777b3',
		accentSoft: '#e6f1f8',
		highlight: '#0777b3',
		series: ['#0777b3', '#bd4e35', '#2d7a00', '#e18727', '#638cad', '#adadad'],
		mutedSeries: '#cfcfcf',
		gap: 40,
		font: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
		heading: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
		tones: {
			critical: { fg: '#bc1200', bg: 'transparent', line: '#bc1200' },
			warning: { fg: '#231f20', bg: 'transparent', line: '#231f20' },
			positive: { fg: '#2d7a00', bg: 'transparent', line: '#2d7a00' },
			info: { fg: '#231f20', bg: 'transparent', line: '#231f20' },
			neutral: { fg: '#6a6a6a', bg: 'transparent', line: '#e2e2e2' },
		},
	},
	swiss: {
		name: 'Swiss',
		flat: true,
		mono: true, // greyscale series told apart by dashes; red only for emphasis
		bg: '#ffffff',
		panel: '#ffffff',
		text: '#111111',
		muted: '#767676',
		line: '#e6e6e6',
		rule: '#111111',
		grid: '#efefef',
		border: '1px solid #e6e6e6',
		radius: 0,
		shadow: 'none',
		accent: '#e4002b',
		accentSoft: '#f5f5f5',
		highlight: '#e4002b',
		series: ['#111111', '#9a9a9a', '#5c5c5c', '#c4c4c4', '#2f2f2f', '#7a7a7a'],
		dashes: [undefined, undefined, '6 3', '2 3', '10 3 2 3', '1 2'],
		mutedSeries: '#d4d4d4',
		gap: 40,
		font: '"Helvetica Neue", Helvetica, "Inter", Arial, sans-serif',
		heading: '"Helvetica Neue", Helvetica, "Inter", Arial, sans-serif',
		tones: {
			critical: { fg: '#e4002b', bg: 'transparent', line: '#e4002b' },
			warning: { fg: '#111111', bg: 'transparent', line: '#111111' },
			positive: { fg: '#111111', bg: 'transparent', line: '#111111' },
			info: { fg: '#111111', bg: 'transparent', line: '#111111' },
			neutral: { fg: '#767676', bg: 'transparent', line: '#e6e6e6' },
		},
	},
	// Tufte: cream page, ink text, a book serif, no chrome and no gridlines. Data
	// ink only: greyscale series told apart by dashes, one muted red for emphasis.
	paper: {
		name: 'Paper',
		flat: true,
		mono: true,
		gridlines: false,
		displayWeight: 400,
		displayTracking: 0,
		bg: '#fffff8',
		panel: '#fffff8',
		text: '#111111',
		muted: '#6b6b63',
		line: '#e3e1d6',
		rule: '#111111',
		grid: '#ebe9df',
		border: '1px solid #e3e1d6',
		radius: 0,
		shadow: 'none',
		accent: '#a6261d',
		accentSoft: '#f4f1e4',
		highlight: '#a6261d',
		series: ['#111111', '#8c8c84', '#4d4d48', '#b9b8ae', '#2b2b28', '#6f6f68'],
		dashes: [undefined, undefined, '6 3', '2 3', '10 3 2 3', '1 2'],
		mutedSeries: '#d6d4c8',
		gap: 40,
		font: 'et-book, "ETBembo", Palatino, "Palatino Linotype", "Book Antiqua", Georgia, serif',
		heading: 'et-book, "ETBembo", Palatino, "Palatino Linotype", "Book Antiqua", Georgia, serif',
		tones: {
			critical: { fg: '#a6261d', bg: 'transparent', line: '#a6261d' },
			warning: { fg: '#111111', bg: 'transparent', line: '#111111' },
			positive: { fg: '#111111', bg: 'transparent', line: '#111111' },
			info: { fg: '#111111', bg: 'transparent', line: '#111111' },
			neutral: { fg: '#6b6b63', bg: 'transparent', line: '#e3e1d6' },
		},
	},
	duck: {
		name: 'Duck',
		bg: '#f4efea',
		panel: '#ffffff',
		text: '#383838',
		muted: '#6a6a6a',
		line: '#383838',
		grid: '#e9e2da',
		border: '2px solid #383838',
		radius: 2,
		shadow: '-4px 4px 0 #383838',
		accent: '#383838',
		accentSoft: '#fff7c2',
		highlight: '#ff7169',
		series: ['#6fc2ff', '#ff7169', '#53dbc9', '#ffde00', '#383838', '#b39ddb'],
		mutedSeries: '#d4cbc1',
		font: '"Inter", ui-sans-serif, system-ui, sans-serif',
		heading: 'ui-monospace, "SFMono-Regular", Menlo, monospace',
		tones: {
			critical: { fg: '#383838', bg: '#ffd5d2', line: '#383838' },
			warning: { fg: '#383838', bg: '#fff1a8', line: '#383838' },
			positive: { fg: '#383838', bg: '#c9f4ee', line: '#383838' },
			info: { fg: '#383838', bg: '#d6edff', line: '#383838' },
			neutral: { fg: '#383838', bg: '#f4efea', line: '#383838' },
		},
	},
	night: {
		name: 'Night',
		bg: '#0e1116',
		panel: '#161b22',
		text: '#e6e8eb',
		muted: '#8b949e',
		line: '#262c36',
		grid: '#202630',
		border: '1px solid #262c36',
		radius: 10,
		shadow: 'none',
		accent: '#58a6ff',
		accentSoft: '#13243a',
		highlight: '#f2b84b',
		series: ['#58a6ff', '#ff7b72', '#56d364', '#f2b84b', '#bc8cff', '#76e3ea'],
		mutedSeries: '#3a414d',
		font: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
		heading: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
		tones: {
			critical: { fg: '#ff9b93', bg: '#2d1517', line: '#5c2326' },
			warning: { fg: '#f2c46b', bg: '#2b2111', line: '#5a4319' },
			positive: { fg: '#7ee08a', bg: '#132516', line: '#24502b' },
			info: { fg: '#8cc4ff', bg: '#11223a', line: '#1f4170' },
			neutral: { fg: '#c9d1d9', bg: '#1c222b', line: '#2d3440' },
		},
	},
};

// A brand theme: a base theme plus any tokens you set. Accepts a theme key
// ('swiss') or { base, name, accent, ...tokens }. Any token in THEMES can be set
// (bg, panel, text, muted, line, rule, grid, border, radius, shadow, font, heading,
// series, dashes, highlight, accentSoft, flat, mono, gridlines, displayWeight, displayTracking);
// tones merge per tone. In a greyscale (mono) base, Swiss or Paper, the accent marks only
// emphasis: highlights, the critical tone and active controls; series stay
// greyscale. Elsewhere the accent leads the series palette.
function makeTheme(spec: any): any {
	if (!spec) return THEMES.motherduck;
	if (typeof spec === 'string') return THEMES[spec] || THEMES.motherduck;
	const base = THEMES[spec.base] || THEMES.motherduck;
	const { base: _b, tones, ...tokens } = spec;
	const t: any = { ...base, ...tokens, tones: { ...base.tones }, name: spec.name || base.name };
	Object.entries(tones || {}).forEach(([k, v]: any) => (t.tones[k] = { ...base.tones[k], ...v }));
	if (spec.accent) {
		t.highlight = spec.highlight || spec.accent;
		t.accentSoft = spec.accentSoft || (base.mono ? base.accentSoft : spec.accent + '1f');
		if (base.mono && !tones?.critical) t.tones.critical = { ...base.tones.critical, fg: spec.accent, line: spec.accent };
		if (!base.mono && !spec.series) t.series = [spec.accent, ...base.series.filter((c: string) => c !== base.accent)].slice(0, 6);
	}
	if (spec.series) {
		t.mono = spec.mono ?? false;
		t.dashes = spec.dashes;
	}
	return t;
}

const ThemeCtx = createContext(THEMES.motherduck);
const useTheme = () => useContext(ThemeCtx);

// ---- Formatting -------------------------------------------------------------

const N = (v: unknown): number | null => {
	if (v == null || v === '') return null;
	const n = Number(v);
	return Number.isFinite(n) ? n : null;
};

const compact = (n: number, digits = 1) => {
	const a = Math.abs(n);
	if (a >= 1e9) return (n / 1e9).toFixed(digits) + 'B';
	if (a >= 1e6) return (n / 1e6).toFixed(digits) + 'M';
	if (a >= 1e4) return (n / 1e3).toFixed(digits) + 'K';
	if (a >= 1e3) return (n / 1e3).toFixed(digits) + 'K';
	return Number.isInteger(n) ? String(n) : n.toFixed(digits);
};

function fmt(value: unknown, format?: string): string {
	if (format === 'text') return value == null ? '—' : String(value);
	const n = N(value);
	if (n == null) return '—';
	switch (format) {
		case 'currency':
			return Math.abs(n) >= 1000 ? '$' + compact(n) : '$' + n.toFixed(2);
		case 'pct':
			return (n * 100).toFixed(Math.abs(n) < 0.1 ? 2 : 1) + '%';
		case 'delta_pct':
			return (n > 0 ? '+' : '') + (n * 100).toFixed(0) + '%';
		case 'count':
			return compact(n, Math.abs(n) >= 1e6 ? 2 : 1);
		default:
			return Math.abs(n) >= 1e4 ? compact(n) : n.toFixed(1);
	}
}

// Axis ticks: as short as possible, but never two different ticks with one label
// (2,500 and 3,000 both rounding to "3K").
const tickNum = (v: number) => {
	const a = Math.abs(v);
	const unit = a >= 1e9 ? [1e9, 'B'] : a >= 1e6 ? [1e6, 'M'] : a >= 1e3 ? [1e3, 'K'] : [1, ''];
	const x = v / (unit[0] as number);
	const s = Number.isInteger(x) ? String(x) : x.toFixed(1).replace(/\.0$/, '');
	return s + unit[1];
};
// Round axis steps (1, 2, 2.5, 5 x 10^n) from zero to just above the max.
function niceTicks(max: number, count = 4) {
	if (!(max > 0)) return undefined;
	const raw = max / count;
	const mag = Math.pow(10, Math.floor(Math.log10(raw)));
	const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) || raw;
	const ticks = [];
	for (let v = 0; v < max + step * 0.999; v += step) ticks.push(Number(v.toPrecision(12)));
	return ticks;
}

const axisFmt = (format?: string) => (v: number) => {
	if (format === 'pct') return (v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0) + '%';
	if (format === 'currency') return '$' + tickNum(v);
	return tickNum(v);
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtPeriod = (iso: string, grain?: string) => {
	const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
	if (!m) return String(iso);
	const mon = MONTHS[Number(m[2]) - 1];
	if (grain === 'month') return `${mon} ${m[1].slice(2)}`;
	return `${Number(m[3])} ${mon} ${m[1].slice(2)}`;
};

const humanize = (s: string) => String(s ?? '').replace(/_/g, ' ');

// Icon per measure, chosen from its name.
function metricIcon(key: string, format?: string) {
	const k = String(key).toLowerCase();
	if (/cancel/.test(k)) return ShieldOff;
	if (/visitor|session|pageview|users/.test(k) && !/assistant/.test(k)) return Users;
	if (/assistant|unanswered|question/.test(k)) return MessageCircleQuestion;
	if (/click|ctr|conversion/.test(k)) return MousePointerClick;
	if (/ai_traffic|requests|agent|bot/.test(k)) return Bot;
	if (/search|quer/.test(k)) return Search;
	if (/return|refund|reversed|not_as_described|battery|doa|chargeback/.test(k)) return Undo2;
	if (/trade|resale|downgrade|quote|payout/.test(k)) return Repeat;
	if (/gmv|price/.test(k)) return ShoppingCart;
	if (/quality/.test(k)) return Star;
	if (/units|plans_sold|claims/.test(k)) return Package;
	if (format === 'pct') return Percent;
	if (format === 'currency') return DollarSign;
	return Hash;
}

// Measures where a decrease is good news. Used for delta colours only.
const DOWN_IS_GOOD = /(return|cancel|refund|chargeback|cost|reversed|downgrade|not_as_described|battery|doa|claim)/;

// ---- Primitives (shadcn-shaped) ----------------------------------------------

function Card({ title, description, actions, footer, children, tone, style, highlighted, icon }: any) {
	const Icon = icon;
	const t = useTheme();
	const toneC = tone ? t.tones[tone] : null;
	if (t.flat) {
		return (
			<section style={{ borderTop: `1px solid ${t.rule}`, color: t.text, minWidth: 0, background: highlighted ? t.accentSoft : 'transparent', transition: 'background 300ms', ...style }} className="flex flex-col">
				{(title || actions) && (
					<header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 pt-3">
						<div className="min-w-0" style={{ flex: '1 1 240px' }}>
							{title && (
								<h3 className="flex items-center gap-2 text-base font-bold leading-snug" style={{ fontFamily: t.heading, letterSpacing: -0.2 }}>
									{Icon && <Icon size={15} strokeWidth={2} style={{ color: t.muted, flexShrink: 0 }} />}
									{title}
								</h3>
							)}
							{description && (
								<p className="mt-1 text-sm leading-snug" style={{ color: t.muted, maxWidth: 680 }}>
									{description}
								</p>
							)}
						</div>
						{actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
					</header>
				)}
				<div className="min-w-0 flex-1 pb-3 pt-4">{children}</div>
				{footer && (
					<footer className="pb-1 text-xs" style={{ color: t.muted }}>
						{footer}
					</footer>
				)}
			</section>
		);
	}
	return (
		<section
			style={{
				background: toneC ? toneC.bg : t.panel,
				border: toneC ? `${t.border.split(' ')[0]} solid ${toneC.line}` : t.border,
				borderRadius: t.radius,
				boxShadow: highlighted ? `0 0 0 3px ${t.accentSoft}, ${t.shadow}` : t.shadow,
				color: t.text,
				minWidth: 0,
				...style,
			}}
			className="flex flex-col"
		>
			{(title || actions) && (
				<header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 px-4 pt-3">
					<div className="min-w-0" style={{ flex: '1 1 240px' }}>
						{title && (
							<h3 className="flex items-center gap-1.5 text-sm font-semibold leading-snug" style={{ fontFamily: t.heading }}>
								{Icon && <Icon size={14} style={{ color: t.muted, flexShrink: 0 }} />}
								{title}
							</h3>
						)}
						{description && (
							<p className="mt-0.5 text-xs leading-snug" style={{ color: t.muted }}>
								{description}
							</p>
						)}
					</div>
					{actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
				</header>
			)}
			<div className="min-w-0 flex-1 px-4 pb-3 pt-2">{children}</div>
			{footer && (
				<footer className="px-4 pb-3 text-xs" style={{ color: t.muted, borderTop: `1px solid ${t.line === '#383838' ? '#e9e2da' : t.line}`, paddingTop: 8 }}>
					{footer}
				</footer>
			)}
		</section>
	);
}

function Badge({ tone = 'neutral', children, title }: any) {
	const t = useTheme();
	const c = t.tones[tone] || t.tones.neutral;
	if (t.flat) {
		return (
			<span title={title} className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium tabular-nums" style={{ color: c.fg }}>
				{children}
			</span>
		);
	}
	return (
		<span
			title={title}
			className="inline-flex items-center gap-1 whitespace-nowrap px-1.5 py-0.5 text-xs font-medium"
			style={{ background: c.bg, color: c.fg, border: `1px solid ${c.line}`, borderRadius: Math.min(t.radius, 6) }}
		>
			{children}
		</span>
	);
}

function Button({ variant = 'outline', size = 'md', children, ...rest }: any) {
	const t = useTheme();
	const pad = size === 'sm' ? '3px 8px' : size === 'icon' ? '4px' : '7px 12px';
	const styles: Record<string, any> = t.flat
		? {
				primary: { background: '#111111', color: '#ffffff', border: '1px solid #111111' },
				outline: { background: 'transparent', color: t.text, border: '1px solid #111111' },
				ghost: { background: 'transparent', color: t.muted, border: '1px solid transparent' },
				soft: { background: 'transparent', color: t.text, border: `1px solid ${t.line}` },
			}
		: {
		primary: { background: t.name === 'Duck' ? '#6fc2ff' : t.accent, color: t.name === 'Duck' ? '#383838' : '#fff', border: t.name === 'Duck' ? t.border : `1px solid ${t.accent}` },
		outline: { background: t.panel, color: t.text, border: t.name === 'Duck' ? t.border : `1px solid ${t.line}` },
		ghost: { background: 'transparent', color: t.muted, border: '1px solid transparent' },
		soft: { background: t.accentSoft, color: t.text, border: `1px solid ${t.accentSoft}` },
			};
	return (
		<button
			{...rest}
			className={`inline-flex items-center justify-center gap-1.5 font-medium transition-opacity ${size === 'sm' || size === 'icon' ? 'text-xs' : 'text-sm'} ${rest.disabled ? 'opacity-50' : 'hover:opacity-80'}`}
			style={{ padding: pad, borderRadius: Math.min(t.radius, 8), cursor: rest.disabled ? 'default' : 'pointer', ...styles[variant], ...(rest.style || {}) }}
		>
			{children}
		</button>
	);
}

function Segmented({ options, value, onChange, size = 'sm' }: any) {
	const t = useTheme();
	if (t.flat) {
		return (
			<div className="inline-flex gap-3">
				{options.map((o: any) => {
					const active = o.value === value;
					return (
						<button
							key={o.value}
							title={o.title || o.label}
							onClick={() => onChange(o.value)}
							className={`inline-flex items-center gap-1 pb-0.5 ${size === 'sm' ? 'text-xs' : 'text-sm'}`}
							style={{ color: active ? t.text : t.muted, fontWeight: active ? 700 : 400, borderBottom: `2px solid ${active ? t.accent : 'transparent'}`, cursor: 'pointer' }}
						>
							{o.label}
						</button>
					);
				})}
			</div>
		);
	}
	return (
		<div className="inline-flex p-0.5" style={{ background: t.name === 'Night' ? '#0e1116' : t.bg, border: `1px solid ${t.name === 'Duck' ? '#383838' : t.line}`, borderRadius: Math.min(t.radius, 8) }}>
			{options.map((o: any) => {
				const active = o.value === value;
				return (
					<button
						key={o.value}
						title={o.title || o.label}
						onClick={() => onChange(o.value)}
						className={`inline-flex items-center gap-1 ${size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm'}`}
						style={{
							background: active ? t.panel : 'transparent',
							color: active ? t.text : t.muted,
							borderRadius: Math.min(t.radius, 6),
							boxShadow: active && t.name !== 'Duck' ? '0 1px 2px rgba(0,0,0,0.08)' : 'none',
							fontWeight: active ? 600 : 500,
							cursor: 'pointer',
						}}
					>
						{o.icon}
						{o.label}
					</button>
				);
			})}
		</div>
	);
}

function Skeleton({ h = 16, w = '100%', className = '' }: any) {
	const t = useTheme();
	return <div className={`animate-pulse ${className}`} style={{ height: h, width: w, background: t.grid, borderRadius: Math.min(t.radius, 6) }} />;
}

function Delta({ value, prior, measure, label, good: goodDir }: any) {
	const v = N(value);
	const p = N(prior);
	if (v == null || p == null || p === 0) return null;
	const change = (v - p) / Math.abs(p);
	const flat = Math.abs(change) < 0.005;
	const lowerIsBetter = goodDir ? goodDir === 'down' : DOWN_IS_GOOD.test(String(measure));
	const good = lowerIsBetter ? change < 0 : change > 0;
	const tone = flat ? 'neutral' : good ? 'positive' : 'critical';
	const Icon = flat ? Minus : change > 0 ? ArrowUpRight : ArrowDownRight;
	return (
		<Badge tone={tone} title={label}>
			<Icon size={12} />
			{(change > 0 ? '+' : '') + (change * 100).toFixed(Math.abs(change) < 0.1 ? 1 : 0)}%
		</Badge>
	);
}

function SparkTip({ active, payload, format, grain }: any) {
	const t = useTheme();
	if (!active || !payload || !payload.length) return null;
	const d = payload[0].payload;
	return (
		<div className="px-2 py-1 text-xs" style={{ background: t.panel, color: t.text, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: Math.min(t.radius, 6), boxShadow: '0 4px 14px rgba(0,0,0,0.12)', whiteSpace: 'nowrap' }}>
			<span style={{ color: t.muted }}>{d.x ? `${grain === 'month' ? '' : 'Week of '}${fmtPeriod(d.x, grain)}` : `Point ${d.i + 1}`}</span>{' '}
			<span className="font-semibold tabular-nums">{fmt(d.v, format)}</span>
		</div>
	);
}

function Sparkline({ values, xs, format, grain, color, height = 34 }: any) {
	const t = useTheme();
	const data = (values || []).map((v: any, i: number) => ({ i, v: N(v), x: xs?.[i] }));
	if (data.length < 2) return null;
	return (
		<ResponsiveContainer width="100%" height={height}>
			<LineChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
				<YAxis hide domain={['dataMin', 'dataMax']} />
				<Tooltip content={<SparkTip format={format} grain={grain} />} cursor={{ stroke: t.muted, strokeWidth: 1 }} allowEscapeViewBox={{ x: true, y: true }} wrapperStyle={{ zIndex: 20 }} />
				<Line type="linear" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} activeDot={{ r: 3, fill: t.flat ? t.accent : color, stroke: t.panel }} isAnimationActive={false} />
			</LineChart>
		</ResponsiveContainer>
	);
}

function ChartTooltip({ active, payload, label, series, grain }: any) {
	const t = useTheme();
	if (!active || !payload || !payload.length) return null;
	return (
		<div className="px-2.5 py-2 text-xs" style={{ background: t.panel, color: t.text, border: t.border, borderRadius: Math.min(t.radius, 6), boxShadow: '0 4px 14px rgba(0,0,0,0.12)' }}>
			<div className="mb-1 font-semibold">{grain ? fmtPeriod(label, grain) : humanize(label)}</div>
			{payload.map((p: any) => {
				const s = (series || []).find((x: any) => x.key === p.dataKey) || {};
				return (
					<div key={p.dataKey} className="flex items-center justify-between gap-4">
						<span className="flex items-center gap-1.5" style={{ color: t.muted }}>
							<span style={{ width: 8, height: 8, borderRadius: 2, background: p.color || p.stroke || p.fill, display: 'inline-block' }} />
							{humanize(s.label || p.dataKey)}
						</span>
						<span className="font-medium tabular-nums">{fmt(p.value, s.format)}</span>
					</div>
				);
			})}
		</div>
	);
}

function Legend({ series, colors }: any) {
	const t = useTheme();
	if (!series || series.length < 2) return null;
	return (
		<div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-xs" style={{ color: t.muted }}>
			{series.map((s: any, i: number) => (
				<span key={s.key} className="inline-flex items-center gap-1.5">
					{t.dashes && !s.muted ? (
						<svg width="18" height="8" style={{ display: 'inline-block' }}>
							<line x1="0" y1="4" x2="18" y2="4" stroke={colors[i]} strokeWidth="2" strokeDasharray={t.dashes[i]} />
						</svg>
					) : (
						<span style={{ width: 10, height: s.muted ? 2 : 10, borderRadius: 2, background: colors[i], display: 'inline-block', borderTop: s.muted ? `2px dashed ${colors[i]}` : undefined }} />
					)}
					{humanize(s.label)}
				</span>
			))}
		</div>
	);
}

const seriesColors = (t: any, series: any[]) => {
	let k = 0;
	return series.map((s) => (s.muted ? t.mutedSeries : t.series[k++ % t.series.length]));
};

// Width of an element, for responsive spans
function useWidth(ref: any) {
	const [w, setW] = useState(1200);
	useEffect(() => {
		if (!ref.current || typeof ResizeObserver === 'undefined') return;
		const ro = new ResizeObserver((entries) => setW(entries[0].contentRect.width));
		ro.observe(ref.current);
		return () => ro.disconnect();
	}, [ref]);
	return w;
}

// @kit-end

// ---- Registry --------------------------------------------------------------------
// Every component registers itself: the built-ins at the end of their own file,
// custom components the same way in a file assembled after the kit.
//
//   registerComponent('funnel', {
//     label: 'Funnel',            // shown in the variant switcher
//     icon: Filter,               // lucide icon for the card title
//     render: FunnelBlock,        // ({ block, onJump?, nav? }) => JSX
//     variants: ['table'],        // other renderers that can show this block
//     accepts: (block) => true,   // can this renderer show that block's data?
//     chrome: true,               // wrap in a Card with title, caption, source
//   });

type ComponentEntry = { label: string; icon?: any; render: any; variants?: string[]; accepts?: (block: any) => boolean; chrome?: boolean };
const REGISTRY: Record<string, ComponentEntry> = {};

function registerComponent(type: string, entry: ComponentEntry) {
	REGISTRY[type] = { chrome: true, variants: [], ...entry };
}

// Renderers a viewer can switch this block to: its own type first, then each
// registered variant whose `accepts` takes the block's data.
const variantsFor = (block: any) => {
	const own = REGISTRY[block.type];
	if (!own) return [];
	return [block.type, ...(own.variants || [])].filter((v, i, all) => all.indexOf(v) === i && REGISTRY[v] && (v === block.type || !REGISTRY[v].accepts || REGISTRY[v].accepts!(block)));
};

const componentIcon = (type: string) => REGISTRY[type]?.icon || FileText;
const componentLabel = (type: string) => REGISTRY[type]?.label || type;

function BlockView({ block, variant, onJump, nav }: any) {
	const v = variant && variantsFor(block).includes(variant) ? variant : block.type;
	const entry = REGISTRY[v];
	if (!entry) {
		return <p className="text-sm">Unsupported component type “{String(block.type)}”.</p>;
	}
	const R = entry.render;
	return <R block={block} onJump={onJump} nav={nav} />;
}
