// ---------------------------------------------------------------------------
// MotherDuck Dive components: heatmap
// Two layouts, one colour scale (empty, then four steps up to the busiest cell):
//   calendar  { mode: 'calendar', series: [{ key, label, format }], data: [{ x: 'YYYY-MM-DD', [key]: n }] }
//             weeks as columns, Monday to Sunday as rows, month labels, Less-More legend
//   matrix    { mode: 'matrix', rows: [label], cols: [x], cells: [{ row, col, v }], format, x: { grain } }
//             a category against time (or any second category)
// A line block also renders as a heatmap: several series become matrix rows, a
// single daily series becomes a calendar.
// ---------------------------------------------------------------------------

// @imports
import { useRef, useState } from 'react';
import * as d3 from 'd3';
import { Grid3x3 } from 'lucide-react';
// @imports-end

const DAY_MS = 86400000;
const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const dayMs = (iso: string) => Date.parse(String(iso).slice(0, 10) + 'T00:00:00Z');
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// Normalise any accepted block into one of the two layouts.
function heatmapModel(block: any) {
	if (block.mode === 'matrix') return { mode: 'matrix', rows: block.rows || [], cols: block.cols || [], cells: block.cells || [], format: block.format, grain: block.x?.grain, label: block.label };
	const series = (block.series || []).filter((s: any) => !s.muted);
	const data = block.data || [];
	if (block.mode === 'calendar' || (series.length === 1 && block.x?.grain === 'day')) {
		const s = series[0] || { key: 'v' };
		return { mode: 'calendar', days: data.map((d: any) => ({ date: String(d.x).slice(0, 10), v: N(d[s.key]) ?? 0 })), format: s.format, label: s.label || block.label };
	}
	return {
		mode: 'matrix',
		rows: series.map((s: any) => s.label),
		cols: data.map((d: any) => String(d.x)),
		cells: data.flatMap((d: any) => series.map((s: any) => ({ row: s.label, col: String(d.x), v: N(d[s.key]) }))),
		format: series[0]?.format,
		grain: block.x?.grain,
		label: block.label,
	};
}

function useHeatScale(values: (number | null)[]) {
	const t = useTheme();
	const max = Math.max(0, ...values.map((v) => v ?? 0));
	const top = t.mono ? '#111111' : t.series[0];
	const steps = [0.22, 0.45, 0.7, 1].map((p) => d3.interpolateRgb(t.name === 'Night' ? '#1f2a37' : '#f0f0f0', top)(p));
	const level = (v: number | null) => (v == null || v <= 0 || max === 0 ? 0 : Math.min(4, Math.ceil((v / max) * 4)));
	const color = (v: number | null) => (level(v) === 0 ? t.grid : steps[level(v) - 1]);
	return { color, steps, max };
}

function HeatLegend({ steps }: any) {
	const t = useTheme();
	return (
		<div className="flex items-center gap-1 text-xs" style={{ color: t.muted }}>
			Less
			{[t.grid, ...steps].map((c: string) => (
				<span key={c} style={{ width: 10, height: 10, background: c, borderRadius: t.flat ? 0 : 2, display: 'inline-block' }} />
			))}
			More
		</div>
	);
}

function HeatTip({ tip }: any) {
	const t = useTheme();
	if (!tip) return null;
	return (
		<div
			className="pointer-events-none px-2 py-1 text-xs"
			style={{ position: 'absolute', left: tip.x, top: tip.y - 8, transform: 'translate(-50%, -100%)', background: t.panel, color: t.text, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: Math.min(t.radius, 6), boxShadow: '0 4px 14px rgba(0,0,0,0.12)', whiteSpace: 'nowrap', zIndex: 10, transition: 'left 80ms, top 80ms' }}
		>
			{tip.text}
		</div>
	);
}

