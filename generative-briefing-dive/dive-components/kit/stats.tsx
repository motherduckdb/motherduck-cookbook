// ---------------------------------------------------------------------------
// MotherDuck Dive components: stats
// Three ways to show headline numbers, all from the KPI data shape
//   { items: [{ key, label, value, prior?, format, good?, spark?, spark_x?, spark_grain?, min?, max?, context? }], compare? }
// so a viewer can switch a KPI row between them.
//   MetricCard  label, change chip, large count-up number, context line, sparkline
//   Stat        compact card: icon indicator, value, trend line, description
//   RadialGauge arc from min to max with the value, and a tick at the comparison
// Each is also usable on its own with plain props.
// ---------------------------------------------------------------------------

// @imports
import { useEffect, useRef, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
// @imports-end

const compareText = (compare?: string) => (compare === 'previous_year' ? 'vs same period last year' : compare === 'previous_period' ? 'vs previous period' : '');

// Counts up from zero once, on first render. Jumps straight to the value when the
// viewer prefers reduced motion.
function useCountUp(target: number | null, ms = 700) {
	const [v, setV] = useState<number | null>(target);
	const done = useRef(false);
	useEffect(() => {
		if (target == null || done.current) return setV(target);
		done.current = true;
		const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
		if (reduce) return setV(target);
		const t0 = performance.now();
		let raf = 0;
		const step = (now: number) => {
			const p = Math.min(1, (now - t0) / ms);
			setV(target * (1 - Math.pow(1 - p, 3)));
			if (p < 1) raf = requestAnimationFrame(step);
		};
		raf = requestAnimationFrame(step);
		return () => cancelAnimationFrame(raf);
	}, [target]);
	return v;
}

// Change as a signed percentage with a direction word for screen readers.
function trendOf(value: any, prior: any, good?: string) {
	const v = N(value);
	const p = N(prior);
	if (v == null || p == null || p === 0) return null;
	const change = (v - p) / Math.abs(p);
	const flat = Math.abs(change) < 0.005;
	const isGood = good === 'down' ? change < 0 : change > 0;
	return {
		change,
		text: (change > 0 ? '+' : '') + (change * 100).toFixed(Math.abs(change) < 0.1 ? 1 : 0) + '%',
		tone: flat ? 'neutral' : isGood ? 'positive' : 'critical',
		Icon: flat ? Minus : change > 0 ? ArrowUpRight : ArrowDownRight,
	};
}

// ---- MetricCard ----------------------------------------------------------------

function MetricCard({ label, value, format, prior, good, context, spark, sparkX, sparkGrain, icon }: any) {
	const t = useTheme();
	const shown = useCountUp(N(value));
	const tr = trendOf(value, prior, good);
	const tone = tr ? t.tones[tr.tone] : null;
	const Icon = icon;
	return (
		<article
			className="flex min-w-0 flex-col"
			style={{
				background: t.panel,
				border: t.flat ? 'none' : t.border,
				borderTop: t.flat ? `2px solid ${t.rule}` : undefined,
				borderRadius: t.flat ? 0 : t.radius,
				boxShadow: t.shadow,
				padding: t.flat ? '12px 0 0' : 16,
			}}
		>
			<div className="flex items-start justify-between gap-2">
				<span className="inline-flex min-w-0 items-center gap-1.5 text-sm font-medium" style={{ color: t.flat ? t.text : t.muted }}>
					{Icon && <Icon size={14} style={{ color: t.muted, flexShrink: 0 }} />}
					<span className="truncate">{label}</span>
				</span>
				{tr && (
					<span
						className="inline-flex shrink-0 items-center gap-0.5 text-xs font-semibold tabular-nums"
						style={{ color: tone.fg, background: t.flat ? 'transparent' : tone.bg, border: t.flat ? 'none' : `1px solid ${tone.line}`, borderRadius: 999, padding: t.flat ? 0 : '1px 7px' }}
						aria-label={`${tr.change > 0 ? 'up' : 'down'} ${tr.text}`}
					>
						<tr.Icon size={12} />
						{tr.text}
					</span>
				)}
			</div>
			<div className="mt-2 tabular-nums" style={{ fontFamily: t.heading, fontSize: t.flat ? 44 : 34, fontWeight: t.displayWeight ?? (t.flat ? 700 : 600), letterSpacing: t.displayTracking ?? (t.flat ? -1.4 : -0.6), lineHeight: 1.05 }}>
				{fmt(shown, format)}
			</div>
			{(context || prior != null) && (
				<div className="mt-1 text-xs" style={{ color: t.muted }}>
					{context || `${fmt(prior, format)} before`}
				</div>
			)}
			{spark && spark.length > 1 && (
				<div className="mt-auto pt-2">
					<Sparkline values={spark} xs={sparkX} format={format} grain={sparkGrain} color={t.mono ? '#111111' : t.series[0]} height={30} />
				</div>
			)}
		</article>
	);
}

function MetricCardsBlock({ block }: any) {
	const t = useTheme();
	const cmp = compareText(block.compare);
	return (
		<div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: t.flat ? 28 : 12 }}>
			{(block.items || []).map((it: any) => (
				<MetricCard
					key={it.key}
					label={it.label}
					value={it.value}
					format={it.format}
					prior={it.prior}
					good={it.good}
					context={it.context || (it.prior != null ? `${fmt(it.prior, it.format)} ${cmp}` : '')}
					spark={it.spark}
					sparkX={it.spark_x}
					sparkGrain={it.spark_grain}
					icon={metricIcon(it.key, it.format)}
				/>
			))}
		</div>
	);
}

