// ---------------------------------------------------------------------------
// MotherDuck Dive components: treemap
// Part-to-whole of one additive measure. Squarified with d3; labels only where
// they fit; text never scales. Branches take a series colour, leaves shade by
// size. Click a branch to zoom in; the breadcrumb zooms back out.
// Data: { tree: { name, children: [{ name, value } | { name, children }] }, format }
//   or the bar shape { series: [{ key, format }], data: [{ x, [key]: n }] } (one level)
// ---------------------------------------------------------------------------

// @imports
import { useRef, useState } from 'react';
import * as d3 from 'd3';
import { LayoutDashboard } from 'lucide-react';
// @imports-end

function treeOf(block: any) {
	if (block.tree) return block.tree;
	const s = (block.series || []).find((x: any) => !x.muted && !String(x.key).endsWith('_prior')) || {};
	return { name: block.title || 'All', children: (block.data || []).map((d: any) => ({ name: String(d.x), value: Math.max(0, N(d[s.key]) ?? 0) })) };
}

function TreemapBlock({ block, height = 320 }: any) {
	const t = useTheme();
	const ref = useRef<HTMLDivElement>(null);
	const width = useWidth(ref);
	const [path, setPath] = useState<string[]>([]);
	const [tip, setTip] = useState<any>(null);
	const format = block.format || (block.series || [])[0]?.format;
	const hl = new Set(block.highlight || []);
	let focus = treeOf(block);
	for (const name of path) focus = (focus.children || []).find((c: any) => c.name === name) || focus;
	const root = d3
		.hierarchy(focus)
		.sum((d: any) => (d.children ? 0 : Math.max(0, N(d.value) ?? 0)))
		.sort((a: any, b: any) => (b.value || 0) - (a.value || 0));
	const total = root.value || 0;
	const nested = (focus.children || []).some((c: any) => c.children);
	d3.treemap().size([Math.max(10, width), height]).paddingInner(t.flat ? 2 : 3).paddingTop(nested ? 20 : 0).round(true).tile(d3.treemapSquarify.ratio(1.3))(root as any);
	const branches = root.children || [];
	const fill = (node: any) => {
		const top = node.depth <= 1 ? node : node.ancestors().find((a: any) => a.depth === 1);
		const bi = branches.indexOf(top);
		const base = t.mono ? (hl.has(node.data.name) ? t.accent : ['#111111', '#4a4a4a', '#767676', '#9a9a9a', '#bdbdbd'][Math.min(4, bi)]) : t.series[bi % t.series.length];
		if (!nested) {
			const share = total ? (node.value || 0) / total : 0;
			return t.mono ? (hl.has(node.data.name) ? t.accent : d3.interpolateRgb('#c8c8c8', '#111111')(Math.min(1, share * 3))) : d3.interpolateRgb('#ffffff', t.series[0])(0.35 + Math.min(0.65, share * 2.5));
		}
		return base;
	};
	const leaves = nested ? root.descendants().filter((n: any) => n.depth >= 1) : root.leaves();
	if (!total) return <p className="text-sm" style={{ color: t.muted }}>No data yet.</p>;
	return (
		<div>
			<div className="mb-2 flex flex-wrap items-baseline gap-x-2 text-xs" style={{ color: t.muted }}>
				{path.length > 0 && (
					<>
						<button onClick={() => setPath([])} style={{ cursor: 'pointer', color: t.text, borderBottom: `1px solid ${t.muted}` }}>
							All
						</button>
						{path.map((p, i) => (
							<span key={p}>
								›{' '}
								<button onClick={() => setPath(path.slice(0, i + 1))} style={{ cursor: 'pointer', color: t.text }}>
									{humanize(p)}
								</button>
							</span>
						))}
						<span>·</span>
					</>
				)}
				<span>
					Total <span className="font-semibold tabular-nums" style={{ color: t.text }}>{fmt(total, format)}</span>
				</span>
			</div>
			<div ref={ref} style={{ position: 'relative', height }} onMouseLeave={() => setTip(null)}>
				{leaves.map((n: any) => {
					const w = n.x1 - n.x0;
					const h = n.y1 - n.y0;
					const isBranch = !!n.children;
					const c = fill(n);
					const dark = d3.hsl(c).l < 0.6;
					const fg = dark ? '#ffffff' : '#111111';
					const share = total ? (n.value / total) * 100 : 0;
					return (
						<div
							key={n.ancestors().map((a: any) => a.data.name).join('/')}
							onClick={() => isBranch && n.data.children && setPath([...path, n.data.name])}
							onMouseMove={(e: any) => {
								const r = ref.current?.getBoundingClientRect();
								if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, text: `${humanize(n.data.name)}: ${fmt(n.value, format)} · ${share.toFixed(1)}%` });
							}}
							style={{
								position: 'absolute',
								left: n.x0,
								top: n.y0,
								width: w,
								height: h,
								background: isBranch ? 'transparent' : c,
								border: isBranch ? `1px solid ${t.line}` : 'none',
								borderRadius: t.flat ? 0 : Math.min(t.radius, 4),
								color: isBranch ? t.text : fg,
								overflow: 'hidden',
								cursor: isBranch ? 'zoom-in' : 'default',
								padding: isBranch ? '3px 6px' : '5px 7px',
								boxSizing: 'border-box',
							}}
						>
							{(isBranch ? w > 50 : w > 56 && h > 26) && (
								<div className="truncate text-xs font-semibold" style={{ lineHeight: 1.25 }}>
									{humanize(n.data.name)}
								</div>
							)}
							{!isBranch && w > 56 && h > 42 && (
								<div className="truncate text-xs tabular-nums" style={{ opacity: 0.8 }}>
									{fmt(n.value, format)} · {share.toFixed(0)}%
								</div>
							)}
						</div>
					);
				})}
				{tip && (
					<div className="pointer-events-none px-2 py-1 text-xs" style={{ position: 'absolute', left: Math.min(tip.x + 12, width - 180), top: tip.y + 12, background: t.panel, color: t.text, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: Math.min(t.radius, 6), boxShadow: '0 4px 14px rgba(0,0,0,0.12)', whiteSpace: 'nowrap', zIndex: 10 }}>
						{tip.text}
					</div>
				)}
			</div>
		</div>
	);
}

// A treemap needs one additive, non-negative measure.
registerComponent('treemap', {
	label: 'Treemap',
	icon: LayoutDashboard,
	render: TreemapBlock,
	variants: ['bar', 'table'],
	accepts: (b: any) => {
		if (b.tree) return true;
		const s = (b.series || []).filter((x: any) => !x.muted && !String(x.key).endsWith('_prior'));
		return s.length === 1 && ['count', 'currency'].includes(s[0].format) && b.x?.kind !== 'time' && (b.data || []).every((d: any) => (N(d[s[0].key]) ?? 0) >= 0);
	},
});
