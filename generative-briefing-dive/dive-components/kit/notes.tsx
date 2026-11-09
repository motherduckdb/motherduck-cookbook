// ---------------------------------------------------------------------------
// MotherDuck Dive components: viewer notes and questions
// A generated page can ask its viewers about what it cannot explain from the data:
// a date ("what happened in the week of 2 Nov?") or a topic (one referrer, page or
// borough). Answers become notes: { kind: date|topic, x, grain?, dimension?, value?,
// category, note }. withNotes() pins them to every block that shows that date or value,
// as a chart marker on line charts and as a note line under any block.
// Data: questions block { items: [{ id, kind, x, grain?, dimension?, value?, label,
//   question, options: [string] }] }; nav.annotate(note) saves, nav.noteFor(item) reads.
// ---------------------------------------------------------------------------

// @imports
import { useState } from 'react';
import { Check, MessageSquare, Pin } from 'lucide-react';
// @imports-end

const NOTE_CATEGORIES: { value: string; label: string }[] = [
	{ value: 'event', label: 'Event' },
	{ value: 'release', label: 'Release' },
	{ value: 'campaign', label: 'Campaign' },
	{ value: 'data_issue', label: 'Data issue' },
	{ value: 'expected', label: 'Expected' },
	{ value: 'noise', label: 'Not important' },
	{ value: 'other', label: 'Other or not sure' },
];
const CATEGORY_LABEL: Record<string, string> = Object.fromEntries(NOTE_CATEGORIES.map((c) => [c.value, c.label]));

// The period of a time series that contains a day: the last x at or before it.
function periodFor(xs: string[], day: string) {
	let hit: string | null = null;
	for (const x of [...xs].sort()) if (x.slice(0, 10) <= day) hit = x;
	if (!hit) return null;
	const sorted = [...xs].sort();
	const next = sorted[sorted.indexOf(hit) + 1];
	return next && next.slice(0, 10) <= day ? null : hit;
}

// Attach the notes that apply to a block: dates on time axes, values on category axes.
function withNotes(block: any, notes: any[]) {
	if (!notes?.length || !block?.data?.length) return block;
	const xs = block.data.map((d: any) => String(d.x));
	const time = block.x?.kind === 'time';
	const dim = block.source?.dimension || '';
	const hits: any[] = [];
	for (const n of notes) {
		if (n.kind === 'date' && time) {
			const x = periodFor(xs, String(n.x).slice(0, 10));
			if (x) hits.push({ ...n, at: x });
		} else if (n.kind === 'topic') {
			const row = block.data.find((d: any) => (dim && dim === n.dimension && String(d.x) === n.value) || (d.value != null && String(d.value) === n.value));
			if (row) hits.push({ ...n, at: String(row.x) });
		}
	}
	if (!hits.length) return block;
	const out: any = { ...block, notes: hits };
	if (block.type === 'line' || block.type === 'area') {
		const short = (text: string) => (text.length <= 24 ? text : `${text.slice(0, 24).replace(/[\s,:;.-]+\S*$/, '')}…`);
		const marks = hits.map((h) => ({ x: h.at, label: short(h.note || CATEGORY_LABEL[h.category] || 'Note'), series: '', viewer: true }));
		const own = (block.annotations || []).filter((a: any) => !marks.some((m) => m.x === a.x));
		out.annotations = [...own, ...marks];
	}
	return out;
}

// The notes under a block: what viewers said about a date or a value it shows.
function NotesLine({ block }: any) {
	const t = useTheme();
	const notes = block?.notes || [];
	if (!notes.length) return null;
	return (
		<div className="mt-2 space-y-1">
			{notes.slice(0, 4).map((n: any, i: number) => (
				<p key={i} className="flex gap-1.5 text-xs leading-snug" style={{ color: t.muted }}>
					<Pin size={12} style={{ color: t.mono ? t.text : t.accent, flexShrink: 0, marginTop: 1 }} />
					<span>
						<span style={{ color: t.text, fontWeight: 600 }}>{n.kind === 'date' ? fmtPeriod(n.at, block.x?.grain) : n.value}</span>
						{' · '}
						{CATEGORY_LABEL[n.category] || 'Note'}
						{n.note ? `: ${n.note}` : ''}
					</span>
				</p>
			))}
		</div>
	);
}