// ---- Stat ------------------------------------------------------------------------

function Stat({ label, value, format, prior, good, description, icon, tone }: any) {
	const t = useTheme();
	const tr = trendOf(value, prior, good);
	const ind = t.tones[tone || (tr ? tr.tone : 'neutral')] || t.tones.neutral;
	const Icon = icon;
	return (
		<div className="min-w-0" style={{ background: t.flat ? 'transparent' : t.panel, border: t.flat ? 'none' : t.border, borderLeft: t.flat ? `1px solid ${t.line}` : undefined, borderRadius: t.flat ? 0 : t.radius, padding: t.flat ? '2px 0 2px 16px' : 14 }}>
			<div className="flex items-center justify-between gap-2">
				<span className="truncate text-xs font-medium" style={{ color: t.muted }}>
					{label}
				</span>
				{Icon && (
					<span className="inline-flex shrink-0 items-center justify-center" style={{ width: 24, height: 24, borderRadius: t.flat ? 0 : Math.min(t.radius, 6), background: t.flat ? 'transparent' : ind.bg, color: t.flat ? t.muted : ind.fg }}>
						<Icon size={14} />
					</span>
				)}
			</div>
			<div className="mt-1 text-2xl font-bold tabular-nums" style={{ fontFamily: t.heading, letterSpacing: -0.5 }}>
				{fmt(value, format)}
			</div>
			{tr && (
				<div className="mt-0.5 inline-flex items-center gap-1 text-xs" style={{ color: t.tones[tr.tone].fg }}>
					<tr.Icon size={12} />
					<span className="font-semibold tabular-nums">{tr.text}</span>
					<span style={{ color: t.muted }}>{description || ''}</span>
				</div>
			)}
			{!tr && description && (
				<div className="mt-0.5 text-xs" style={{ color: t.muted }}>
					{description}
				</div>
			)}
		</div>
	);
}

function StatsBlock({ block }: any) {
	const t = useTheme();
	const cmp = compareText(block.compare);
	return (
		<div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: t.flat ? 16 : 10 }}>
			{(block.items || []).map((it: any) => (
				<Stat key={it.key} label={it.label} value={it.value} format={it.format} prior={it.prior} good={it.good} description={cmp} icon={metricIcon(it.key, it.format)} />
			))}
		</div>
	);
}

// ---- Gauge -----------------------------------------------------------------------

const SWEEP = 240; // degrees, centred on the top

