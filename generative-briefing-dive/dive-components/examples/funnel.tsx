// ---------------------------------------------------------------------------
// Example custom component: funnel
// How to add a component the kit doesn't have. Copy this file, then:
//   1. render from the block only, with theme tokens from useTheme(), never SQL;
//   2. convert values with N() and format with fmt();
//   3. registerComponent() with the renderers a viewer may switch to;
//   4. assemble it after the kit: python3 assemble.py app.tsx -o out.tsx --extra funnel.tsx
//   5. for a malleable Dive, add an entry to the recipe's components and, if the
//      Flight should plan it, a compiler in domain.py (see SKILL.md).
// Data: the bar shape with one count series, in funnel order:
//   { series: [{ key: 'v', label, format: 'count' }], data: [{ x: 'Visited docs', v: 12480 }, …] }
// ---------------------------------------------------------------------------

// @imports
import { Filter } from 'lucide-react';
// @imports-end

function FunnelBlock({ block }: any) {
	const t = useTheme();
	const s = (block.series || [])[0] || {};
	const steps = (block.data || []).map((d: any) => ({ label: String(d.x), v: N(d[s.key]) ?? 0 }));
	const top = steps[0]?.v || 1;
	return (
		<div className="space-y-1.5">
			{steps.map((st: any, i: number) => {
				const w = Math.max(2, (st.v / top) * 100);
				const step = i > 0 && steps[i - 1].v ? st.v / steps[i - 1].v : null;
				return (
					<div key={st.label} className="grid items-center gap-3" style={{ gridTemplateColumns: 'minmax(110px, 30%) 1fr 64px' }}>
						<span className="truncate text-sm">{st.label}</span>
						<div style={{ background: t.grid, height: 22, borderRadius: t.flat ? 0 : Math.min(t.radius, 4) }}>
							<div style={{ width: `${w}%`, height: '100%', background: i === steps.length - 1 && t.mono ? t.accent : t.mono ? '#111111' : t.series[0], borderRadius: t.flat ? 0 : Math.min(t.radius, 4) }} />
						</div>
						<span className="text-right text-xs tabular-nums">
							<span className="font-semibold">{fmt(st.v, s.format)}</span>
							{step != null && <span style={{ color: t.muted }}> {(step * 100).toFixed(step < 0.1 ? 1 : 0)}%</span>}
						</span>
					</div>
				);
			})}
		</div>
	);
}

registerComponent('funnel', {
	label: 'Funnel',
	icon: Filter,
	render: FunnelBlock,
	variants: ['bar', 'table'],
	accepts: (b: any) => (b.series || []).length === 1 && b.series[0].format === 'count',
});
