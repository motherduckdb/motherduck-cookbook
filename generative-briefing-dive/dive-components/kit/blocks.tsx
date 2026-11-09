// ---------------------------------------------------------------------------
// MotherDuck Dive components: blocks
// The original spec blocks: KPI, line, bar, table, callouts, text, source footer.
// Contract: each block takes { block, onJump?, nav? } where block is one
// spec.blocks entry (see SPEC.md). Blocks never run SQL.
// ---------------------------------------------------------------------------

// @imports
import { Lightbulb, ThumbsDown, ThumbsUp } from 'lucide-react';
// @imports-end

// ---- Blocks -------------------------------------------------------------------

function KpiBlock({ block }: any) {
	const t = useTheme();
	const items = block.items || [];
	const cmp = block.compare === 'previous_year' ? 'vs same period last year' : block.compare === 'previous_period' ? 'vs previous period' : '';
	return (
		<div className="grid gap-y-4" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(170px, 1fr))`, columnGap: t.flat ? 32 : 24 }}>
			{items.map((it: any, i: number) => (
				<div key={it.key} className="min-w-0" style={t.flat && i > 0 ? { borderLeft: `1px solid ${t.line}`, paddingLeft: 20 } : undefined}>
					<div className={t.flat ? 'text-sm font-medium' : 'text-xs font-medium uppercase'} style={{ color: t.flat ? t.text : t.muted, letterSpacing: t.flat ? 0 : 0.5 }}>
						<span className="inline-flex items-center gap-1.5">
							{(() => {
								const MI = metricIcon(it.key, it.format);
								return <MI size={14} style={{ color: t.muted }} />;
							})()}
							{it.label}
						</span>
					</div>
					<div className={`${t.flat ? 'mt-2 flex-col items-start gap-1' : 'mt-1 items-baseline gap-2'} flex`}>
						<span className={`${t.flat ? 'text-5xl font-bold' : 'text-3xl font-semibold'} tabular-nums`} style={{ fontFamily: t.heading, letterSpacing: t.flat ? -1.5 : -0.5, lineHeight: 1.05 }}>
							{fmt(it.value, it.format)}
						</span>
						<Delta value={it.value} prior={it.prior} measure={it.key} good={it.good} label={cmp} />
					</div>
					{it.prior != null && (
						<div className="text-xs" style={{ color: t.muted }}>
							{fmt(it.prior, it.format)} {cmp}
						</div>
					)}
					<div className="mt-1">
						<Sparkline values={it.spark} xs={it.spark_x} format={it.format} grain={it.spark_grain} color={t.mono ? '#111111' : t.series[i % t.series.length]} />
					</div>
				</div>
			))}
		</div>
	);
}

function LineBlock({ block, height = 240 }: any) {
	const t = useTheme();
	const series = block.series || [];
	const colors = seriesColors(t, series);
	const grain = block.x?.grain;
	const data = (block.data || []).map((d: any) => {
		const row: any = { x: String(d.x) };
		series.forEach((s: any) => (row[s.key] = N(d[s.key])));
		return row;
	});
	const fmt0 = series[0]?.format;
	const keys = series.filter((s: any) => !s.muted).map((s: any) => s.key);
	// Each marker sits on the series it is about. Older specs have no `points`:
	// put the dot on the series that moves most at that period.
	const mover = (x: string) => {
		const i = data.findIndex((d: any) => d.x === x);
		const move = (k: string) => {
			const v = data[i]?.[k] ?? 0;
			const ref = [data[i - 1]?.[k], data[i + 1]?.[k]].filter((r) => r != null);
			return ref.length ? Math.max(...ref.map((r: number) => Math.abs(v - r))) : Math.abs(v);
		};
		return keys.reduce((best: string, k: string) => (move(k) > move(best) ? k : best), keys[0]);
	};
	const points: { x: string; series: string }[] = block.points || [...new Set([...(block.highlight || []), ...(block.annotations || []).map((a: any) => a.x)])].map((x: any) => ({ x, series: mover(x) }));
	const nonNeg = data.every((d: any) => keys.every((k: string) => d[k] == null || d[k] >= 0));
	const ticks = nonNeg ? niceTicks(Math.max(0, ...data.flatMap((d: any) => series.map((s: any) => d[s.key] ?? 0)))) : undefined;
	return (
		<div>
			<Legend series={series} colors={colors} />
			<ResponsiveContainer width="100%" height={height}>
				<LineChart data={data} margin={{ top: (block.annotations || []).length ? 24 : 8, right: 16, bottom: 0, left: 0 }}>
					<CartesianGrid strokeDasharray="3 3" stroke={t.grid} vertical={false} />
					<XAxis dataKey="x" tickFormatter={(v) => fmtPeriod(v, grain)} fontSize={11} stroke={t.muted} tickLine={false} axisLine={{ stroke: t.grid }} minTickGap={28} />
					<YAxis tickFormatter={axisFmt(fmt0)} fontSize={11} stroke={t.muted} tickLine={false} axisLine={false} width={48} domain={ticks ? [0, ticks[ticks.length - 1]] : ['auto', 'auto']} ticks={ticks} />
					<Tooltip content={<ChartTooltip series={series} grain={grain} />} />
					{(() => {
						const xs = data.map((d: any) => d.x);
						let last = -99;
						return [...(block.annotations || [])]
							.sort((a: any, b: any) => xs.indexOf(a.x) - xs.indexOf(b.x))
							.map((a: any) => {
								const i = xs.indexOf(a.x);
								const show = i - last >= Math.max(3, Math.round(xs.length / 8));
								if (show) last = i;
								return (
									<ReferenceLine
										key={a.x}
										x={a.x}
										stroke={t.highlight}
										strokeDasharray="4 3"
										strokeWidth={1.5}
										label={show ? { value: a.label, position: 'top', fill: t.text, fontSize: 11, fontWeight: 600, textAnchor: i > xs.length * 0.8 ? 'end' : i < xs.length * 0.2 ? 'start' : 'middle' } : undefined}
									/>
								);
							});
					})()}
					{series.map((s: any, i: number) => (
						<Line key={s.key} type="linear" dataKey={s.key} stroke={colors[i]} strokeWidth={s.muted ? 1.5 : 2} strokeDasharray={s.muted ? '4 3' : t.dashes?.[i]} dot={false} isAnimationActive={false} connectNulls />
					))}
					{points.map((p) => {
						const d = data.find((r: any) => r.x === p.x);
						const k = keys.includes(p.series) ? p.series : keys[0];
						if (!d || d[k] == null) return null;
						return <ReferenceDot key={`${p.x}-${k}`} x={p.x} y={d[k]} r={5} fill={t.highlight} stroke={t.panel} strokeWidth={2} />;
					})}
				</LineChart>
			</ResponsiveContainer>
		</div>
	);
}

function BarBlock({ block }: any) {
	const t = useTheme();
	const series = block.series || [];
	const colors = seriesColors(t, series);
	const grain = block.x?.kind === 'time' ? block.x.grain : null;
	const data = (block.data || []).map((d: any) => {
		const row: any = { x: String(d.x) };
		series.forEach((s: any) => (row[s.key] = N(d[s.key])));
		return row;
	});
	const hl = new Set(block.highlight || []);
	const longLabels = !grain && data.some((d: any) => d.x.length > 9);
	const horizontal = longLabels || (!grain && data.length > 7);
	const fmt0 = series[0]?.format;
	const height = horizontal ? Math.max(180, data.length * (series.length > 1 ? 30 : 24) + 30) : 240;
	const label = (v: string) => (grain ? fmtPeriod(v, grain) : humanize(v));
	return (
		<div>
			<Legend series={series} colors={colors} />
			<ResponsiveContainer width="100%" height={height}>
				<BarChart data={data} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ top: 6, right: 12, bottom: 0, left: 0 }} barGap={2}>
					<CartesianGrid strokeDasharray="3 3" stroke={t.grid} vertical={horizontal} horizontal={!horizontal} />
					{horizontal ? (
						<>
							<XAxis type="number" tickFormatter={axisFmt(fmt0)} fontSize={11} stroke={t.muted} tickLine={false} axisLine={false} />
							<YAxis type="category" dataKey="x" tickFormatter={label} fontSize={11} stroke={t.muted} tickLine={false} axisLine={false} width={Math.min(190, 16 + 7.4 * Math.max(...data.map((d: any) => label(d.x).length), 4))} />
						</>
					) : (
						<>
							<XAxis dataKey="x" tickFormatter={label} fontSize={11} stroke={t.muted} tickLine={false} axisLine={{ stroke: t.grid }} interval={grain ? 'preserveStartEnd' : 0} minTickGap={grain ? 28 : 4} />
							<YAxis tickFormatter={axisFmt(fmt0)} fontSize={11} stroke={t.muted} tickLine={false} axisLine={false} width={48} />
						</>
					)}
					<Tooltip cursor={{ fill: t.accentSoft, opacity: 0.5 }} content={<ChartTooltip series={series} grain={grain} />} />
					{series.map((s: any, i: number) => (
						<Bar key={s.key} dataKey={s.key} fill={colors[i]} radius={t.name === 'Duck' || t.flat ? 0 : horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0]} isAnimationActive={false} stroke={t.name === 'Duck' ? '#383838' : undefined} strokeWidth={t.name === 'Duck' ? 1 : 0}>
							{data.map((d: any) => (
								<Cell key={d.x} fill={s.muted ? colors[i] : hl.size === 0 ? colors[i] : hl.has(d.x) ? t.highlight : colors[i]} fillOpacity={hl.size && !hl.has(d.x) && !s.muted ? 0.45 : 1} />
							))}
						</Bar>
					))}
				</BarChart>
			</ResponsiveContainer>
		</div>
	);
}

function TableBlock({ block, maxRows = 12 }: any) {
	const t = useTheme();
	const [expanded, setExpanded] = useState(false);
	const series = block.series || [];
	const hl = new Set(block.highlight || []);
	const rows = block.data || [];
	const shown = expanded ? rows : rows.slice(0, maxRows);
	const grain = block.x?.kind === 'time' ? block.x.grain : null;
	const priorOf = (key: string) => series.find((s: any) => s.key === key + '_prior');
	const cols = series.filter((s: any) => !s.key.endsWith('_prior'));
	const cell = (row: any, s: any) => {
		const f = s.format === 'row' ? row._format : s.format;
		if (s.format === 'delta_pct') {
			const v = N(row[s.key]);
			if (v == null) return '—';
			const lowerIsBetter = row._good ? row._good === 'down' : DOWN_IS_GOOD.test(String(row.x).toLowerCase().replace(/ /g, '_'));
			const good = lowerIsBetter ? v < 0 : v > 0;
			return <Badge tone={Math.abs(v) < 0.02 ? 'neutral' : good ? 'positive' : 'critical'}>{fmt(v, 'delta_pct')}</Badge>;
		}
		if (s.format === 'text') return <span style={{ color: t.muted }}>{fmtPeriod(row[s.key])}</span>;
		const p = priorOf(s.key);
		return (
			<span className="inline-flex items-center justify-end gap-1.5">
				<span>{fmt(row[s.key], f)}</span>
				{p && <Delta value={row[s.key]} prior={row[p.key]} measure={s.key} good={s.good} label={`${p.label}: ${fmt(row[p.key], f)}`} />}
			</span>
		);
	};
	const tot = block.total;
	return (
		<div className="overflow-x-auto">
			{tot && (
				<div className="mb-2 flex flex-wrap items-baseline gap-x-2 text-sm">
					<span style={{ color: t.muted }}>{tot.label}</span>
					<span className="tabular-nums" style={{ color: t.muted }}>{fmt(tot.before, tot.format)}</span>
					<span style={{ color: t.muted }}>→</span>
					<span className="font-bold tabular-nums">{fmt(tot.now, tot.format)}</span>
					<Delta value={tot.now} prior={tot.before} good={block.data?.[0]?._good} />
				</div>
			)}
			<table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
				<thead>
					<tr style={{ color: t.muted }}>
						<th className="py-1.5 pr-3 text-left text-xs font-medium uppercase" style={{ letterSpacing: 0.4, paddingLeft: t.flat ? 0 : 6 }}>
							{humanize(block.x?.label || '')}
						</th>
						{cols.map((s: any) => (
							<th key={s.key} className="py-1.5 pl-3 text-right text-xs font-medium uppercase" style={{ letterSpacing: 0.4 }}>
								{humanize(s.label)}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{shown.map((row: any) => {
						const on = hl.has(String(row.x));
						return (
							<tr key={row.x} style={{ borderTop: `1px solid ${t.name === 'Duck' ? '#e9e2da' : t.line}`, background: on && !t.flat ? t.accentSoft : 'transparent', fontWeight: on && t.flat ? 700 : undefined }}>
								<td className="py-1.5 pr-3 font-medium" style={{ borderLeft: t.flat ? 'none' : on ? `3px solid ${t.highlight}` : '3px solid transparent', paddingLeft: t.flat ? 0 : 6 }}>
									{t.flat && <span style={{ display: 'inline-block', width: 6, height: 6, background: on ? t.accent : 'transparent', marginRight: 8, verticalAlign: 'middle' }} />}
									{grain ? fmtPeriod(row.x, grain) : humanize(row.x)}
								</td>
								{cols.map((s: any) => (
									<td key={s.key} className="py-1.5 pl-3 text-right tabular-nums">
										{cell(row, s)}
									</td>
								))}
							</tr>
						);
					})}
				</tbody>
			</table>
			{rows.length > maxRows && (
				<Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)} style={{ marginTop: 4 }}>
					{expanded ? 'Show fewer' : `Show all ${rows.length}`}
				</Button>
			)}
		</div>
	);
}

const TONE_ICON: Record<string, any> = { critical: AlertOctagon, warning: AlertTriangle, positive: CheckCircle2, info: Info };

function InvestigateButton({ nav, item }: any) {
	const t = useTheme();
	if (!nav?.investigate || !item?.question) return null;
	const existing = nav.existing?.(item.question);
	const running = nav.running?.(item.question);
	return (
		<button
			className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold"
			title={item.question}
			disabled={!existing && nav.busy}
			onClick={() => (existing ? nav.open(existing) : nav.investigate(item))}
			style={{
				cursor: !existing && nav.busy ? 'default' : 'pointer',
				opacity: !existing && nav.busy && !running ? 0.45 : 1,
				color: t.flat ? '#ffffff' : t.name === 'Duck' ? '#383838' : '#ffffff',
				background: t.mono ? '#111111' : t.name === 'Duck' ? '#6fc2ff' : t.accent,
				border: t.name === 'Duck' ? '2px solid #383838' : 'none',
				borderRadius: Math.min(t.radius, 6),
				padding: '4px 9px',
			}}
		>
			{running ? <Loader2 size={12} className="animate-spin" /> : existing ? <ArrowRight size={12} /> : <SearchCheck size={12} />}
			{running ? 'Investigating…' : existing ? 'Open investigation' : 'Investigate'}
		</button>
	);
}

// How an insight relates to earlier ones: new, ongoing since a date, worsening, improving, resolved.
function Continuity({ item }: any) {
	const t = useTheme();
	const c = item?.continuity;
	if (!c) return null;
	const since = item.first_seen && c !== 'new' && c !== 'resolved' ? ` since ${fmtPeriod(item.first_seen)}` : '';
	const nth = item.times_seen > 1 && c !== 'resolved' ? ` · ${item.times_seen} briefings` : '';
	const tone = c === 'worsening' ? 'critical' : c === 'improving' || c === 'resolved' ? 'positive' : 'neutral';
	const label = { new: 'New', ongoing: 'Ongoing', worsening: 'Worsening', improving: 'Improving', resolved: 'Resolved' }[c as string] || c;
	return (
		<span className="inline-flex items-center gap-1 text-xs" style={{ color: t.tones[tone].fg, fontWeight: c === 'new' ? 600 : 500 }} title={item.refers_to ? `Continues: ${item.refers_to}` : undefined}>
			{c === 'new' && <span style={{ width: 6, height: 6, borderRadius: t.flat ? 0 : 999, background: t.accent, display: 'inline-block' }} />}
			{label}
			{since}
			{nth}
		</span>
	);
}

// Thumbs up/down. nav.rate(kind, target, value) sends it; nav.rating(kind, target) reads it back.
function RateButtons({ nav, kind, target, label }: any) {
	const t = useTheme();
	if (!nav?.rate) return null;
	const cur = nav.rating?.(kind, target);
	const btn = (v: 'up' | 'down', Icon: any) => (
		<button
			onClick={() => nav.rate(kind, target, cur === v ? '' : v)}
			title={v === 'up' ? 'Useful' : 'Not useful'}
			aria-pressed={cur === v}
			style={{ cursor: 'pointer', color: cur === v ? (v === 'up' ? t.tones.positive.fg : t.tones.critical.fg) : t.muted, padding: 2 }}
		>
			<Icon size={13} fill={cur === v ? 'currentColor' : 'none'} />
		</button>
	);
	return (
		<span className="inline-flex items-center gap-0.5 text-xs" style={{ color: t.muted }}>
			{label && <span className="mr-1">{label}</span>}
			{btn('up', ThumbsUp)}
			{btn('down', ThumbsDown)}
		</span>
	);
}

// The "so what" line a narrator can add under any block.
function BlockInsight({ block }: any) {
	const t = useTheme();
	if (!block?.insight) return null;
	return (
		<p className="mt-2 flex gap-1.5 text-sm leading-snug" style={{ color: t.text }}>
			<Lightbulb size={14} style={{ color: t.mono ? t.text : t.accent, flexShrink: 0, marginTop: 2 }} />
			<span>{block.insight}</span>
		</p>
	);
}

function Callout({ tone = 'info', title, children, onJump, item, nav }: any) {
	const t = useTheme();
	const c = t.tones[tone] || t.tones.info;
	const Icon = TONE_ICON[tone] || Info;
	if (t.flat) {
		return (
			<div className="pt-3" style={{ borderTop: `${tone === 'critical' ? 3 : 1}px solid ${c.line}`, color: t.text }}>
				<div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
					<span className="flex items-center gap-1.5 text-xs font-medium uppercase" style={{ color: c.fg, letterSpacing: 0.8 }}>
						<Icon size={13} />
						{tone === 'critical' ? 'Act now' : tone === 'warning' ? 'Watch' : tone === 'positive' ? 'Going well' : 'Note'}
					</span>
					<Continuity item={item} />
				</div>
				<div className="mt-1 text-base font-bold leading-snug" style={{ fontFamily: t.heading }}>
					{title}
				</div>
				<div className="mt-1 text-sm leading-relaxed" style={{ color: '#3a3a3a' }}>
					{children}
				</div>
				<div className="flex flex-wrap items-center gap-3">
					<InvestigateButton nav={nav} item={item} />
					{onJump && (
						<button className="mt-2 inline-flex items-center gap-1 text-xs font-medium" style={{ color: t.text, cursor: 'pointer', borderBottom: `1px solid ${t.text}` }} onClick={onJump}>
							See evidence
						</button>
					)}
					<span className="mt-2 ml-auto">
						<RateButtons nav={nav} kind="insight" target={title} />
					</span>
				</div>
			</div>
		);
	}
	return (
		<div className="flex gap-2.5 p-3" style={{ background: c.bg, border: `${t.name === 'Duck' ? 2 : 1}px solid ${c.line}`, borderRadius: t.radius, color: t.text, boxShadow: t.name === 'Duck' ? '-3px 3px 0 #383838' : 'none' }}>
			<Icon size={18} style={{ color: c.fg, flexShrink: 0, marginTop: 1 }} />
			<div className="min-w-0">
				<div className="flex flex-wrap items-baseline justify-between gap-x-3">
					<span className="text-sm font-semibold" style={{ color: t.name === 'Duck' ? t.text : c.fg }}>
						{title}
					</span>
					<Continuity item={item} />
				</div>
				<div className="mt-0.5 text-sm leading-snug">{children}</div>
				<div className="flex flex-wrap items-center gap-3">
					<InvestigateButton nav={nav} item={item} />
					{onJump && (
						<button className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium" style={{ color: t.muted, cursor: 'pointer' }} onClick={onJump}>
							See evidence <ChevronRight size={12} />
						</button>
					)}
					<span className="mt-1.5 ml-auto">
						<RateButtons nav={nav} kind="insight" target={title} />
					</span>
				</div>
			</div>
		</div>
	);
}

function CalloutsBlock({ block, onJump, nav }: any) {
	const t = useTheme();
	const items = block.items || [];
	return (
		<div>
			{t.flat && block.title && (
				<div className="mb-3 text-xs font-bold uppercase" style={{ letterSpacing: 1, color: t.muted }}>
					{block.title}
				</div>
			)}
		<div className="grid" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${items.length > 3 ? 220 : 240}px, 1fr))`, gap: t.flat ? 32 : 12 }}>
			{items.map((c: any, i: number) => (
				<Callout key={i} tone={c.tone} title={c.title} item={c} nav={nav} onJump={c.block_id && onJump ? () => onJump(c.block_id) : null}>
					{c.body}
				</Callout>
			))}
		</div>
		</div>
	);
}