function arcPath(cx: number, cy: number, r: number, from: number, to: number) {
	const pt = (deg: number) => {
		const a = ((deg - 90) * Math.PI) / 180;
		return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
	};
	const [x1, y1] = pt(from);
	const [x2, y2] = pt(to);
	return `M ${x1} ${y1} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
}

// The gauge's maximum: an explicit max, else a round number above the larger of
// value and comparison (rates never above 100%).
function gaugeMax(it: any) {
	if (N(it.max) != null) return N(it.max)!;
	const top = Math.max(N(it.value) ?? 0, N(it.prior) ?? 0) * 1.3;
	const ticks = niceTicks(top || 1, 4);
	const m = ticks ? ticks[ticks.length - 1] : 1;
	return it.format === 'pct' ? Math.min(1, m) : m;
}

function RadialGauge({ value, min = 0, max = 1, prior, format, label, good, size = 168 }: any) {
	const t = useTheme();
	const v = N(value);
	const stroke = t.flat ? 10 : 12;
	const r = size / 2 - stroke;
	const cx = size / 2;
	const cy = size / 2;
	const frac = (x: number) => Math.max(0, Math.min(1, (x - min) / (max - min || 1)));
	const start = -SWEEP / 2;
	const end = start + SWEEP * (v == null ? 0 : frac(v));
	const p = N(prior);
	const pDeg = p == null ? null : start + SWEEP * frac(p);
	const tick = (deg: number) => {
		const a = ((deg - 90) * Math.PI) / 180;
		return { x1: cx + (r - stroke) * Math.cos(a), y1: cy + (r - stroke) * Math.sin(a), x2: cx + (r + stroke) * Math.cos(a), y2: cy + (r + stroke) * Math.sin(a) };
	};
	const tr = trendOf(value, prior, good);
	return (
		<div className="flex flex-col items-center" role="meter" aria-valuemin={min} aria-valuemax={max} aria-valuenow={v ?? undefined} aria-valuetext={fmt(v, format)} aria-label={label}>
			<svg width={size} height={size * 0.82} viewBox={`0 0 ${size} ${size * 0.82}`}>
				<path d={arcPath(cx, cy, r, start, start + SWEEP)} fill="none" stroke={t.grid} strokeWidth={stroke} strokeLinecap={t.flat ? 'butt' : 'round'} />
				{v != null && end > start && <path d={arcPath(cx, cy, r, start, end)} fill="none" stroke={t.mono ? t.text : t.series[0]} strokeWidth={stroke} strokeLinecap={t.flat ? 'butt' : 'round'} />}
				{pDeg != null && <line {...tick(pDeg)} stroke={t.flat ? t.accent : t.text} strokeWidth={2} />}
				<text x={cx} y={cy + 4} textAnchor="middle" fontSize={size / 6.2} fontWeight={700} fill={t.text} style={{ fontFamily: t.heading, letterSpacing: -0.8 }}>
					{fmt(v, format)}
				</text>
				{tr && (
					<text x={cx} y={cy + size / 7.5} textAnchor="middle" fontSize={11} fontWeight={600} fill={t.tones[tr.tone].fg}>
						{tr.text}
					</text>
				)}
				<text x={cx - r * 0.87} y={size * 0.8} textAnchor="middle" fontSize={10} fill={t.muted}>
					{fmt(min, format)}
				</text>
				<text x={cx + r * 0.87} y={size * 0.8} textAnchor="middle" fontSize={10} fill={t.muted}>
					{fmt(max, format)}
				</text>
			</svg>
			<div className="-mt-1 text-center text-sm font-medium" style={{ color: t.text, maxWidth: size + 20 }}>
				{label}
			</div>
			{p != null && (
				<div className="text-xs" style={{ color: t.muted }}>
					<span style={{ display: 'inline-block', width: 8, height: 2, background: t.flat ? t.accent : t.text, verticalAlign: 'middle', marginRight: 4 }} />
					{fmt(p, format)} before
				</div>
			)}
		</div>
	);
}

function GaugesBlock({ block }: any) {
	return (
		<div className="grid justify-items-center gap-6" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
			{(block.items || []).map((it: any) => (
				<RadialGauge key={it.key} value={it.value} min={N(it.min) ?? 0} max={gaugeMax(it)} prior={it.prior} format={it.format} label={it.label} good={it.good} />
			))}
		</div>
	);
}

registerComponent('metric', { label: 'Cards', icon: Gauge, render: MetricCardsBlock, variants: ['kpi', 'stat', 'gauge'] });
registerComponent('stat', { label: 'Compact', icon: Gauge, render: StatsBlock, variants: ['kpi', 'metric', 'gauge'] });
// Gauges need a bounded scale: rates, or items with an explicit max.
registerComponent('gauge', {
	label: 'Gauges',
	icon: Gauge,
	render: GaugesBlock,
	variants: ['kpi', 'metric', 'stat'],
	accepts: (b: any) => (b.items || []).length > 0 && (b.items || []).length <= 4 && (b.items || []).every((it: any) => it.format === 'pct' || N(it.max) != null),
});
