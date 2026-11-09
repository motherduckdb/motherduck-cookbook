// ---------------------------------------------------------------------------
// MotherDuck Dive components: timeline
// Events in time order: dot, connector, title, time and description. Items before
// `activeIndex` are completed, the active one is ringed, later ones are pending.
// Data: { items: [{ date, title, body?, tone? }], activeIndex? }
//   or a table block with `timeline: { date, title }` naming the columns to use;
//   every other column becomes the description.
// ---------------------------------------------------------------------------

// @imports
import { GitCommitVertical } from 'lucide-react';
// @imports-end

function timelineItems(block: any) {
	if (block.items) return block.items;
	const map = block.timeline || {};
	const rest = (block.series || []).filter((s: any) => s.key !== map.date && s.key !== map.title);
	return (block.data || [])
		.map((row: any) => ({
			date: String(row[map.date] ?? ''),
			title: humanize(String(row[map.title || 'x'] ?? row.x)),
			body: rest
				.map((s: any) => {
					const f = s.format === 'row' ? row._format : s.format;
					return `${humanize(s.label)} ${s.format === 'delta_pct' ? fmt(row[s.key], 'delta_pct') : fmt(row[s.key], f)}`;
				})
				.join(' · '),
			tone: row._good && N(row.change) != null ? ((row._good === 'down' ? N(row.change)! < 0 : N(row.change)! > 0) ? 'positive' : 'critical') : undefined,
		}))
		.sort((a: any, b: any) => (a.date < b.date ? -1 : 1));
}

function Timeline({ items, activeIndex }: any) {
	const t = useTheme();
	const active = activeIndex ?? items.length - 1;
	const dot = 14;
	return (
		<ol role="list" style={{ position: 'relative' }}>
			{items.map((it: any, i: number) => {
				const status = i < active ? 'completed' : i === active ? 'active' : 'pending';
				const tone = it.tone ? t.tones[it.tone] : null;
				const c = tone && t.flat && it.tone !== 'critical' ? t.text : tone ? tone.fg : t.text;
				return (
					<li key={i} role="listitem" data-status={status} className="flex gap-3" style={{ minHeight: 44 }}>
						<div className="flex flex-col items-center" style={{ width: dot }}>
							<span
								aria-hidden
								style={{
									width: dot,
									height: dot,
									marginTop: 3,
									flexShrink: 0,
									borderRadius: t.flat ? 0 : 999,
									background: status === 'pending' ? t.panel : c,
									border: `2px solid ${status === 'pending' ? t.line : c}`,
									boxShadow: status === 'active' ? `0 0 0 3px ${t.flat ? t.accentSoft : t.accentSoft}` : 'none',
								}}
							/>
							{i < items.length - 1 && <span aria-hidden style={{ width: 2, flex: 1, background: i < active ? (t.flat ? t.text : t.line) : t.line, opacity: i < active ? 1 : 0.6, margin: '2px 0' }} />}
						</div>
						<div className="min-w-0 pb-4">
							<div className="flex flex-wrap items-baseline gap-x-2">
								<span className="text-sm font-semibold" style={{ color: status === 'pending' ? t.muted : t.text }}>
									{it.title}
								</span>
								{it.date && (
									<time dateTime={it.date} className="text-xs tabular-nums" style={{ color: t.muted }}>
										{/^\d{4}-\d{2}-\d{2}/.test(it.date) ? fmtPeriod(it.date) : it.date}
									</time>
								)}
							</div>
							{it.body && (
								<p className="mt-0.5 text-sm leading-snug" style={{ color: t.muted }}>
									{it.body}
								</p>
							)}
						</div>
					</li>
				);
			})}
		</ol>
	);
}

function TimelineBlock({ block }: any) {
	const items = timelineItems(block);
	return <Timeline items={items} activeIndex={block.activeIndex} />;
}

registerComponent('timeline', { label: 'Timeline', icon: GitCommitVertical, render: TimelineBlock, variants: ['table'], accepts: (b: any) => Array.isArray(b.items) || !!b.timeline });
