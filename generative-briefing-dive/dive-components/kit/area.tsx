// ---------------------------------------------------------------------------
// MotherDuck Dive components: area
// A line chart with a headline: the latest value and its change, legend toggles
// that hide series, a gradient fill under the first series, and a crosshair.
// Data: the line block shape
//   { x: { kind: 'time', grain }, series: [{ key, label, format, muted? }], data: [{ x, [key]: number }] }
// ---------------------------------------------------------------------------

// @imports
import { useState } from 'react';
import { Area, ComposedChart } from 'recharts';
// @imports-end

function AreaBlock({ block, height = 220 }: any) {
	const t = useTheme();
	const series = block.series || [];
	const colors = seriesColors(t, series);
	const [hidden, setHidden] = useState<string[]>([]);
	const grain = block.x?.grain;
	const data = (block.data || []).map((d: any) => {
		const row: any = { x: String(d.x) };
		series.forEach((s: any) => (row[s.key] = N(d[s.key])));
		return row;
	});
	const visible = series.filter((s: any) => !hidden.includes(s.key));
	const lead = series.find((s: any) => !s.muted) || series[0];
	const fmt0 = lead?.format;
	const vals = data.map((d: any) => d[lead?.key]).filter((v: any) => v != null);
	const last = vals[vals.length - 1];
	const prev = vals[vals.length - 2];
	const nonNeg = data.every((d: any) => visible.every((s: any) => d[s.key] == null || d[s.key] >= 0));
	const ticks = nonNeg ? niceTicks(Math.max(0, ...data.flatMap((d: any) => visible.map((s: any) => d[s.key] ?? 0)))) : undefined;
	const gid = `area-${String(block.id || 'x').replace(/[^a-z0-9]/gi, '')}`;
	const per = grain === 'month' ? 'previous month' : grain === 'day' ? 'previous day' : 'previous week';
	return (
		<div>
			{lead && last != null && (
				<div className="mb-2 flex flex-wrap items-baseline gap-x-2">
					<span className="text-2xl font-bold tabular-nums" style={{ fontFamily: t.heading, letterSpacing: -0.6 }}>
						{fmt(last, fmt0)}
					</span>
					<Delta value={last} prior={prev} good={lead.good} label={`vs ${per}`} />
					<span className="text-xs" style={{ color: t.muted }}>
						{series.length > 1 ? `${humanize(lead.label)}, ` : ''}latest {grain || 'period'} vs {per}
					</span>
				</div>
			)}
			{series.length > 1 && (
				<div className="mb-1 flex flex-wrap gap-1.5">
					{series.map((s: any, i: number) => {
						const off = hidden.includes(s.key);
						return (
							<button
								key={s.key}
								onClick={() => setHidden(off ? hidden.filter((h) => h !== s.key) : visible.length > 1 ? [...hidden, s.key] : hidden)}
								aria-pressed={!off}
								className="inline-flex items-center gap-1.5 text-xs"
								style={{ cursor: 'pointer', color: off ? t.muted : t.text, opacity: off ? 0.55 : 1, padding: '2px 8px', border: `1px solid ${t.flat ? (off ? t.line : t.rule) : t.line}`, borderRadius: t.flat ? 0 : 999, background: 'transparent' }}
							>
								<svg width="14" height="8">
									<line x1="0" y1="4" x2="14" y2="4" stroke={colors[i]} strokeWidth="2" strokeDasharray={s.muted ? '3 2' : t.dashes?.[i]} />
								</svg>
								{humanize(s.label)}
							</button>
						);
					})}
				</div>
			)}
			<ResponsiveContainer width="100%" height={height}>
				<ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
					<defs>
						<linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
							<stop offset="0%" stopColor={colors[series.indexOf(lead)]} stopOpacity={t.flat ? 0.16 : 0.28} />
							<stop offset="100%" stopColor={colors[series.indexOf(lead)]} stopOpacity={0} />
						</linearGradient>
					</defs>
					<CartesianGrid strokeDasharray="3 3" stroke={t.grid} vertical={false} />
					<XAxis dataKey="x" tickFormatter={(v) => fmtPeriod(v, grain)} fontSize={11} stroke={t.muted} tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={40} />
					<YAxis tickFormatter={axisFmt(fmt0)} fontSize={11} stroke={t.muted} tickLine={false} axisLine={false} width={48} domain={ticks ? [0, ticks[ticks.length - 1]] : ['auto', 'auto']} ticks={ticks} />
					<Tooltip cursor={{ stroke: t.muted, strokeDasharray: '3 3' }} content={<ChartTooltip series={series} grain={grain} />} />
					{series.map((s: any, i: number) =>
						hidden.includes(s.key) ? null : s === lead ? (
							<Area key={s.key} type="monotone" dataKey={s.key} stroke={colors[i]} strokeWidth={2} fill={`url(#${gid})`} dot={false} activeDot={{ r: 4, fill: colors[i], stroke: t.panel, strokeWidth: 2 }} isAnimationActive={false} connectNulls />
						) : (
							<Line key={s.key} type="monotone" dataKey={s.key} stroke={colors[i]} strokeWidth={s.muted ? 1.5 : 1.75} strokeDasharray={s.muted ? '4 3' : t.dashes?.[i]} dot={false} activeDot={{ r: 3 }} isAnimationActive={false} connectNulls />
						),
					)}
				</ComposedChart>
			</ResponsiveContainer>
		</div>
	);
}

registerComponent('area', { label: 'Area', icon: TrendingUp, render: AreaBlock, variants: ['line', 'bar', 'heatmap', 'table'], accepts: (b: any) => b.x?.kind === 'time' });