// Category and free text for one note, built from the kit's form fields (field.tsx).
// targets: [{ value, label }] when the viewer picks the date or value; options: likely explanations.
function NoteForm({ targets, targetLabel = 'When', initial, options, onSave, onCancel, saveLabel = 'Save note' }: any) {
	const [target, setTarget] = useState(initial?.target ?? targets?.[0]?.value ?? '');
	const [category, setCategory] = useState(initial?.category || '');
	const [note, setNote] = useState(initial?.note || '');
	const [tried, setTried] = useState(false);
	const missing = !category && !note.trim();
	const save = () => {
		setTried(true);
		if (!missing) onSave({ target, category: category || 'other', note: note.trim().slice(0, 240) });
	};
	return (
		<div className="mt-3">
			<FieldGroup gap={14}>
				{options?.length > 0 && (
					<FieldSet>
						<FieldLegend variant="label">Likely explanation</FieldLegend>
						<ChoiceGroup layout="chips" options={options} value={options.includes(note) ? note : ''} onChange={(v: string) => setNote(v)} />
					</FieldSet>
				)}
				<div className="flex flex-wrap items-end gap-3">
					{targets?.length > 0 && (
						<Field>
							<FieldLabel>{targetLabel}</FieldLabel>
							<Select value={target} options={targets} onChange={(e: any) => setTarget(e.target.value)} style={{ maxWidth: 220 }} />
						</Field>
					)}
					<Field>
						<FieldLabel>Kind of note</FieldLabel>
						<Select value={category} options={NOTE_CATEGORIES} placeholder="Choose…" onChange={(e: any) => setCategory(e.target.value)} />
					</Field>
					<Field invalid={tried && missing} style={{ flex: '1 1 260px' }}>
						<FieldLabel>What happened?</FieldLabel>
						<Input value={note} invalid={tried && missing} placeholder="e.g. launch post, tracking outage" onChange={(e: any) => setNote(e.target.value)} onKeyDown={(e: any) => e.key === 'Enter' && save()} />
					</Field>
				</div>
				{tried && missing ? (
					<FieldError>Pick a kind of note or say what happened.</FieldError>
				) : (
					<FieldDescription>The note appears on every chart that shows this date or value, and later briefings use it.</FieldDescription>
				)}
				<Field orientation="horizontal">
					<Button size="sm" variant="primary" onClick={save}>
						{saveLabel}
					</Button>
					{onCancel && (
						<Button size="sm" variant="ghost" onClick={onCancel}>
							Cancel
						</Button>
					)}
				</Field>
			</FieldGroup>
		</div>
	);
}

function QuestionItem({ item, nav }: any) {
	const t = useTheme();
	const existing = nav?.noteFor?.(item);
	const [editing, setEditing] = useState(false);
	return (
		<div className="py-3" style={{ borderTop: `1px solid ${t.flat ? t.rule : t.line}` }}>
			<div className="flex gap-2">
				<MessageSquare size={15} style={{ color: t.mono ? t.text : t.accent, flexShrink: 0, marginTop: 2 }} />
				<div className="min-w-0 flex-1">
					<div className="text-sm font-semibold leading-snug">{item.question}</div>
					<div className="mt-0.5 text-xs" style={{ color: t.muted }}>
						About {item.label}
					</div>
					{existing && !editing ? (
						<p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" style={{ color: t.text }}>
							<Check size={13} style={{ color: t.tones.positive.fg }} />
							<span>
								{CATEGORY_LABEL[existing.category] || 'Note'}
								{existing.note ? `: ${existing.note}` : ''}
							</span>
							<span style={{ color: t.muted }}>{existing.pending ? '· saving, the next briefing will use it' : '· the briefing uses this'}</span>
							{nav?.annotate && (
								<button className="ml-1" style={{ color: t.muted, borderBottom: `1px solid ${t.muted}`, cursor: 'pointer' }} onClick={() => setEditing(true)}>
									Change
								</button>
							)}
						</p>
					) : nav?.annotate ? (
						<NoteForm
							options={item.options}
							initial={existing}
							saveLabel="Answer"
							onCancel={editing ? () => setEditing(false) : null}
							onSave={(v: any) => {
								nav.annotate({ kind: item.kind, x: item.x, grain: item.grain, dimension: item.dimension, value: item.value, category: v.category, note: v.note, question: item.question });
								setEditing(false);
							}}
						/>
					) : (
						<p className="mt-1 text-xs" style={{ color: t.muted }}>
							People with run access to this Dive's Flight can answer.
						</p>
					)}
				</div>
			</div>
		</div>
	);
}

function QuestionsBlock({ block, nav }: any) {
	const t = useTheme();
	const items = block.items || [];
	if (!items.length) return null;
	return (
		<div>
			<div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase" style={{ letterSpacing: 1, color: t.muted }}>
				{block.title || 'Questions for you'}
			</div>
			<p className="mb-1 text-xs" style={{ color: t.muted }}>
				The briefing noticed something it can't explain from the data. Your answer becomes a note on the charts and context for every later analysis.
			</p>
			{items.map((it: any) => (
				<QuestionItem key={it.id || it.question} item={it} nav={nav} />
			))}
		</div>
	);
}

registerComponent('questions', { label: 'Questions', icon: MessageSquare, render: QuestionsBlock, chrome: false });
