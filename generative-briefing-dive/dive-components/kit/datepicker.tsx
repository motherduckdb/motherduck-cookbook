// ---------------------------------------------------------------------------
// MotherDuck Dive components: date picker
// An input, not a spec block. A button that opens a month calendar with presets.
//   <DatePicker mode="single" value="2026-11-08" onChange={(iso) => …} max="2026-11-08" />
//   <DatePicker mode="range" value={{ from, to }} onChange={({ from, to }) => …}
//               presets={[{ label: 'Last 28 days', days: 28 }]} max={latest} />
// Dates are ISO strings ('YYYY-MM-DD'). Keep the value in useDiveState so it
// travels with the link, and use it in SQL as a quoted literal or ?::DATE.
// ---------------------------------------------------------------------------

// @imports
import { useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
// @imports-end

const D_MS = 86400000;
const toMs = (iso: string) => Date.parse(iso + 'T00:00:00Z');
const toIso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const addDays = (iso: string, n: number) => toIso(toMs(iso) + n * D_MS);
const DEFAULT_PRESETS = [
	{ label: 'Last 7 days', days: 7 },
	{ label: 'Last 28 days', days: 28 },
	{ label: 'Last 90 days', days: 90 },
	{ label: 'Last 12 months', days: 365 },
];

function DatePicker({ mode = 'single', value, onChange, min, max, presets, label, align = 'left', disabled }: any) {
	const t = useTheme();
	const today = max || toIso(Date.now());
	const [open, setOpen] = useState(false);
	const cur = mode === 'range' ? value?.to || today : value || today;
	const [month, setMonth] = useState(cur.slice(0, 7));
	const [draft, setDraft] = useState<any>(null); // range: first click
	const inRange = (iso: string) => (!min || iso >= min) && (!max || iso <= max);
	const y = Number(month.slice(0, 4));
	const m = Number(month.slice(5, 7)) - 1;
	const first = Date.UTC(y, m, 1);
	const lead = (new Date(first).getUTCDay() + 6) % 7;
	const cells = Array.from({ length: 42 }, (_, i) => toIso(first + (i - lead) * D_MS));
	const shift = (k: number) => setMonth(toIso(Date.UTC(y, m + k, 1)).slice(0, 7));
	const from = mode === 'range' ? draft?.from || value?.from : null;
	const to = mode === 'range' ? (draft ? null : value?.to) : null;
	const pick = (iso: string) => {
		if (!inRange(iso)) return;
		if (mode === 'single') {
			onChange(iso);
			setOpen(false);
		} else if (!draft) setDraft({ from: iso });
		else {
			const [a, b] = [draft.from, iso].sort();
			onChange({ from: a, to: b });
			setDraft(null);
			setOpen(false);
		}
	};
	const text =
		mode === 'range'
			? value?.from
				? `${fmtPeriod(value.from)} – ${fmtPeriod(value.to)}`
				: 'Pick dates'
			: value
				? fmtPeriod(value)
				: 'Pick a date';
	const hover = t.name === 'Night' ? '#1c222b' : '#f3f3f3';
	return (
		<div style={{ position: 'relative', display: 'inline-block' }}>
			<Button variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(!open)} aria-haspopup="dialog" aria-expanded={open}>
				<CalendarDays size={13} />
				{label && <span style={{ color: t.muted }}>{label}</span>}
				<span className="tabular-nums">{text}</span>
			</Button>
			{open && (
				<>
					<div onClick={() => (setOpen(false), setDraft(null))} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
					<div
						role="dialog"
						aria-label="Choose a date"
						className="flex gap-3 p-3"
						style={{ position: 'absolute', [align]: 0, top: 'calc(100% + 6px)', zIndex: 31, background: t.panel, color: t.text, border: t.flat ? `1px solid ${t.rule}` : t.border, borderRadius: t.radius, boxShadow: '0 12px 32px rgba(0,0,0,0.14)' }}
					>
						{mode === 'range' && (
							<div className="flex flex-col gap-0.5 pr-3" style={{ borderRight: `1px solid ${t.line}`, minWidth: 120 }}>
								{(presets || DEFAULT_PRESETS).map((p: any) => (
									<button
										key={p.label}
										className="px-2 py-1 text-left text-xs"
										style={{ cursor: 'pointer', borderRadius: Math.min(t.radius, 6), color: t.text }}
										onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = hover)}
										onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = 'transparent')}
										onClick={() => {
											onChange({ from: addDays(today, -(p.days - 1)), to: today });
											setOpen(false);
										}}
									>
										{p.label}
									</button>
								))}
							</div>
						)}
						<div style={{ width: 238 }}>
							<div className="mb-2 flex items-center justify-between">
								<Button variant="ghost" size="icon" onClick={() => shift(-1)} title="Previous month">
									<ChevronLeft size={14} />
								</Button>
								<span className="text-sm font-semibold">
									{MONTHS[m]} {y}
								</span>
								<Button variant="ghost" size="icon" onClick={() => shift(1)} title="Next month">
									<ChevronRight size={14} />
								</Button>
							</div>
							<div className="grid text-center text-xs" style={{ gridTemplateColumns: 'repeat(7, 1fr)', gap: 2 }}>
								{['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((d) => (
									<span key={d} style={{ color: t.muted, padding: '2px 0' }}>
										{d}
									</span>
								))}
								{cells.map((iso) => {
									const inMonth = iso.slice(0, 7) === month;
									const ok = inRange(iso);
									const sel = mode === 'single' ? iso === value : iso === from || iso === to;
									const between = mode === 'range' && from && to && iso > from && iso < to;
									return (
										<button
											key={iso}
											disabled={!ok}
											onClick={() => pick(iso)}
											className="tabular-nums"
											style={{
												height: 30,
												cursor: ok ? 'pointer' : 'default',
												borderRadius: t.flat ? 0 : Math.min(t.radius, 6),
												background: sel ? (t.mono ? '#111111' : t.accent) : between ? t.accentSoft : 'transparent',
												color: sel ? '#ffffff' : !ok ? t.line : inMonth ? t.text : t.muted,
												fontWeight: iso === today ? 700 : 400,
												textDecoration: iso === today && !sel ? 'underline' : 'none',
												opacity: inMonth ? 1 : 0.55,
											}}
										>
											{Number(iso.slice(8))}
										</button>
									);
								})}
							</div>
							{mode === 'range' && draft && (
								<p className="mt-2 text-xs" style={{ color: t.muted }}>
									From {fmtPeriod(draft.from)}: pick the end date.
								</p>
							)}
						</div>
					</div>
				</>
			)}
		</div>
	);
}
