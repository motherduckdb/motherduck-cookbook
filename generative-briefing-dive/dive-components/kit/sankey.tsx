// ---------------------------------------------------------------------------
// MotherDuck Dive components: sankey
// Flows from one set of categories to another (channel → section, topic → outcome).
// Data: { nodes: [{ name, side: 'source' | 'target' }], links: [{ source, target, value }],
//         format, source_label?, target_label? }
// where source/target are node indexes. Blocks also carry the flows as rows
// ({ x: 'A → B', value }) with series [{ key: 'value', ... }] so a viewer can
// switch to a bar chart or a table.
// ---------------------------------------------------------------------------

// @imports
import { Sankey } from 'recharts';
import { Waypoints } from 'lucide-react';
// @imports-end

function SankeyBlock({ block, height }: any) {
	const t = useTheme();
	const nodes = block.nodes || [];
	const links = (block.links || []).filter((l: any) => (N(l.value) ?? 0) > 0).map((l: any) => ({ ...l, value: N(l.value) }));
	if (!nodes.length || !links.length) return <p className="text-sm" style={{ color: t.muted }}>No flows to show.</p>;
	const sources = nodes.map((n: any, i: number) => (n.side === 'source' ? i : -1)).filter((i: number) => i >= 0);
	const colorOf = (i: number) => {
		const k = sources.indexOf(i);
		if (t.mono) return k === 0 ? '#111111' : ['#5c5c5c', '#9a9a9a', '#c4c4c4', '#2f2f2f', '#7a7a7a'][(k - 1) % 5];
		return t.series[(k < 0 ? 0 : k) % t.series.length];
	};
	const hl = new Set(block.highlight || []);
	const total = links.reduce((s: number, l: any) => s + l.value, 0);
	const h = height || Math.max(240, Math.min(420, 34 * Math.max(sources.length, nodes.length - sources.length) + 40));
	const Node = (p: any) => {
		const { x, y, width, height: nh, index, payload } = p;
		const isSource = nodes[index]?.side === 'source';
		const on = hl.has(payload.name);
		return (
			<g>
				<rect x={x} y={y} width={width} height={Math.max(1, nh)} fill={isSource ? colorOf(index) : t.mono ? '#111111' : t.text} fillOpacity={isSource ? 1 : 0.85} />
				<text x={isSource ? x - 6 : x + width + 6} y={y + nh / 2} textAnchor={isSource ? 'end' : 'start'} dominantBaseline="middle" fontSize={11} fill={on ? t.accent : t.text} fontWeight={on ? 700 : 500}>
					{humanize(payload.name)}
					<tspan fill={t.muted} fontWeight={400}>
						{'  '}
						{fmt(payload.value, block.format)}
					</tspan>
				</text>
			</g>
		);
	};
	const Link = (p: any) => {
		const { sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload } = p;
		const si = nodes.findIndex((n: any) => n === payload.source || n.name === payload.source?.name);
		return (
			<path
				d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`}
				fill="none"
				stroke={colorOf(si)}
				strokeOpacity={t.flat ? 0.22 : 0.3}
				strokeWidth={Math.max(1, linkWidth)}
				onMouseEnter={(e: any) => (e.currentTarget.style.strokeOpacity = '0.55')}
				onMouseLeave={(e: any) => (e.currentTarget.style.strokeOpacity = t.flat ? '0.22' : '0.3')}
			/>
		);
	};
	const labelW = Math.min(170, 30 + 6.4 * Math.max(...nodes.map((n: any) => humanize(n.name).length + fmt(1000, block.format).length)));
	return (
		<div>
			{(block.source_label || block.target_label) && (
				<div className="mb-1 flex justify-between text-xs font-medium uppercase" style={{ color: t.muted, letterSpacing: 0.5 }}>
					<span>{humanize(block.source_label || '')}</span>
					<span>{humanize(block.target_label || '')}</span>
				</div>
			)}
			<ResponsiveContainer width="100%" height={h}>
				<Sankey data={{ nodes: nodes.map((n: any) => ({ name: n.name })), links }} node={<Node />} link={<Link />} nodePadding={14} nodeWidth={t.flat ? 6 : 10} margin={{ top: 8, bottom: 8, left: labelW, right: labelW }} iterations={32}>
					<Tooltip
						content={({ active, payload }: any) => {
							if (!active || !payload?.length) return null;
							const d = payload[0].payload?.payload || payload[0].payload;
							const isLink = d?.source && d?.target;
							const v = N(d?.value) ?? 0;
							return (
								<div className="px-2.5 py-1.5 text-xs" style={{ background: t.panel, color: t.text, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: Math.min(t.radius, 6), boxShadow: '0 4px 14px rgba(0,0,0,0.12)' }}>
									<div className="font-semibold">{isLink ? `${humanize(d.source.name)} → ${humanize(d.target.name)}` : humanize(d?.name)}</div>
									<div className="tabular-nums" style={{ color: t.muted }}>
										{fmt(v, block.format)} · {total ? ((v / total) * 100).toFixed(1) : 0}% of all
									</div>
								</div>
							);
						}}
					/>
				</Sankey>
			</ResponsiveContainer>
		</div>
	);
}

registerComponent('sankey', { label: 'Flow', icon: Waypoints, render: SankeyBlock, variants: ['bar', 'table'], accepts: (b: any) => Array.isArray(b.nodes) && Array.isArray(b.links) });