function TextBlock({ block }: any) {
	const t = useTheme();
	return <p className="text-sm leading-relaxed" style={{ color: t.text }}>{block.body}</p>;
}

// "About" under a block: definitions, why it is on the page, and where the numbers come from.
function SourceFooter({ block }: any) {
	const t = useTheme();
	const [open, setOpen] = useState(false);
	const s = block.source;
	if (!s) return null;
	const about = block.about || {};
	const ds = s.dataset || (s.datasets || []).join(', ');
	const filters = (s.filters || []).map((f: any) => `${f.dimension} = ${f.values.join(', ')}`).join('; ');
	return (
		<div>
			<button className="inline-flex items-center gap-1" style={{ color: t.muted, cursor: 'pointer' }} onClick={() => setOpen(!open)}>
				<Database size={12} /> {about.definitions?.length ? 'About' : 'Source'} {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
			</button>
			{block.probe && (
				<span className="ml-3 inline-flex items-center gap-1" style={{ color: t.muted }} title="The analyst step ran this query to test a hypothesis">
					<SearchCheck size={12} /> Follow-up query
				</span>
			)}
			{open && (
				<div className="mt-2 space-y-1.5">
					{(about.definitions || []).map((d: any) => (
						<p key={d.term} style={{ color: t.text }}>
							<span className="font-semibold">{d.term}</span>
							<span style={{ color: t.muted }}> · {d.text}</span>
						</p>
					))}
					{block.hypothesis && <p style={{ color: t.muted }}>Tests: {block.hypothesis}</p>}
					<p style={{ color: t.muted }}>
						Source: {ds} · {(s.measures || []).map((m: string) => humanize(m.split('.').pop() || m)).join(', ')}
						{s.dimension && s.dimension !== 'time' ? ` by ${humanize(s.dimension)}` : ''} · {humanize(s.window || '')}
						{filters ? ` · ${filters}` : ''}
					</p>
					{block.why && !block.hypothesis && <p style={{ color: t.muted }}>Why it is here: {block.why}</p>}
					{(s.sql || []).map((q: string, i: number) => (
						<pre key={i} className="overflow-x-auto p-2 text-xs" style={{ background: t.bg === '#ffffff' ? '#f7f7f7' : t.bg, border: `1px solid ${t.name === 'Duck' ? '#e9e2da' : t.line}`, borderRadius: Math.min(t.radius, 6), whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: t.text }}>
							{q}
						</pre>
					))}
				</div>
			)}
		</div>
	);
}


// ---- Register --------------------------------------------------------------------

// A table or chart can only become a bar chart when every series shares one unit.
const oneUnit = (block: any) => {
	const fmts = new Set((block.series || []).map((s: any) => s.format));
	return fmts.size === 1 && !fmts.has('row') && !fmts.has('text') && !fmts.has('delta_pct');
};

registerComponent('kpi', { label: 'Stats', icon: Gauge, render: KpiBlock, variants: ['metric', 'stat', 'gauge'] });
registerComponent('line', { label: 'Line', icon: TrendingUp, render: LineBlock, variants: ['area', 'bar', 'heatmap', 'table'] });
registerComponent('bar', { label: 'Bar', icon: BarChart3, render: BarBlock, variants: ['treemap', 'table'], accepts: oneUnit });
registerComponent('table', { label: 'Table', icon: Table2, render: TableBlock, variants: ['bar', 'treemap', 'timeline'] });
registerComponent('callouts', { label: 'Callouts', icon: AlertTriangle, render: CalloutsBlock, chrome: false });
registerComponent('text', { label: 'Text', icon: FileText, render: TextBlock });