function CalendarHeatmap({ days, format, label }: any) {
	const t = useTheme();
	const ref = useRef<HTMLDivElement>(null);
	const width = useWidth(ref);
	const scroller = useRef<HTMLDivElement>(null);
	const [tip, setTip] = useState<any>(null);
	const { color, steps } = useHeatScale(days.map((d: any) => d.v));
	if (!days.length) return <p className="text-sm" style={{ color: t.muted }}>No data for this range.</p>;
	const byDay: Record<string, number> = {};
	days.forEach((d: any) => (byDay[d.date] = d.v));
	const first = Math.min(...days.map((d: any) => dayMs(d.date)));
	const last = Math.max(...days.map((d: any) => dayMs(d.date)));
	const start = first - ((new Date(first).getUTCDay() + 6) % 7) * DAY_MS; // the Monday on or before
	const weeks = Math.ceil((last - start + DAY_MS) / (7 * DAY_MS));
	const gap = t.flat ? 2 : 3;
	// Cells widen to fill the card; height stops at 22px so a wide card doesn't grow tall.
	// Below 9px wide the calendar keeps 9px cells and scrolls instead.
	const cw = Math.max(9, (width - 34) / weeks - gap);
	const cell = Math.min(cw, 22);
	const total = days.reduce((s: number, d: any) => s + (d.v || 0), 0);
	const months: { col: number; label: string }[] = [];
	for (let w = 0; w < weeks; w++) {
		const m = new Date(start + (w * 7 + 6) * DAY_MS).getUTCMonth();
		const isNew = w === 0 || m !== new Date(start + (w * 7 - 1) * DAY_MS).getUTCMonth();
		const prev = months[months.length - 1];
		if (isNew && (!prev || (w - prev.col) * (cw + gap) >= 26)) months.push({ col: w, label: MONTHS[m] });
	}
	return (
		<div ref={ref} style={{ position: 'relative' }}>
			<div className="mb-2 flex flex-wrap items-baseline justify-between gap-2 text-xs" style={{ color: t.muted }}>
				<span>
					<span className="font-semibold tabular-nums" style={{ color: t.text }}>
						{fmt(total, format)}
					</span>{' '}
					{humanize(label || 'total').toLowerCase()} · {fmtPeriod(isoDay(first))} – {fmtPeriod(isoDay(last))}
				</span>
				<HeatLegend steps={steps} />
			</div>
			<div ref={scroller} style={{ overflowX: 'auto' }} onMouseLeave={() => setTip(null)}>
				<svg width={34 + weeks * (cw + gap)} height={16 + 7 * (cell + gap)} role="img" aria-label={`${humanize(label || '')} by day`}>
					{months.map((m) => (
						<text key={m.col} x={34 + m.col * (cw + gap)} y={10} fontSize={10} fill={t.muted}>
							{m.label}
						</text>
					))}
					{[0, 2, 4].map((r) => (
						<text key={r} x={0} y={16 + r * (cell + gap) + cell - 2} fontSize={10} fill={t.muted}>
							{WEEKDAYS[r]}
						</text>
					))}
					{Array.from({ length: weeks * 7 }, (_, i) => {
						const ms = start + i * DAY_MS;
						if (ms < first || ms > last) return null;
						const iso = isoDay(ms);
						const v = byDay[iso] ?? 0;
						const x = 34 + Math.floor(i / 7) * (cw + gap);
						const y = 16 + (i % 7) * (cell + gap);
						return (
							<rect
								key={iso}
								x={x}
								y={y}
								width={cw}
								height={cell}
								rx={t.flat ? 0 : 2}
								fill={color(v)}
								onMouseEnter={() => setTip({ x: x + cw / 2 - (scroller.current?.scrollLeft || 0), y: y + 22, text: `${WEEKDAYS[i % 7]} ${fmtPeriod(iso)}: ${fmt(v, format)}` })}
							/>
						);
					})}
				</svg>
			</div>
			<HeatTip tip={tip} />
		</div>
	);
}

function MatrixHeatmap({ rows, cols, cells, format, grain }: any) {
	const t = useTheme();
	const ref = useRef<HTMLDivElement>(null);
	const width = useWidth(ref);
	const [tip, setTip] = useState<any>(null);
	const val: Record<string, number | null> = {};
	cells.forEach((c: any) => (val[`${c.row}\u0000${c.col}`] = c.v));
	const { color, steps } = useHeatScale(cells.map((c: any) => c.v));
	const labelW = Math.min(150, 12 + 6.6 * Math.max(4, ...rows.map((r: string) => humanize(r).length)));
	const gap = t.flat ? 2 : 3;
	const cw = Math.max(8, (width - labelW) / Math.max(1, cols.length) - gap);
	const ch = 20;
	const every = Math.max(1, Math.ceil(46 / (cw + gap)));
	const colLabel = (c: string) => (/^\d{4}-\d{2}-\d{2}/.test(c) ? fmtPeriod(c, grain) : humanize(c));
	return (
		<div ref={ref} style={{ position: 'relative' }} onMouseLeave={() => setTip(null)}>
			<div className="mb-2 flex justify-end">
				<HeatLegend steps={steps} />
			</div>
			<svg width="100%" height={rows.length * (ch + gap) + 20} role="img" aria-label="Heatmap">
				{rows.map((r: string, ri: number) => (
					<g key={r}>
						<text x={0} y={ri * (ch + gap) + ch / 2 + 4} fontSize={11} fill={t.text}>
							{humanize(r).length > 22 ? humanize(r).slice(0, 21) + '…' : humanize(r)}
						</text>
						{cols.map((c: string, ci: number) => {
							const v = val[`${r}\u0000${c}`] ?? null;
							const x = labelW + ci * (cw + gap);
							const y = ri * (ch + gap);
							return <rect key={c} x={x} y={y} width={cw} height={ch} rx={t.flat ? 0 : 2} fill={color(v)} onMouseEnter={() => setTip({ x: x + cw / 2, y: y + 6, text: `${humanize(r)} · ${colLabel(c)}: ${fmt(v, format)}` })} />;
						})}
					</g>
				))}
				{cols.map((c: string, ci: number) =>
					ci % every === 0 ? (
						<text key={c} x={labelW + ci * (cw + gap)} y={rows.length * (ch + gap) + 14} fontSize={10} fill={t.muted}>
							{colLabel(c)}
						</text>
					) : null,
				)}
			</svg>
			<HeatTip tip={tip} />
		</div>
	);
}

function HeatmapBlock({ block }: any) {
	const m: any = heatmapModel(block);
	return m.mode === 'calendar' ? <CalendarHeatmap days={m.days} format={m.format} label={m.label} /> : <MatrixHeatmap rows={m.rows} cols={m.cols} cells={m.cells} format={m.format} grain={m.grain} />;
}

registerComponent('heatmap', {
	label: 'Heatmap',
	icon: Grid3x3,
	render: HeatmapBlock,
	variants: ['line', 'area', 'table'],
	// From a line block: several series (rows), or one daily series (calendar).
	accepts: (b: any) => b.mode === 'calendar' || b.mode === 'matrix' || (b.x?.kind === 'time' && ((b.series || []).filter((s: any) => !s.muted).length > 1 || b.x?.grain === 'day')),
});
