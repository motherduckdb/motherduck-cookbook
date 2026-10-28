import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useDiveState, useFlightRun, useRunFlight, useSQLQuery } from '@motherduck/react-sql-query';
import {
	ChevronDown,
	BookOpen,
	Check,
	ChevronRight,
	FileText,
	Folder,
	Gem,
	ListTree,
	Loader2,
	Pencil,
	Play,
	Plus,
	RotateCcw,
	Search,
	Sparkles,
	Workflow,
} from 'lucide-react';

export const REQUIRED_DATABASES = [
  { type: 'database', path: 'md:interview_insights', alias: 'interview_insights' }
];

// deploy.py fills in the id of the interview-insights Flight.
const FLIGHT_ID = '00000000-0000-0000-0000-000000000000';

const C = {
	bg: '#f5f4f7',
	panel: '#ffffff',
	text: '#26232b',
	muted: '#6f6b78',
	line: '#e6e3eb',
	select: '#f0ecf5',
	accent: '#e0632a',
	pos: '#2d7a00',
	neg: '#bc1200',
};
const MODELS = ['gpt-6-luna', 'gpt-5', 'gpt-4.1', 'gpt-5-mini'];
const DB = '"interview_insights"."main"';

const N = (v: unknown): number => (v == null ? 0 : Number(v));
const S = (v: unknown): string => (v == null ? '' : String(v));
const rowsOf = (q: { data?: unknown }) => (Array.isArray(q.data) ? (q.data as any[]) : []);
const fmt = (v: number) => v.toLocaleString('en-US');
const signed = (v: number) => (v > 0 ? '+' : '') + v.toFixed(2);

// Viewers need a run grant on the Flight. The hook's error has no code, so match its text.
const NO_RUN_ACCESS = /permission|forbidden|denied|not authori[sz]ed|unauthori[sz]ed|\b403\b|not found/i;
function startErrorText(err: { message?: string }): string {
	const msg = S(err?.message);
	return NO_RUN_ACCESS.test(msg)
		? `You can't run this pipeline. Ask the owner of this Dive to grant you run access to Flight ${FLIGHT_ID}. Restricted members can't run Flights. (${msg})`
		: `Could not start the run: ${msg}`;
}

const panel = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 };
const label = { color: C.muted, letterSpacing: 0.6 };
const clamp = (lines: number) => ({
	display: '-webkit-box',
	WebkitLineClamp: lines,
	WebkitBoxOrient: 'vertical' as const,
	overflow: 'hidden',
});

function Skeleton({ h = 16, w = '100%' }: { h?: number; w?: string | number }) {
	return <div className="animate-pulse rounded bg-gray-200" style={{ height: h, width: w }} />;
}

function Err({ q }: { q: { isError?: boolean; error?: unknown } }) {
	return q.isError ? (
		<p role="alert" className="text-sm" style={{ color: C.neg }}>
			{String(q.error)}
		</p>
	) : null;
}

// Viewers read the database through a share, which lags the Flight's writes. The Flight
// publishes a snapshot when it finishes; refresh the share once per succeeded run, then refetch.
// A plain database attachment can't be refreshed, so an error also counts as done.
// Returns true while the refresh is still running.
function useAfterRun(runNumber: number | undefined, succeeded: boolean, onFresh: () => void): boolean {
	const refresh = useSQLQuery(`REFRESH DATABASE "interview_insights" -- run ${runNumber}`, { enabled: succeeded });
	const handled = useRef<number | undefined>(undefined);
	useEffect(() => {
		if ((refresh.isSuccess || refresh.isError) && succeeded && handled.current !== runNumber) {
			handled.current = runNumber;
			onFresh();
		}
	}, [refresh.isSuccess, refresh.isError, succeeded, runNumber]);
	return succeeded && handled.current !== runNumber;
}

function Kpi({ name, value, loading }: { name: string; value: string; loading: boolean }) {
	return (
		<div>
			{loading ? <Skeleton h={36} w={80} /> : <p className="text-3xl font-semibold">{value}</p>}
			<p className="text-xs mt-1" style={{ color: C.muted }}>
				{name}
			</p>
		</div>
	);
}

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: any }) {
	return (
		<button
			onClick={onClick}
			className="px-1 pb-2 text-sm"
			style={{
				color: active ? C.text : C.muted,
				borderBottom: `2px solid ${active ? C.accent : 'transparent'}`,
				fontWeight: active ? 600 : 400,
			}}
		>
			{children}
		</button>
	);
}

/* ------------------------------------------------------------------ corpus */

const CONTEXT_FIELDS: [string, string, string][] = [
	['source', 'Source', 'Who collected the data, e.g. a company or research group'],
	['domain', 'Industry or domain', 'e.g. retail banking, higher education'],
	['period', 'Period', 'When the conversations took place'],
	['participants', 'Participants', 'Who the participants are'],
	['question', 'Research question', 'What this analysis should answer'],
];

const parseObj = (v: unknown): Record<string, string> => {
	try {
		const o = JSON.parse(S(v) || '{}');
		return o && typeof o === 'object' ? o : {};
	} catch {
		return {};
	}
};

// Summary plus the context the researcher confirmed, as plain text for prompts.
function corpusText(row: any): string {
	const ctx = parseObj(row?.context);
	const lines = CONTEXT_FIELDS.filter(([k]) => ctx[k]).map(([k, name]) => `- ${name}: ${ctx[k]}`);
	return [S(row?.summary), lines.length ? `Confirmed context:\n${lines.join('\n')}` : ''].filter(Boolean).join('\n');
}

function CorpusPanel({
	q,
	busy,
	running,
	failed,
	onSave,
}: {
	q: { data?: unknown; isLoading?: boolean; isError?: boolean; error?: unknown };
	busy: boolean;
	running: boolean;
	failed: boolean;
	onSave: (context: string, summary: string) => void;
}) {
	const row = rowsOf(q)[0];
	const context = parseObj(row?.context);
	const suggested = parseObj(row?.suggested);
	const [editing, setEditing] = useState(false);
	const [summary, setSummary] = useState('');
	const [values, setValues] = useState<Record<string, string>>({});
	// What was just submitted, shown with a "pending" tag until the saved row comes back.
	const [pending, setPending] = useState<{ summary: string; context: Record<string, string> | null } | null>(null);
	const dataAtSave = useRef<unknown>(null);
	useEffect(() => {
		if (!running && (failed || q.data !== dataAtSave.current)) setPending(null);
	}, [q.data, failed, running]);
	const markPending = (p: { summary: string; context: Record<string, string> | null }) => {
		dataAtSave.current = q.data;
		setPending(p);
	};
	const field = { border: `1px solid ${C.line}`, background: 'white' };
	const startEdit = () => {
		setSummary(S(row?.summary));
		setValues(Object.fromEntries(CONTEXT_FIELDS.map(([k]) => [k, context[k] ?? suggested[k] ?? ''])));
		setEditing(true);
	};
	const save = (withSummary: boolean) => {
		const filled = Object.fromEntries(
			Object.entries(values)
				.map(([k, v]) => [k, v.trim()])
				.filter(([, v]) => v),
		);
		const text = withSummary ? summary.trim() : '';
		markPending({ summary: text, context: filled });
		onSave(JSON.stringify(filled), text);
		setEditing(false);
	};
	const Pending = () => (
		<span className="text-xs ml-2 uppercase" style={{ color: C.muted, letterSpacing: 0.5 }}>
			pending
		</span>
	);
	const shownSummary = pending?.summary || S(row?.summary);

	return (
		<div className="p-5 flex flex-col gap-3" style={panel}>
			<div className="flex items-center justify-between gap-3">
				<p className="text-xs uppercase" style={label}>
					About this corpus
				</p>
				<div className="flex items-center gap-2">
					{running && (
						<span className="text-xs flex items-center gap-1" style={{ color: C.muted }}>
							<Loader2 size={12} className="animate-spin" /> {pending?.summary ? 'Saving…' : 'Updating summary…'}
						</span>
					)}
					{failed && !running && (
						<span className="text-xs" style={{ color: C.neg }}>
							Saving failed; nothing changed.
						</span>
					)}
					{!editing && (
						<>
							<Btn disabled={busy} onClick={startEdit}>
								<Pencil size={13} /> Edit
							</Btn>
							<Btn
								disabled={busy}
								onClick={() => {
									markPending({ summary: '', context: null });
									onSave('', '');
								}}
							>
								<Sparkles size={13} /> Regenerate summary
							</Btn>
						</>
					)}
				</div>
			</div>
			{editing ? (
				<div className="flex flex-col gap-2 text-sm">
					<p className="text-xs" style={{ color: C.muted }}>
						The summary and confirmed context are added to every prompt: discover, theme summaries and suggestions.
						Prefilled values are the model's guesses; correct or clear them.
					</p>
					<label className="grid gap-3" style={{ gridTemplateColumns: '160px 1fr' }}>
						<span className="text-xs uppercase pt-1" style={label}>
							Summary
						</span>
						<textarea
							value={summary}
							maxLength={2000}
							rows={4}
							onChange={(e) => setSummary(e.target.value)}
							placeholder="What the corpus is, who the participants are and what they talk about"
							className="px-2 py-1 rounded"
							style={field}
						/>
					</label>
					{CONTEXT_FIELDS.map(([k, name, hint]) => (
						<label key={k} className="grid gap-3 items-center" style={{ gridTemplateColumns: '160px 1fr' }}>
							<span className="text-xs uppercase" style={label}>
								{name}
							</span>
							<input
								value={values[k] ?? ''}
								placeholder={hint}
								maxLength={300}
								onChange={(e) => setValues({ ...values, [k]: e.target.value })}
								className="px-2 py-1 rounded"
								style={field}
							/>
						</label>
					))}
					<div className="flex gap-2 justify-end">
						<Btn onClick={() => setEditing(false)}>Cancel</Btn>
						<Btn onClick={() => save(false)}>
							<Sparkles size={13} /> Save and regenerate summary
						</Btn>
						<Btn kind="primary" disabled={!summary.trim()} onClick={() => save(true)}>
							Save
						</Btn>
					</div>
				</div>
			) : (
				<>
					{q.isLoading ? (
						<Skeleton h={60} />
					) : (
						<p
							className="text-sm leading-relaxed"
							style={{ maxWidth: 900, opacity: running && !pending?.summary ? 0.5 : 1 }}
						>
							{shownSummary || 'No summary yet. Run Load, or Regenerate summary.'}
							{running && pending?.summary && <Pending />}
						</p>
					)}
					<Err q={q} />
					<dl className="grid gap-x-6 gap-y-1 text-sm" style={{ gridTemplateColumns: 'max-content 1fr' }}>
						{CONTEXT_FIELDS.map(([k, name]) => {
							const isPending = !!pending?.context;
							const v = isPending ? pending!.context![k] : context[k] ?? suggested[k];
							return (
								<Fragment key={k}>
									<dt className="text-xs uppercase" style={{ ...label, paddingTop: 2 }}>
										{name}
									</dt>
									<dd>
										{v ? (
											<>
												{v}
												{isPending ? (
													<Pending />
												) : (
													!context[k] && (
														<span className="text-xs ml-2" style={{ color: C.accent }}>
															suggested
														</span>
													)
												)}
											</>
										) : (
											<span style={{ color: C.muted }}>unknown</span>
										)}
									</dd>
								</Fragment>
							);
						})}
					</dl>
				</>
			)}
		</div>
	);
}

/* ------------------------------------------------------------------ pipeline */

type Phase = 'load' | 'describe' | 'discover' | 'classify' | 'reset';

function Pipeline({ onClassified, onReview }: { onClassified: () => void; onReview: () => void }) {
	const stats = useSQLQuery(`
    SELECT
      (SELECT count(*) FROM ${DB}."transcripts") AS conversations,
      (SELECT count(*) FROM ${DB}."turns" WHERE speaker = 'participant') AS answers,
      (SELECT count(*) FROM ${DB}."themes") AS themes,
      (SELECT count(*) FROM ${DB}."turn_themes") AS classified
  `);
	const runs = useSQLQuery(`
    SELECT phase, strftime(finished_at, '%Y-%m-%d %H:%M') AS finished, details
    FROM ${DB}."pipeline_runs" ORDER BY finished_at DESC LIMIT 6
  `);
	const corpus = useSQLQuery(`SELECT summary, context, suggested FROM ${DB}."corpus" WHERE id = 1`);
	const source = useSQLQuery(`
    SELECT details->>'source' AS name, details->>'url' AS url FROM ${DB}."pipeline_runs"
    WHERE phase = 'load' ORDER BY finished_at DESC LIMIT 1
  `);
	const lastRuns = useSQLQuery(`
    SELECT phase, strftime(max(finished_at), '%Y-%m-%d %H:%M:%S.%f') AS last
    FROM ${DB}."pipeline_runs" GROUP BY 1
  `);
	const codebook = useSQLQuery(`
    SELECT t.theme_id, g.group_name, t.theme_name, t.description, count(i.insight_id) AS insights
    FROM ${DB}."themes" t
    JOIN ${DB}."groups" g USING (group_id)
    LEFT JOIN ${DB}."insights" i USING (theme_id)
    GROUP BY ALL ORDER BY t.theme_id
  `);

	const stale = useSQLQuery(`
    SELECT count(*) AS edits FROM ${DB}."pipeline_runs"
    WHERE phase = 'edit' AND finished_at > (
      SELECT coalesce(max(finished_at), TIMESTAMP '1970-01-01') FROM ${DB}."pipeline_runs"
      WHERE phase IN ('discover', 'classify'))
  `);

	const [phase, setPhase] = useState<Phase | null>(null);
	const [perCohort, setPerCohort] = useState('20');
	const [model, setModel] = useState('gpt-6-luna');
	const [limit, setLimit] = useState('500');
	const [confirmReset, setConfirmReset] = useState(false);

	const start = useRunFlight(FLIGHT_ID);
	const run = useFlightRun(FLIGHT_ID, start.data?.runNumber);
	const refreshing = useAfterRun(start.data?.runNumber, run.status === 'SUCCEEDED', () => {
		stats.refetch();
		runs.refetch();
		codebook.refetch();
		stale.refetch();
		corpus.refetch();
		source.refetch();
		lastRuns.refetch();
		if (phase === 'classify') onClassified();
	});
	const busy = start.isPending || run.isChecking || refreshing;

	const go = (p: Phase, config: Record<string, string>) => {
		setPhase(p);
		start.run({ PHASE: p, ...config });
	};

	const field = { border: `1px solid ${C.line}`, background: 'white' };
	const s = rowsOf(stats)[0] ?? {};
	const src = rowsOf(source)[0] ?? {};
	// Timestamps are fixed-width strings, so they compare in time order.
	const last = Object.fromEntries(rowsOf(lastRuns).map((r) => [S(r.phase), S(r.last)])) as Record<string, string>;
	const after = (a: string | undefined, ...bs: (string | undefined)[]) => !!a && bs.every((b) => !b || a > b);
	const done: Record<string, boolean> = {
		load: !!last.load,
		discover: after(last.discover, last.load),
		review: after(last.edit, last.discover),
		classify: after(last.classify, last.discover, last.edit),
	};
	const steps: { key: Phase | 'review'; title: string; body: string; controls?: any; config: Record<string, string> }[] = [
		{
			key: 'load',
			title: '1  Load',
			body: 'Load conversations and split them into participant and interviewer turns. To use your own data, adapt load_turns() in the Flight.',
			config: {},
			controls: S(src.name) ? (
				<span className="text-xs">
					Last loaded from{' '}
					{S(src.url) ? (
						<a href={S(src.url)} target="_blank" rel="noopener noreferrer" style={{ color: C.muted, textDecoration: 'underline' }}>
							{S(src.name)}
						</a>
					) : (
						S(src.name)
					)}
				</span>
			) : undefined,
		},
		{
			key: 'discover',
			title: '2  Discover',
			body: 'Large model reads a stratified sample and writes the codebook: groups, themes, insights.',
			config: { SAMPLE_PER_COHORT: perCohort, MODEL: model },
			controls: (
				<div className="flex gap-2 items-center text-xs" style={{ color: C.muted }}>
					<span>per cohort</span>
					<input
						value={perCohort}
						onChange={(e) => setPerCohort(e.target.value.replace(/\D/g, ''))}
						className="px-2 py-1 rounded"
						style={{ ...field, width: 48 }}
					/>
					<select value={model} onChange={(e) => setModel(e.target.value)} className="px-2 py-1 rounded" style={field}>
						{MODELS.map((m) => (
							<option key={m}>{m}</option>
						))}
					</select>
				</div>
			),
		},
		{
			key: 'review',
			title: '3  Review',
			body: 'Read the codebook with its evidence, then rename, redefine, merge or delete themes before classifying every answer.',
			config: {},
		},
		{
			key: 'classify',
			title: '4  Classify',
			body: 'prompt_jev assigns every answer to a theme and insight, scores sentiment and quotability.',
			config: { LIMIT_TURNS: limit },
			controls: (
				<div className="flex gap-2 items-center text-xs" style={{ color: C.muted }}>
					<span>answers (0 = all)</span>
					<input
						value={limit}
						onChange={(e) => setLimit(e.target.value.replace(/\D/g, ''))}
						className="px-2 py-1 rounded"
						style={{ ...field, width: 64 }}
					/>
				</div>
			),
		},
	];

	return (
		<div className="flex flex-col gap-4">
			<p className="text-sm leading-relaxed" style={{ maxWidth: 760 }}>
				Qualitative research without leaving MotherDuck: a Flight loads conversation transcripts, prompt()
				drafts a codebook of themes from a sample, and prompt_jev() classifies every participant answer against it.
				<br />
				<span style={{ color: C.muted }}>
					You review and edit the codebook in this Dive, which writes your changes back by running the same
					Flight, then reclassify.
				</span>
			</p>
			<CorpusPanel
				q={corpus}
				busy={busy}
				running={phase === 'describe' && busy}
				failed={phase === 'describe' && (!!start.error || (!!run.isFinished && run.status !== 'SUCCEEDED'))}
				onSave={(context, summary) => go('describe', { CONTEXT: context, SUMMARY: summary })}
			/>
			<div className="grid grid-cols-4 gap-8 p-5" style={panel}>
				<Kpi name="Conversations" value={fmt(N(s.conversations))} loading={stats.isLoading} />
				<Kpi name="Participant answers" value={fmt(N(s.answers))} loading={stats.isLoading} />
				<Kpi name="Themes in codebook" value={fmt(N(s.themes))} loading={stats.isLoading} />
				<Kpi name="Answers classified" value={fmt(N(s.classified))} loading={stats.isLoading} />
			</div>
			<Err q={stats} />

			<div className="flex flex-col px-5" style={panel}>
				{steps.map((st, i) => {
					const active = phase === st.key;
					return (
						<div
							key={st.key}
							className="flex items-center gap-4 py-3"
							style={{ borderTop: i ? `1px solid ${C.line}` : 'none' }}
						>
							<div style={{ width: 120 }} className="text-sm font-semibold flex items-center gap-2">
								{st.title}
								{done[st.key] && (
									<span
										title="Done"
										aria-label="Done"
										className="flex items-center justify-center rounded-full"
										style={{ width: 18, height: 18, background: C.pos }}
									>
										<Check size={12} color="white" strokeWidth={3} />
									</span>
								)}
							</div>
							<div className="flex-1 text-sm" style={{ color: C.muted }}>
								{st.body}
								{st.key === 'classify' && N(rowsOf(stale)[0]?.edits) > 0 && (
									<span className="block mt-1" style={{ color: C.accent }}>
										The codebook changed since the last classification. Run this to apply the edits.
									</span>
								)}
								{st.controls && <div className="mt-2">{st.controls}</div>}
							</div>
							<div style={{ width: 90 }} className="text-xs text-right" aria-live="polite">
								{active && run.status && (
									<span
										style={{
											color: run.status === 'SUCCEEDED' ? C.pos : run.isFinished ? C.neg : C.muted,
										}}
									>
										{run.status.toLowerCase()}
									</span>
								)}
							</div>
							{st.key === 'review' ? (
								<Btn onClick={onReview}>
									<BookOpen size={14} /> Open codebook
								</Btn>
							) : (
							<button
								disabled={busy}
								onClick={() => go(st.key as Phase, st.config)}
								className="flex items-center gap-1 px-3 py-1 rounded text-sm"
								style={{
									background: busy ? C.line : C.text,
									color: busy ? C.muted : 'white',
									cursor: busy ? 'default' : 'pointer',
								}}
							>
								{busy && active ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
								{last[st.key] ? 'Re-run' : 'Run'}
							</button>
							)}
						</div>
					);
				})}
			</div>
			<div className="flex items-center justify-end gap-3 text-sm" aria-live="polite">
				{phase === 'reset' && (
					<span style={{ color: run.isFinished && run.status !== 'SUCCEEDED' ? C.neg : C.muted }}>
						{busy
							? 'Deleting all data…'
							: run.status === 'SUCCEEDED'
							? 'All data deleted. Run Load to start again.'
							: run.isFinished
							? 'Reset failed.'
							: ''}
					</span>
				)}
				{confirmReset ? (
					<>
						<span style={{ color: C.neg }}>
							Delete the transcripts, codebook, classifications, corpus context and run history?
						</span>
						<Btn onClick={() => setConfirmReset(false)}>Cancel</Btn>
						<Btn
							kind="danger"
							disabled={busy}
							onClick={() => {
								setConfirmReset(false);
								go('reset', {});
							}}
						>
							Delete everything
						</Btn>
					</>
				) : (
					<Btn kind="danger" disabled={busy} onClick={() => setConfirmReset(true)}>
						<RotateCcw size={13} /> Reset all data
					</Btn>
				)}
			</div>
			{start.error && (
				<p className="text-sm" style={{ color: C.neg }}>
					{startErrorText(start.error)}
				</p>
			)}
			{run.error && (
				<p className="text-sm" style={{ color: C.neg }}>
					Could not check the run: {run.error.message}
				</p>
			)}

			<div className="grid grid-cols-2 gap-4">
				<div className="p-5" style={panel}>
					<div className="flex items-center justify-between mb-2">
						<p className="text-xs uppercase" style={label}>
							Codebook
						</p>
						<Btn onClick={onReview}>Review and edit</Btn>
					</div>
					{codebook.isLoading ? (
						<Skeleton h={120} />
					) : codebook.isError ? (
						<Err q={codebook} />
					) : rowsOf(codebook).length === 0 ? (
						<p className="text-sm" style={{ color: C.muted }}>
							Run Discover to generate themes.
						</p>
					) : (
						<div style={{ maxHeight: 260, overflowY: 'auto' }}>
							{rowsOf(codebook).map((r, i, all) => (
								<div key={N(r.theme_id)}>
									{(i === 0 || S(all[i - 1].group_name) !== S(r.group_name)) && (
										<p className="text-sm font-semibold mt-3 mb-1">{S(r.group_name)}</p>
									)}
									<p className="text-sm pl-3" title={S(r.description)}>
										{S(r.theme_name)} <span style={{ color: C.muted }}>· {N(r.insights)} insights</span>
									</p>
								</div>
							))}
						</div>
					)}
				</div>
				<div className="p-5" style={panel}>
					<p className="text-xs uppercase mb-2" style={label}>
						Recent runs
					</p>
					{runs.isLoading ? (
						<Skeleton h={120} />
					) : (
						<table className="w-full text-xs">
							<tbody>
								{rowsOf(runs).map((r, i) => {
									let d: Record<string, unknown> = {};
									try {
										d = JSON.parse(S(r.details));
									} catch {}
									return (
										<tr key={i} style={{ borderTop: i ? `1px solid ${C.line}` : 'none', verticalAlign: 'top' }}>
											<td className="py-1 pr-2 font-semibold">{S(r.phase)}</td>
											<td className="py-1 pr-2" style={{ color: C.muted, whiteSpace: 'nowrap' }}>
												{S(r.finished)}
											</td>
											<td className="py-1" style={{ color: C.muted }}>
												{Object.entries(d)
													.map(([k, v]) => `${k} ${v}`)
													.join(' · ')}
											</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					)}
					<Err q={runs} />
				</div>
			</div>
		</div>
	);
}

/* ------------------------------------------------------------------ codebook draft */

// Edits are staged in a draft (see useDraft) and applied in one Flight run on submit.
// Themes added in the draft get negative ids until the Flight assigns real ones.
type Edit =
	| { op: 'add'; id: number; group: number; name: string; description: string; importance: number }
	| { op: 'update'; id: number; name: string; description: string; importance: number }
	| { op: 'delete'; id: number }
	| { op: 'merge'; id: number; target: number };

type Theme = {
	id: number;
	groupId: number;
	group: string;
	name: string;
	description: string;
	importance: number;
	n: number;
	status?: 'new' | 'edited' | 'deleted' | 'merged';
	target?: number;
	// Evidence from the last classification, shown in the Codebook view.
	answers?: number;
	share?: number;
	conf?: number;
	runnerUp?: number;
	closeShare?: number;
	insights?: string[];
	quote?: string;
};

const IMPORTANCE = [
	['3', 'High'],
	['2', 'Normal'],
	['1', 'Low'],
];

function applyDraft(themes: Theme[], groups: Map<number, string>, draft: Edit[]): Theme[] {
	const out = themes.map((t) => ({ ...t }));
	for (const e of draft) {
		if (e.op === 'add') {
			const { op, group, ...rest } = e;
			out.push({ ...rest, groupId: group, group: groups.get(group) ?? '', n: 0, status: 'new' });
			continue;
		}
		const t = out.find((x) => x.id === e.id);
		if (!t) continue;
		if (e.op === 'update') {
			Object.assign(t, { name: e.name, description: e.description, importance: e.importance });
			if (t.status !== 'new') t.status = 'edited';
		} else if (e.op === 'delete') t.status = 'deleted';
		else Object.assign(t, { status: 'merged', target: e.target });
	}
	return out;
}

function stageEdit(draft: Edit[], e: Edit): Edit[] {
	if (e.id < 0 && e.op === 'update') {
		return draft.map((d) => (d.op === 'add' && d.id === e.id ? { ...d, ...e, op: 'add' } : d));
	}
	if (e.id < 0 && e.op === 'delete') return revert(draft, e.id);
	if (e.id < 0 && e.op === 'merge') {
		// A new theme has no insights yet, so merging it only redirects merges that pointed at it.
		return revert(draft, e.id).concat(
			draft.filter((d) => d.op === 'merge' && d.target === e.id).map((d) => ({ ...d, target: e.target })),
		);
	}
	return [...draft.filter((d) => d.id !== e.id), e];
}

function revert(draft: Edit[], id: number): Edit[] {
	return draft.filter((d) => d.id !== id && !(d.op === 'merge' && d.target === id));
}

// Deletes and merges first so their names are free for renames and new themes.
function submitOrder(draft: Edit[]): Edit[] {
	const rank = (d: Edit) =>
		d.op === 'delete' ? 0 : d.op === 'merge' && d.target > 0 ? 1 : d.op === 'add' ? 2 : d.op === 'update' ? 3 : 4;
	return [...draft].sort((a, b) => rank(a) - rank(b));
}

function nameProblem(name: string, themes: Theme[], id?: number): string | null {
	const n = name.trim();
	if (!n) return 'Name is required.';
	if (n.length > 80) return 'Name must be at most 80 characters.';
	if (n.toLowerCase() === 'other') return '"other" is reserved for answers that fit no theme.';
	const clash = themes.find(
		(t) => t.id !== id && !['deleted', 'merged'].includes(t.status ?? '') && t.name.toLowerCase() === n.toLowerCase(),
	);
	return clash ? `"${clash.name}" already exists.` : null;
}

function Btn({
	kind = 'plain',
	disabled,
	onClick,
	children,
}: {
	kind?: 'plain' | 'primary' | 'danger';
	disabled?: boolean;
	onClick: () => void;
	children: any;
}) {
	const look = {
		plain: { background: 'white', color: C.text, border: `1px solid ${C.line}` },
		primary: { background: C.text, color: 'white', border: `1px solid ${C.text}` },
		danger: { background: 'white', color: C.neg, border: `1px solid ${C.line}` },
	}[kind];
	return (
		<button
			disabled={disabled}
			onClick={onClick}
			className="flex items-center gap-1 px-3 py-1 rounded text-sm"
			style={{ ...look, opacity: disabled ? 0.45 : 1, cursor: disabled ? 'default' : 'pointer' }}
		>
			{children}
		</button>
	);
}

function StatusTag({ status }: { status?: Theme['status'] }) {
	if (!status) return null;
	const color = status === 'new' || status === 'edited' ? C.accent : C.muted;
	return (
		<span className="text-xs uppercase" style={{ color, letterSpacing: 0.5 }}>
			{status}
		</span>
	);
}

function ThemeForm({
	initial,
	themes,
	id,
	submitLabel,
	onSubmit,
	onCancel,
}: {
	initial: { name: string; description: string; importance: number };
	themes: Theme[];
	id?: number;
	submitLabel: string;
	onSubmit: (v: { name: string; description: string; importance: number }) => void;
	onCancel: () => void;
}) {
	const [name, setName] = useState(initial.name);
	const [description, setDescription] = useState(initial.description);
	const [importance, setImportance] = useState(String(initial.importance));
	const problem = nameProblem(name, themes, id) ?? (description.trim() ? null : 'Definition is required.');
	const field = { border: `1px solid ${C.line}`, background: 'white' };
	return (
		<div className="flex flex-col gap-2 text-sm">
			<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Theme name" className="px-2 py-1 rounded" style={field} />
			<textarea
				value={description}
				onChange={(e) => setDescription(e.target.value)}
				placeholder="Definition the classifier uses to decide whether an answer belongs here"
				rows={3}
				className="px-2 py-1 rounded"
				style={field}
			/>
			<div className="flex items-center gap-2">
				<span style={{ color: C.muted }}>Importance</span>
				<select value={importance} onChange={(e) => setImportance(e.target.value)} className="px-2 py-1 rounded" style={field}>
					{IMPORTANCE.map(([v, l]) => (
						<option key={v} value={v}>
							{l}
						</option>
					))}
				</select>
				<span className="flex-1" />
				<Btn onClick={onCancel}>Cancel</Btn>
				<Btn
					kind="primary"
					disabled={!!problem}
					onClick={() => onSubmit({ name: name.trim(), description: description.trim(), importance: Number(importance) })}
				>
					{submitLabel}
				</Btn>
			</div>
			{problem && (name || description) && (
				<p className="text-xs" style={{ color: C.neg }}>
					{problem}
				</p>
			)}
		</div>
	);
}

/* ------------------------------------------------------------------ categories */

function CategoryTree({
	themes,
	groups,
	loading,
	selected,
	onSelect,
}: {
	themes: Theme[];
	groups: [number, string][];
	loading: boolean;
	selected: number | null;
	onSelect: (id: number) => void;
}) {
	const [search, setSearch] = useState('');
	const [closed, setClosed] = useState<Record<number, boolean>>({});
	const order = (t: Theme) => [-t.importance, t.id < 0 ? 1e6 - t.id : t.id];

	return (
		<div className="flex flex-col gap-3" style={{ width: 260, flexShrink: 0 }}>
			<div className="flex items-center gap-2 px-3 py-2" style={{ ...panel, borderRadius: 999 }}>
				<Search size={14} color={C.muted} />
				<input
					value={search}
					onChange={(e) => setSearch(e.target.value)}
					placeholder="Search categories..."
					className="text-sm flex-1"
					style={{ outline: 'none', background: 'transparent' }}
				/>
			</div>
			<div className="pr-3" style={{ maxHeight: 600, overflowY: 'auto' }}>
				{loading && <Skeleton h={200} />}
				{groups.map(([gid, gname]) => {
					const items = themes
						.filter((t) => t.groupId === gid && (!search || t.name.toLowerCase().includes(search.toLowerCase())))
						.sort((a, b) => {
							const [x, y] = [order(a), order(b)];
							return x[0] - y[0] || x[1] - y[1];
						});
					return (
						<div key={gid} className="mb-2">
							<button
								onClick={() => setClosed({ ...closed, [gid]: !closed[gid] })}
								className="flex items-center gap-1 text-sm font-semibold py-1 w-full text-left"
							>
								{closed[gid] ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
								{gname}
							</button>
							{!closed[gid] &&
								items.map((t) => {
										const gone = t.status === 'deleted' || t.status === 'merged';
										return (
											<button
												key={t.id}
												onClick={() => onSelect(t.id)}
												className="flex justify-between gap-2 text-sm py-1 pl-6 pr-2 w-full text-left rounded"
												style={{ background: selected === t.id ? C.select : 'transparent' }}
											>
												<span
													style={{
														color: gone || t.importance === 1 ? C.muted : C.text,
														textDecoration: gone ? 'line-through' : 'none',
														fontWeight: t.importance === 3 ? 600 : 400,
													}}
												>
													{t.importance === 3 && <span style={{ color: C.accent }}>● </span>}
													{t.name}
												</span>
												<span className="text-xs flex-shrink-0" style={{ color: C.muted }}>
													{t.status ? <StatusTag status={t.status} /> : t.n ? fmt(t.n) : ''}
												</span>
											</button>
										);
								})}
						</div>
					);
				})}
			</div>
		</div>
	);
}

/* ------------------------------------------------------------------ theme tabs */

function ThemeSummary({ themeId, cohortSql }: { themeId: number; cohortSql: string }) {
	const summary = useSQLQuery(`SELECT summary FROM ${DB}."theme_summaries" WHERE theme_id = ${themeId}`);
	const cohorts = useSQLQuery(`
    WITH totals AS (
      SELECT tu.cohort, count(*) AS total
      FROM ${DB}."turn_themes" tt JOIN ${DB}."turns" tu USING (turn_id) GROUP BY 1
    )
    SELECT tu.cohort, count(*) AS answers, count(*) / any_value(totals.total) AS share,
      avg(tt.sentiment) AS sentiment
    FROM ${DB}."turn_themes" tt JOIN ${DB}."turns" tu USING (turn_id) JOIN totals USING (cohort)
    WHERE tt.theme_id = ${themeId} ${cohortSql}
    GROUP BY 1 ORDER BY 2 DESC
  `);
	return (
		<div className="flex flex-col gap-6">
			{summary.isLoading ? (
				<div className="flex flex-col gap-2">
					<Skeleton w="90%" />
					<Skeleton w="80%" />
					<Skeleton w="60%" />
				</div>
			) : (
				<p className="text-sm leading-relaxed" style={{ maxWidth: 680 }}>
					{S(rowsOf(summary)[0]?.summary) || 'No summary yet. Run Classify.'}
				</p>
			)}
			<Err q={summary} />
			<table className="text-sm" style={{ maxWidth: 480 }}>
				<thead>
					<tr className="text-xs uppercase" style={label}>
						<th className="text-left font-normal py-1">Cohort</th>
						<th className="text-right font-normal py-1">Answers</th>
						<th className="text-right font-normal py-1">Share of cohort</th>
						<th className="text-right font-normal py-1">Sentiment</th>
					</tr>
				</thead>
				<tbody>
					{rowsOf(cohorts).map((r) => (
						<tr key={S(r.cohort)} style={{ borderTop: `1px solid ${C.line}` }}>
							<td className="py-1">{S(r.cohort)}</td>
							<td className="py-1 text-right">{fmt(N(r.answers))}</td>
							<td className="py-1 text-right">{(N(r.share) * 100).toFixed(1)}%</td>
							<td className="py-1 text-right">{signed(N(r.sentiment))}</td>
						</tr>
					))}
				</tbody>
			</table>
			<Err q={cohorts} />
		</div>
	);
}

function QuoteText({ text }: { text: string }) {
	const [open, setOpen] = useState(false);
	return (
		<p
			onClick={() => setOpen(!open)}
			title={open ? 'Collapse' : 'Show full answer'}
			style={{ cursor: 'pointer', ...(open ? {} : clamp(4)) }}
		>
			{text}
		</p>
	);
}

function InsightsQuotes({ themeId, cohortSql }: { themeId: number; cohortSql: string }) {
	const q = useSQLQuery(`
    WITH ranked AS (
      SELECT ti.insight_id, tu.text, tu.transcript_id,
        row_number() OVER (PARTITION BY ti.insight_id ORDER BY ti.quotability * ti.insight_conf DESC) AS rn,
        count(*) OVER (PARTITION BY ti.insight_id) AS support
      FROM ${DB}."turn_insights" ti JOIN ${DB}."turns" tu USING (turn_id)
      WHERE ti.theme_id = ${themeId} AND ti.insight_id IS NOT NULL ${cohortSql}
    )
    SELECT i.insight_id, i.insight, coalesce(r.support, 0) AS support, r.text, r.transcript_id, r.rn
    FROM ${DB}."insights" i
    LEFT JOIN ranked r ON r.insight_id = i.insight_id AND r.rn <= 3
    WHERE i.theme_id = ${themeId}
    ORDER BY support DESC, i.insight_id, r.rn
  `);

	const insights = useMemo(() => {
		const out: { id: number; text: string; support: number; quotes: { text: string; source: string }[] }[] = [];
		for (const r of rowsOf(q)) {
			const id = N(r.insight_id);
			let ins = out[out.length - 1];
			if (!ins || ins.id !== id) out.push((ins = { id, text: S(r.insight), support: N(r.support), quotes: [] }));
			if (r.text) ins.quotes.push({ text: S(r.text), source: S(r.transcript_id) });
		}
		return out;
	}, [q.data]);

	if (q.isLoading) return <Skeleton h={240} />;
	return (
		<div style={{ maxHeight: 520, overflowY: 'auto' }}>
			<Err q={q} />
			<table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
				<thead>
					<tr className="text-xs uppercase" style={label}>
						<th className="text-left font-normal py-2 pr-6" style={{ width: '32%' }}>
							Insight
						</th>
						<th className="text-left font-normal py-2 pr-6">Quote</th>
						<th className="text-left font-normal py-2" style={{ width: 110 }}>
							Source
						</th>
					</tr>
				</thead>
				<tbody>
					{insights.map((ins) => {
						const quotes = ins.quotes.length ? ins.quotes : [null];
						return quotes.map((qt, j) => (
							<tr
								key={`${ins.id}-${j}`}
								style={{ borderTop: j === 0 ? `1px solid ${C.line}` : 'none', verticalAlign: 'top' }}
							>
								{j === 0 && (
									<td rowSpan={quotes.length} className="py-3 pr-6">
										<div className="flex gap-2">
											<Gem size={14} color={C.accent} style={{ flexShrink: 0, marginTop: 3 }} />
											<div>
												<p>{ins.text}</p>
												<p className="text-xs mt-1" style={{ color: C.muted }}>
													{fmt(ins.support)} supporting answers
												</p>
											</div>
										</div>
									</td>
								)}
								<td className="py-3 pr-6">
									{qt ? (
										<QuoteText text={qt.text} />
									) : (
										<span style={{ color: C.muted }}>No supporting answers</span>
									)}
								</td>
								<td className="py-3 text-xs" style={{ color: C.muted }}>
									{qt && (
										<span className="flex items-center gap-1">
											<FileText size={13} />
											{qt.source}
										</span>
									)}
								</td>
							</tr>
						));
					})}
				</tbody>
			</table>
		</div>
	);
}

function Documents({ themeId, cohortSql }: { themeId: number; cohortSql: string }) {
	const q = useSQLQuery(`
    SELECT tu.transcript_id, tu.cohort, count(*) AS answers, avg(tt.sentiment) AS sentiment,
      arg_max(tu.text, tt.theme_conf) AS example
    FROM ${DB}."turn_themes" tt JOIN ${DB}."turns" tu USING (turn_id)
    WHERE tt.theme_id = ${themeId} ${cohortSql}
    GROUP BY 1, 2 ORDER BY answers DESC, transcript_id LIMIT 50
  `);
	if (q.isLoading) return <Skeleton h={240} />;
	return (
		<div style={{ maxHeight: 520, overflowY: 'auto' }}>
			<Err q={q} />
			<table className="w-full text-sm">
				<thead>
					<tr className="text-xs uppercase" style={label}>
						<th className="text-left font-normal py-2 pr-4">Conversation</th>
						<th className="text-right font-normal py-2 pr-4">Answers</th>
						<th className="text-right font-normal py-2 pr-4">Sentiment</th>
						<th className="text-left font-normal py-2">Most typical answer</th>
					</tr>
				</thead>
				<tbody>
					{rowsOf(q).map((r) => (
						<tr key={S(r.transcript_id)} style={{ borderTop: `1px solid ${C.line}`, verticalAlign: 'top' }}>
							<td className="py-2 pr-4">
								<span className="flex items-center gap-1">
									<FileText size={13} color={C.muted} />
									{S(r.transcript_id)}
								</span>
								<span className="text-xs" style={{ color: C.muted }}>
									{S(r.cohort)}
								</span>
							</td>
							<td className="py-2 pr-4 text-right">{N(r.answers)}</td>
							<td className="py-2 pr-4 text-right">{signed(N(r.sentiment))}</td>
							<td className="py-2 text-xs" style={{ color: C.muted }}>
								<span style={clamp(3)}>{S(r.example)}</span>
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function Metadata({ themeId, cohortSql }: { themeId: number; cohortSql: string }) {
	const q = useSQLQuery(`
    SELECT t.theme_id, t.description, g.group_name, g.description AS group_description,
      (SELECT count(*) FROM ${DB}."insights" i WHERE i.theme_id = t.theme_id) AS insights,
      count(tu.turn_id) AS answers, count(DISTINCT tu.transcript_id) AS conversations,
      avg(tt.sentiment) FILTER (tu.turn_id IS NOT NULL) AS sentiment,
      avg(tt.theme_conf) FILTER (tu.turn_id IS NOT NULL) AS conf
    FROM ${DB}."themes" t
    JOIN ${DB}."groups" g USING (group_id)
    LEFT JOIN ${DB}."turn_themes" tt ON tt.theme_id = t.theme_id
    LEFT JOIN ${DB}."turns" tu ON tu.turn_id = tt.turn_id ${cohortSql}
    WHERE t.theme_id = ${themeId}
    GROUP BY ALL
  `);
	const m = rowsOf(q)[0] ?? {};
	const rows: [string, string][] = [
		['Theme ID', S(m.theme_id)],
		['Definition used by the classifier', S(m.description)],
		['Group', `${S(m.group_name)}: ${S(m.group_description)}`],
		['Insights in codebook', fmt(N(m.insights))],
	];
	return (
		<div className="flex flex-col gap-6">
			<div className="grid grid-cols-4 gap-8">
				<Kpi name="Answers" value={fmt(N(m.answers))} loading={q.isLoading} />
				<Kpi name="Conversations" value={fmt(N(m.conversations))} loading={q.isLoading} />
				<Kpi name="Sentiment (−1 to 1)" value={signed(N(m.sentiment))} loading={q.isLoading} />
				<Kpi name="Mean classifier confidence" value={N(m.conf).toFixed(2)} loading={q.isLoading} />
			</div>
			<table className="text-sm" style={{ maxWidth: 680 }}>
				<tbody>
					{rows.map(([k, v]) => (
						<tr key={k} style={{ borderTop: `1px solid ${C.line}`, verticalAlign: 'top' }}>
							<td className="py-2 pr-6 text-xs uppercase" style={{ ...label, width: 200 }}>
								{k}
							</td>
							<td className="py-2">{q.isLoading ? <Skeleton w="70%" /> : v}</td>
						</tr>
					))}
				</tbody>
			</table>
			<Err q={q} />
		</div>
	);
}

type Sub = 'summary' | 'documents' | 'quotes' | 'metadata';

// One draft per database, so two deployments in the same browser do not share it.
const DRAFT_KEY = 'interview-insights:interview_insights:codebook-draft';

function readDraft(): { basis: string; edits: Edit[] } {
	try {
		const v = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null');
		if (v && typeof v.basis === 'string' && Array.isArray(v.edits)) return v;
	} catch {}
	return { basis: '', edits: [] };
}

// The draft lives in this browser's localStorage until it is submitted or discarded.
function useDraft(basis: string | null): [Edit[], (edits: Edit[]) => void] {
	const [stored, setStored] = useState(readDraft);
	const edits = basis != null && stored.basis === basis ? stored.edits : [];
	const setDraft = (next: Edit[]) => {
		const value = { basis: basis ?? '', edits: next };
		setStored(value);
		try {
			if (next.length) localStorage.setItem(DRAFT_KEY, JSON.stringify(value));
			else localStorage.removeItem(DRAFT_KEY);
		} catch {}
	};
	return [edits, setDraft];
}

/* ------------------------------------------------------------------ codebook */

// An answer is a close call when the classifier's runner-up label got at least this probability.
const CLOSE_CALL = 0.25;

const importanceName = (v: number) => IMPORTANCE.find(([k]) => k === String(v))?.[1] ?? 'Normal';

function describeEdits(draft: Edit[], saved: Theme[], themes: Theme[]): { id: number; text: string }[] {
	const name = (id: number) => themes.find((t) => t.id === id)?.name ?? `#${id}`;
	return draft.map((e) => {
		if (e.op === 'add') return { id: e.id, text: `Add "${e.name}" to ${themes.find((t) => t.id === e.id)?.group}` };
		if (e.op === 'delete') return { id: e.id, text: `Delete "${name(e.id)}" and its insights` };
		if (e.op === 'merge') return { id: e.id, text: `Merge "${name(e.id)}" into "${name(e.target)}"` };
		const old = saved.find((t) => t.id === e.id);
		const parts: string[] = [];
		if (old?.name !== e.name) parts.push(`rename to "${e.name}"`);
		if (old?.description !== e.description) parts.push('new definition');
		if (old?.importance !== e.importance) parts.push(`${importanceName(e.importance).toLowerCase()} importance`);
		return { id: e.id, text: `Edit "${old?.name ?? e.name}": ${parts.join(', ') || 'no change'}` };
	});
}

const sqlStr = (v: string) => "'" + v.replace(/'/g, "''") + "'";

const SUGGEST_SCHEMA = JSON.stringify({
	name: 'suggestions',
	strict: true,
	schema: {
		type: 'object',
		additionalProperties: false,
		required: ['suggestions'],
		properties: {
			suggestions: {
				type: 'array',
				items: {
					type: 'object',
					additionalProperties: false,
					required: ['action', 'theme_ids', 'target_id', 'group_id', 'name', 'description', 'reason'],
					properties: {
						action: { type: 'string', enum: ['merge', 'rename', 'redefine', 'delete', 'add'] },
						theme_ids: { type: 'array', items: { type: 'integer' } },
						target_id: { type: ['integer', 'null'] },
						group_id: { type: ['integer', 'null'] },
						name: { type: ['string', 'null'], description: 'New theme name, or null to keep it' },
						description: {
							type: ['string', 'null'],
							description: "The theme's new definition as the classifier will read it, starting with 'Use when'. Null to keep it.",
						},
						reason: { type: 'string', description: 'Why this change helps, for the reviewer' },
					},
				},
			},
		},
	},
});

type Suggestion = {
	action: 'merge' | 'rename' | 'redefine' | 'delete' | 'add';
	theme_ids: number[];
	target_id: number | null;
	group_id: number | null;
	name: string | null;
	description: string | null;
	reason: string;
};

function suggestPrompt(themes: Theme[], hint: string, corpus: string): string {
	const lines = themes
		.map((t) => {
			const evidence =
				t.answers == null
					? 'not classified yet'
					: `${t.n} answers (${((t.share ?? 0) * 100).toFixed(1)}%), confidence ${(t.conf ?? 0).toFixed(2)}` +
					  (t.runnerUp != null ? `, ${Math.round((t.closeShare ?? 0) * 100)}% close calls with [${t.runnerUp}]` : '');
			return `[${t.id}] group ${t.groupId} '${t.group}' > '${t.name}': ${t.description} | ${evidence}`;
		})
		.join('\n');
	return `You review a qualitative-research codebook. A classifier assigns each participant answer to exactly one theme by reading the theme definitions, so overlapping or vague definitions cause misclassification.
For each theme you get its id, group, name, definition, number of answers, share of all answers, mean classifier confidence (low = vague definition) and the share of its answers that were close calls with another theme (high = the two overlap).
Suggest at most 8 concrete improvements that make themes distinct, specific and useful for a research report: merge overlapping themes, rename unclear themes, rewrite vague definitions, delete themes that only restate a cohort or have almost no answers, or add a missing theme. Only reference ids that exist. For merge, put the theme to keep in target_id and the themes to fold into it in theme_ids; give a description only if the kept theme's definition must widen to cover the merged ones.
name and description are the text that replaces the theme's name and definition. A description is one sentence starting with "Use when" that says which answers belong to the theme, never advice to the reviewer about the codebook; that goes in reason. Keep names under 6 words.
Reviewer's instruction: ${hint.trim() || 'none'}

CORPUS:
${corpus || 'no description available'}

CODEBOOK:
${lines}`;
}

// Turns a suggestion into draft edits, or explains why it can't be applied to the current codebook.
function suggestionEdits(s: Suggestion, themes: Theme[], nextId: number): Edit[] | string {
	const live = (id: number | null | undefined) =>
		themes.find((t) => t.id === id && !['deleted', 'merged'].includes(t.status ?? ''));
	const ids = s.theme_ids.length ? s.theme_ids : s.target_id != null ? [s.target_id] : [];
	if (s.action === 'add') {
		if (s.group_id == null || !s.name || !s.description) return 'Needs a group, name and definition.';
		return [{ op: 'add', id: nextId, group: s.group_id, name: s.name, description: s.description, importance: 2 }];
	}
	if (s.action === 'delete') {
		if (!ids.length || ids.some((id) => !live(id))) return 'Refers to a theme that no longer exists.';
		return ids.map((id) => ({ op: 'delete' as const, id }));
	}
	if (s.action === 'merge') {
		const target = live(s.target_id ?? ids[0]);
		const sources = ids.filter((id) => id !== target?.id);
		if (!target || !sources.length || sources.some((id) => !live(id))) return 'Refers to a theme that no longer exists.';
		const edits: Edit[] = sources.map((id) => ({ op: 'merge' as const, id, target: target.id }));
		if (s.name || s.description) {
			edits.push({
				op: 'update',
				id: target.id,
				name: s.name ?? target.name,
				description: s.description ?? target.description,
				importance: target.importance,
			});
		}
		return edits;
	}
	const t = live(s.target_id ?? ids[0]);
	if (!t) return 'Refers to a theme that no longer exists.';
	return [{ op: 'update', id: t.id, name: s.name ?? t.name, description: s.description ?? t.description, importance: t.importance }];
}

function SuggestPanel({
	themes,
	corpus,
	nextId,
	onApply,
}: {
	themes: Theme[];
	corpus: string;
	nextId: number;
	onApply: (edits: Edit[]) => void;
}) {
	const [open, setOpen] = useState(false);
	const [hint, setHint] = useState('');
	const [model, setModel] = useState('gpt-6-luna');
	const [request, setRequest] = useState<{ prompt: string; model: string; nonce: number } | null>(null);
	const [applied, setApplied] = useState<number[]>([]);
	// Runs only when the viewer asks for suggestions; prompt() is a read-only SELECT.
	const q = useSQLQuery(
		`SELECT prompt(${sqlStr(request?.prompt ?? '')}, model := ${sqlStr(request?.model ?? 'gpt-6-luna')},
       json_schema := ${sqlStr(SUGGEST_SCHEMA)})::VARCHAR AS j -- ${request?.nonce ?? 0}`,
		{ enabled: request != null },
	);
	const suggestions = useMemo((): Suggestion[] | null => {
		const raw = rowsOf(q)[0]?.j;
		if (raw == null) return null;
		try {
			return JSON.parse(S(raw)).suggestions;
		} catch {
			return null;
		}
	}, [q.data]);
	// Adds every open suggestion in order. A suggestion is skipped when it no longer applies, or
	// when it touches a theme that an earlier one in this batch deletes or merges away.
	const addAll = () => {
		const gone = new Set<number>();
		let id = nextId;
		const batch: Edit[] = [];
		const added: number[] = [];
		(suggestions ?? []).forEach((sg, i) => {
			if (applied.includes(i)) return;
			const edits = suggestionEdits(sg, themes, id);
			if (typeof edits === 'string') return;
			if (edits.some((e) => gone.has(e.id) || (e.op === 'merge' && gone.has(e.target)))) return;
			for (const e of edits) {
				if (e.op === 'delete' || e.op === 'merge') gone.add(e.id);
				if (e.op === 'add') id -= 1;
			}
			batch.push(...edits);
			added.push(i);
		});
		if (batch.length) onApply(batch);
		setApplied([...applied, ...added]);
	};
	const loading = request != null && q.isLoading;
	const field = { border: `1px solid ${C.line}`, background: 'white' };
	const name = (id: number | null) => themes.find((t) => t.id === id)?.name ?? `#${id}`;
	const title = (s: Suggestion) => {
		const ids = s.theme_ids.length ? s.theme_ids : [s.target_id];
		if (s.action === 'merge') {
			const target = s.target_id ?? ids[0];
			return `Merge ${ids.filter((id) => id !== target).map((id) => `"${name(id)}"`).join(', ')} into "${name(target)}"` + (s.name ? ` and call it "${s.name}"` : '');
		}
		if (s.action === 'add') return `Add "${s.name}"`;
		if (s.action === 'delete') return `Delete ${ids.map((id) => `"${name(id)}"`).join(', ')}`;
		if (s.action === 'rename') return `Rename "${name(s.target_id ?? ids[0])}" to "${s.name}"`;
		return `Redefine "${name(s.target_id ?? ids[0])}"`;
	};

	return (
		<div className="p-4 flex flex-col gap-3" style={panel}>
			<button onClick={() => setOpen(!open)} className="flex items-center gap-2 text-sm font-semibold text-left">
				{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
				<Sparkles size={14} color={C.accent} /> Suggest improvements with prompt()
				<span className="font-normal" style={{ color: C.muted }}>
					reads the codebook and its evidence, and proposes edits you can add to your draft
				</span>
			</button>
			{open && (
				<>
					<div className="flex gap-2 items-start text-sm">
						<textarea
							value={hint}
							onChange={(e) => setHint(e.target.value)}
							placeholder="Optional instruction, e.g. 'aim for 15 themes', 'split themes by attitude, not by cohort', 'keep the trust themes separate'"
							rows={2}
							className="flex-1 px-2 py-1 rounded"
							style={field}
						/>
						<select value={model} onChange={(e) => setModel(e.target.value)} className="px-2 py-1 rounded" style={field}>
							{MODELS.map((m) => (
								<option key={m}>{m}</option>
							))}
						</select>
						<Btn
							kind="primary"
							disabled={loading}
							onClick={() => {
								setApplied([]);
								setRequest({ prompt: suggestPrompt(themes.filter((t) => !['deleted', 'merged'].includes(t.status ?? '')), hint, corpus), model, nonce: Date.now() });
							}}
						>
							{loading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
							{suggestions ? 'Suggest again' : 'Suggest'}
						</Btn>
					</div>
					<Err q={q} />
					{request != null && !loading && suggestions == null && !q.isError && (
						<p className="text-sm" style={{ color: C.neg }}>
							The model returned no usable suggestions. Try again or pick another model.
						</p>
					)}
					{suggestions && !loading && suggestions.length > applied.length && (
						<div className="flex justify-end">
							<Btn onClick={addAll}>
								<Plus size={14} /> Add all to draft
							</Btn>
						</div>
					)}
					{suggestions && !loading && (
						<ul className="flex flex-col gap-2">
							{suggestions.map((s, i) => {
								const edits = suggestionEdits(s, themes, nextId);
								const done = applied.includes(i);
								return (
									<li key={i} className="flex gap-3 items-start text-sm" style={{ borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
										<div className="flex-1">
											<p className="font-semibold">{title(s)}</p>
											{s.description && s.action !== 'delete' && (
												<p style={{ color: C.text }}>New definition: {s.description}</p>
											)}
											<p className="text-xs mt-1" style={{ color: C.muted }}>
												{s.reason}
											</p>
										</div>
										{done ? (
											<Btn disabled onClick={() => {}}>
												Added
											</Btn>
										) : typeof edits === 'string' ? (
											<span className="text-xs" style={{ color: C.muted, maxWidth: 160 }}>
												{edits}
											</span>
										) : (
											<Btn
												onClick={() => {
													onApply(edits);
													setApplied([...applied, i]);
												}}
											>
												Add to draft
											</Btn>
										)}
									</li>
								);
							})}
						</ul>
					)}
				</>
			)}
		</div>
	);
}

function ThemeRow({
	t,
	themes,
	selected,
	editing,
	onToggle,
	onEdit,
	onCancel,
	onStage,
	onRevert,
}: {
	t: Theme;
	themes: Theme[];
	selected: boolean;
	editing: boolean;
	onToggle: () => void;
	onEdit: () => void;
	onCancel: () => void;
	onStage: (e: Edit) => void;
	onRevert: () => void;
}) {
	const gone = t.status === 'deleted' || t.status === 'merged';
	const runnerUp = themes.find((x) => x.id === t.runnerUp);

	return (
		<div
			className="grid gap-6 p-4"
			style={{
				...panel,
				gridTemplateColumns: 'minmax(0, 1.1fr) minmax(0, 1fr)',
				opacity: gone ? 0.6 : 1,
				borderColor: selected ? C.accent : C.line,
			}}
		>
			<div className="flex flex-col gap-2">
				{editing ? (
					<ThemeForm
						initial={t}
						themes={themes}
						id={t.id}
						submitLabel="Save to draft"
						onCancel={onCancel}
						onSubmit={(v) => {
							onStage({ op: 'update', id: t.id, ...v });
							onCancel();
						}}
					/>
				) : (
					<>
						<label className="flex items-center gap-2 flex-wrap" style={{ cursor: gone ? 'default' : 'pointer' }}>
							<input type="checkbox" checked={selected} disabled={gone} onChange={onToggle} style={{ accentColor: C.accent }} />
							<span className="font-semibold" style={{ textDecoration: gone ? 'line-through' : 'none' }}>
								{t.name}
							</span>
							{t.importance !== 2 && (
								<span className="text-xs uppercase" style={{ color: t.importance === 3 ? C.accent : C.muted, letterSpacing: 0.5 }}>
									{importanceName(t.importance)} importance
								</span>
							)}
							<StatusTag status={t.status} />
						</label>
						<p className="text-sm" style={{ color: C.muted }}>
							{t.description}
						</p>
						{gone ? (
							<div className="flex items-center gap-3 text-sm">
								<span style={{ color: C.muted }}>
									{t.status === 'merged'
										? `Will be merged into "${themes.find((x) => x.id === t.target)?.name}".`
										: 'Will be deleted with its insights.'}
								</span>
								<Btn onClick={onRevert}>
									<RotateCcw size={13} /> Restore
								</Btn>
							</div>
						) : (
							<div className="flex items-center gap-2 flex-wrap mt-1">
								<Btn onClick={onEdit}>
									<Pencil size={13} /> Edit
								</Btn>
								<Btn kind="danger" onClick={() => onStage({ op: 'delete', id: t.id })}>
									Delete
								</Btn>
								{t.status === 'edited' && (
									<Btn onClick={onRevert}>
										<RotateCcw size={13} /> Undo
									</Btn>
								)}
							</div>
						)}
					</>
				)}
			</div>

			<div className="flex flex-col gap-2 text-sm">
				{t.id < 0 || t.answers == null ? (
					<p style={{ color: C.muted }}>Not classified yet. It gets answers after you submit and classify.</p>
				) : (
					<>
						<p>
							<span className="font-semibold">{fmt(t.n)}</span> answers
							<span style={{ color: C.muted }}> · {((t.share ?? 0) * 100).toFixed(1)}% of all · </span>
							<span style={{ color: (t.conf ?? 1) < 0.55 ? C.accent : C.muted }}>
								confidence {(t.conf ?? 0).toFixed(2)}
								{(t.conf ?? 1) < 0.55 ? ' (low)' : ''}
							</span>
						</p>
						{runnerUp && (t.closeShare ?? 0) >= 0.1 && (
							<p className="flex items-center gap-2 flex-wrap">
								<span style={{ color: (t.closeShare ?? 0) >= 0.25 ? C.accent : C.muted }}>
									{Math.round((t.closeShare ?? 0) * 100)}% close calls with "{runnerUp.name}"
								</span>
								{!gone && !['deleted', 'merged'].includes(runnerUp.status ?? '') && (
									<button
										onClick={() => onStage({ op: 'merge', id: t.id, target: runnerUp.id })}
										className="text-xs"
										style={{ color: C.muted, textDecoration: 'underline' }}
									>
										Merge into it
									</button>
								)}
							</p>
						)}
						{(t.insights ?? []).length > 0 && (
							<ul className="text-xs flex flex-col gap-1" style={{ color: C.muted }}>
								{(t.insights ?? []).map((ins, i) => (
									<li key={i} className="flex gap-1">
										<Gem size={11} color={C.accent} style={{ flexShrink: 0, marginTop: 2 }} />
										<span>{ins}</span>
									</li>
								))}
							</ul>
						)}
						{t.quote && (
							<p className="text-xs italic" style={{ ...clamp(3), borderLeft: `2px solid ${C.line}`, paddingLeft: 8 }}>
								“{t.quote}”
							</p>
						)}
					</>
				)}
			</div>
		</div>
	);
}

function MergeBar({
	selected,
	onMerge,
	onClear,
}: {
	selected: Theme[];
	onMerge: (target: number) => void;
	onClear: () => void;
}) {
	const largest = [...selected].sort((a, b) => b.n - a.n)[0];
	const [target, setTarget] = useState<number | null>(null);
	const keep = selected.find((t) => t.id === target) ?? largest;
	return (
		<div
			className="flex items-center gap-3 px-4 py-3 text-sm"
			style={{
				position: 'fixed',
				bottom: 24,
				left: '50%',
				transform: 'translateX(-50%)',
				zIndex: 20,
				background: C.text,
				color: 'white',
				borderRadius: 12,
				boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
				maxWidth: '90%',
			}}
		>
			<span>
				<span className="font-semibold">{selected.length}</span> themes selected
			</span>
			{selected.length >= 2 ? (
				<>
					<span style={{ color: '#c9c5d1' }}>keep as</span>
					<select
						value={keep.id}
						onChange={(e) => setTarget(Number(e.target.value))}
						className="px-2 py-1 rounded"
						style={{ background: 'white', color: C.text, maxWidth: 260 }}
					>
						{selected.map((t) => (
							<option key={t.id} value={t.id}>
								{t.name}
							</option>
						))}
					</select>
					<button
						onClick={() => onMerge(keep.id)}
						className="px-3 py-1 rounded font-semibold"
						style={{ background: C.accent, color: 'white' }}
					>
						Merge {selected.length}
					</button>
				</>
			) : (
				<span style={{ color: '#c9c5d1' }}>select one more to merge</span>
			)}
			<button onClick={onClear} className="px-2 py-1 rounded" style={{ color: '#c9c5d1', textDecoration: 'underline' }}>
				Clear
			</button>
		</div>
	);
}

function Codebook() {
	const [rev, setRev] = useState(0);
	const cb = useSQLQuery(`
    WITH tot AS (SELECT count(*) AS n FROM ${DB}."turn_themes"),
    stats AS (
      SELECT theme_id, count(*) AS answers, avg(theme_conf) AS conf
      FROM ${DB}."turn_themes" WHERE theme_id IS NOT NULL GROUP BY 1
    ),
    close AS (
      SELECT theme_id, runner_up_id, count(*) AS n
      FROM ${DB}."turn_themes"
      WHERE theme_id IS NOT NULL AND runner_up_id IS NOT NULL AND runner_up_prob >= ${CLOSE_CALL}
      GROUP BY 1, 2
      QUALIFY row_number() OVER (PARTITION BY theme_id ORDER BY count(*) DESC) = 1
    ),
    ins AS (
      SELECT theme_id, string_agg(insight, chr(31) ORDER BY insight_id) AS insights
      FROM ${DB}."insights" GROUP BY 1
    ),
    quote AS (
      SELECT ti.theme_id, arg_max(tu.text, ti.quotability * ti.insight_conf) AS quote
      FROM ${DB}."turn_insights" ti JOIN ${DB}."turns" tu USING (turn_id)
      WHERE ti.insight_id IS NOT NULL GROUP BY 1
    )
    SELECT g.group_id, g.group_name, g.description AS group_description,
      t.theme_id, t.theme_name, t.description, t.importance,
      s.answers, s.answers / nullif(tot.n, 0) AS share, s.conf,
      c.runner_up_id, c.n / s.answers AS close_share, i.insights, q.quote
    FROM ${DB}."groups" g
    CROSS JOIN tot
    LEFT JOIN ${DB}."themes" t ON t.group_id = g.group_id
    LEFT JOIN stats s ON s.theme_id = t.theme_id
    LEFT JOIN close c ON c.theme_id = t.theme_id
    LEFT JOIN ins i ON i.theme_id = t.theme_id
    LEFT JOIN quote q ON q.theme_id = t.theme_id
    ORDER BY g.group_id, t.theme_id
    -- rev ${rev}
  `);
	// basis identifies the codebook a draft was made against: a new Discover run reuses theme ids.
	const stale = useSQLQuery(`
    SELECT
      (SELECT count(*) FROM ${DB}."pipeline_runs"
       WHERE phase = 'edit' AND finished_at > (
         SELECT coalesce(max(finished_at), TIMESTAMP '1970-01-01') FROM ${DB}."pipeline_runs"
         WHERE phase IN ('discover', 'classify'))) AS edits,
      (SELECT coalesce(max(finished_at)::VARCHAR, '') FROM ${DB}."pipeline_runs" WHERE phase = 'discover') AS basis,
      (SELECT details->>'limit' FROM ${DB}."pipeline_runs" WHERE phase = 'classify'
       ORDER BY finished_at DESC LIMIT 1) AS last_limit
    -- rev ${rev}
  `);
	const corpus = useSQLQuery(`SELECT summary, context FROM ${DB}."corpus" WHERE id = 1`);
	const [draft, setDraft] = useDraft(stale.isLoading ? null : S(rowsOf(stale)[0]?.basis));
	const [editing, setEditing] = useState<number | null>(null);
	const [adding, setAdding] = useState<number | null>(null);
	const [open, setOpen] = useState<Record<number, boolean>>({});
	const [selected, setSelected] = useState<number[]>([]);

	const { groups, saved } = useMemo(() => {
		const groups: { id: number; name: string; description: string }[] = [];
		const saved: Theme[] = [];
		for (const r of rowsOf(cb)) {
			const gid = N(r.group_id);
			if (!groups.some((g) => g.id === gid)) groups.push({ id: gid, name: S(r.group_name), description: S(r.group_description) });
			if (r.theme_id == null) continue;
			saved.push({
				id: N(r.theme_id),
				groupId: gid,
				group: S(r.group_name),
				name: S(r.theme_name),
				description: S(r.description),
				importance: N(r.importance) || 2,
				n: N(r.answers),
				answers: r.answers == null ? undefined : N(r.answers),
				share: N(r.share),
				conf: N(r.conf),
				runnerUp: r.runner_up_id == null ? undefined : N(r.runner_up_id),
				closeShare: N(r.close_share),
				insights: r.insights == null ? [] : S(r.insights).split('\u001f'),
				quote: S(r.quote),
			});
		}
		return { groups, saved };
	}, [cb.data]);
	const groupNames = useMemo(() => new Map(groups.map((g) => [g.id, g.name])), [groups]);
	const themes = useMemo(() => applyDraft(saved, groupNames, draft), [saved, groupNames, draft]);
	const changes = describeEdits(draft, saved, themes);
	const total = saved.reduce((sum, t) => sum + t.n, 0);
	const pick = selected.map((id) => themes.find((t) => t.id === id)).filter((t): t is Theme => !!t);

	const start = useRunFlight(FLIGHT_ID);
	const action = useRef<'submit' | 'test' | 'full'>('submit');
	const run = useFlightRun(FLIGHT_ID, start.data?.runNumber);
	const refreshing = useAfterRun(start.data?.runNumber, run.status === 'SUCCEEDED', () => {
		if (action.current === 'submit') setDraft([]);
		setRev((v) => v + 1);
	});
	const busy = start.isPending || run.isChecking || refreshing;
	const go = (a: 'submit' | 'test' | 'full', config: Record<string, string>) => {
		action.current = a;
		start.run(config);
	};
	const stageAll = (edits: Edit[]) => setDraft(edits.reduce(stageEdit, draft));
	const nextId = Math.min(0, ...draft.map((d) => d.id)) - 1;
	const merge = (target: number) => {
		stageAll(selected.filter((id) => id !== target).map((id) => ({ op: 'merge' as const, id, target })));
		setSelected([]);
		const keep = themes.find((t) => t.id === target);
		if (keep) setOpen({ ...open, [keep.groupId]: true });
		setEditing(target);
	};

	const st = rowsOf(stale)[0] ?? {};
	const step = draft.length > 0 ? 2 : N(st.edits) > 0 ? 3 : N(st.last_limit) > 0 ? 4 : 1;
	const steps = ['Edit themes', 'Submit changes', 'Test on 500 answers', 'Classify all answers'];
	const msgs = {
		submit: ['Submitting changes…', 'Changes saved. Run a test classification next.', 'Submit failed. Nothing changed; your draft is kept.'],
		test: ['Classifying 500 answers…', 'Test done. Check the numbers below, then classify all answers.', 'Test classification failed.'],
		full: ['Classifying all answers…', 'All answers classified.', 'Classification failed.'],
	}[action.current];
	const message = busy ? msgs[0] : run.status === 'SUCCEEDED' ? msgs[1] : run.isFinished ? `${msgs[2]} (run #${start.data?.runNumber})` : '';
	const allOpen = groups.length > 0 && groups.every((g) => open[g.id]);

	return (
		<div className="flex flex-col gap-4" style={{ paddingBottom: pick.length ? 80 : 0 }}>
			<p className="text-sm leading-relaxed" style={{ maxWidth: 760 }}>
				The classifier puts every answer into one of these themes by reading each theme's definition, so the
				definition is what you are really editing.
				<span style={{ color: C.muted }}>
					{' '}
					Close calls show which themes it confuses: a high share is a merge candidate, low confidence means a vague
					definition. Tick two or more themes to merge them.
				</span>
			</p>
			{S(rowsOf(corpus)[0]?.summary) && (
				<p className="text-sm leading-relaxed px-4 py-3" style={{ ...panel, color: C.muted, maxWidth: 1000 }}>
					<span className="text-xs uppercase mr-2" style={label}>
						About this corpus
					</span>
					{S(rowsOf(corpus)[0]?.summary)}
				</p>
			)}

			<div className="flex flex-col gap-3 px-4 py-3" style={{ ...panel, position: 'sticky', top: 0, zIndex: 10, borderColor: draft.length ? C.accent : C.line }}>
				<div className="flex items-center gap-4 flex-wrap text-sm">
					{steps.map((s, i) => (
						<span key={s} style={{ color: i + 1 === step ? C.text : C.muted, fontWeight: i + 1 === step ? 600 : 400 }}>
							<span style={{ color: i + 1 === step ? C.accent : C.muted }}>{i + 1}</span> {s}
						</span>
					))}
					<span className="flex-1" />
					{draft.length > 0 && (
						<Btn disabled={busy} onClick={() => setDraft([])}>
							Discard all
						</Btn>
					)}
					<Btn
						kind={step === 2 ? 'primary' : 'plain'}
						disabled={busy || draft.length === 0}
						onClick={() => go('submit', { PHASE: 'edit', EDITS: JSON.stringify(submitOrder(draft)) })}
					>
						Submit {draft.length || ''}
					</Btn>
					<Btn kind={step === 3 ? 'primary' : 'plain'} disabled={busy || draft.length > 0} onClick={() => go('test', { PHASE: 'classify', LIMIT_TURNS: '500' })}>
						Test on 500
					</Btn>
					<Btn kind={step === 4 ? 'primary' : 'plain'} disabled={busy || draft.length > 0} onClick={() => go('full', { PHASE: 'classify', LIMIT_TURNS: '0' })}>
						Classify all
					</Btn>
				</div>
				{(message || start.error || run.error) && (
					<p className="text-sm flex items-center gap-2" aria-live="polite" style={{ color: run.isFinished && run.status !== 'SUCCEEDED' ? C.neg : C.muted }}>
						{busy && <Loader2 size={14} className="animate-spin" />}
						{message}
						{start.error && <span style={{ color: C.neg }}>{startErrorText(start.error)}</span>}
						{run.error && <span style={{ color: C.neg }}>Could not check the run: {run.error.message}</span>}
					</p>
				)}
				{changes.length > 0 && (
					<ul className="text-sm flex flex-col gap-1" style={{ borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
						{changes.map((c, i) => (
							<li key={i} className="flex items-center gap-2">
								<span className="flex-1">{c.text}</span>
								<button onClick={() => setDraft(revert(draft, c.id))} className="text-xs" style={{ color: C.muted, textDecoration: 'underline' }}>
									undo
								</button>
							</li>
						))}
					</ul>
				)}
				{step === 3 && draft.length === 0 && (
					<p className="text-xs" style={{ color: C.muted }}>
						A test run classifies a random 500 answers and replaces the current results, so the numbers below cover
						those 500 until you classify all answers.
					</p>
				)}
			</div>

			<SuggestPanel themes={themes} corpus={corpusText(rowsOf(corpus)[0])} nextId={nextId} onApply={stageAll} />

			<div className="flex items-center justify-between">
				<h3 className="text-xs uppercase" style={label}>
					{groups.length} groups · {themes.filter((t) => !['deleted', 'merged'].includes(t.status ?? '')).length} themes
				</h3>
				<button
					onClick={() => setOpen(Object.fromEntries(groups.map((g) => [g.id, !allOpen])))}
					className="text-xs"
					style={{ color: C.muted, textDecoration: 'underline' }}
				>
					{allOpen ? 'Collapse all' : 'Expand all'}
				</button>
			</div>
			<Err q={cb} />
			{cb.isLoading && <Skeleton h={300} />}
			{groups.map((g) => {
				const items = themes
					.filter((t) => t.groupId === g.id)
					.sort((a, b) => b.importance - a.importance || (a.id < 0 ? 1e6 - a.id : a.id) - (b.id < 0 ? 1e6 - b.id : b.id));
				const answers = items.reduce((sum, t) => sum + (t.status === 'new' ? 0 : t.n), 0);
				const edited = items.filter((t) => t.status).length;
				return (
					<section key={g.id} className="flex flex-col gap-2">
						<div className="p-4 flex flex-col gap-2" style={panel}>
							<button onClick={() => setOpen({ ...open, [g.id]: !open[g.id] })} className="flex items-start gap-2 text-left">
								<span style={{ marginTop: 3 }}>{open[g.id] ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
								<span className="flex-1">
									<span className="font-semibold">{g.name}</span>
									<span className="block text-sm" style={{ color: C.muted }}>
										{g.description}
									</span>
								</span>
								<span className="text-sm text-right" style={{ color: C.muted, whiteSpace: 'nowrap' }}>
									{items.length} themes · {fmt(answers)} answers · {total ? ((answers / total) * 100).toFixed(0) : 0}%
									{edited > 0 && <span className="block" style={{ color: C.accent }}>{edited} changed in draft</span>}
								</span>
							</button>
							{!open[g.id] && (
								<div className="flex flex-wrap gap-2 pl-6">
									{items.map((t) => (
										<span
											key={t.id}
											className="text-xs px-2 py-1 rounded"
											style={{
												background: C.select,
												color: t.status === 'deleted' || t.status === 'merged' ? C.muted : C.text,
												textDecoration: t.status === 'deleted' || t.status === 'merged' ? 'line-through' : 'none',
											}}
										>
											{t.name} <span style={{ color: C.muted }}>{t.status === 'new' ? 'new' : fmt(t.n)}</span>
										</span>
									))}
								</div>
							)}
						</div>
						{open[g.id] && (
							<div className="flex flex-col gap-2 pl-6">
								{items.map((t) => (
									<ThemeRow
										key={t.id}
										t={t}
										themes={themes}
										selected={selected.includes(t.id)}
										editing={editing === t.id}
										onToggle={() =>
											setSelected(selected.includes(t.id) ? selected.filter((id) => id !== t.id) : [...selected, t.id])
										}
										onEdit={() => setEditing(t.id)}
										onCancel={() => setEditing(null)}
										onStage={(e) => stageAll([e])}
										onRevert={() => setDraft(revert(draft, t.id))}
									/>
								))}
								{adding === g.id ? (
									<div className="p-4" style={{ ...panel, borderColor: C.accent }}>
										<ThemeForm
											initial={{ name: '', description: '', importance: 2 }}
											themes={themes}
											submitLabel="Add to draft"
											onCancel={() => setAdding(null)}
											onSubmit={(v) => {
												stageAll([{ op: 'add', id: nextId, group: g.id, ...v }]);
												setAdding(null);
											}}
										/>
									</div>
								) : (
									<button onClick={() => setAdding(g.id)} className="flex items-center gap-1 text-sm py-1" style={{ color: C.muted }}>
										<Plus size={13} /> Add theme to {g.name}
									</button>
								)}
							</div>
						)}
					</section>
				);
			})}
			{pick.length > 0 && <MergeBar selected={pick} onMerge={merge} onClear={() => setSelected([])} />}
		</div>
	);
}

/* ------------------------------------------------------------------ categories */

function Categories({ onEdit }: { onEdit: () => void }) {
	const [themeId, setThemeId] = useDiveState<number | null>('theme', null);
	const [sub, setSub] = useDiveState<Sub>('sub', 'quotes');
	const [cohort, setCohort] = useDiveState<string>('cohort', 'all');
	const cohortList = useSQLQuery(`SELECT DISTINCT cohort FROM ${DB}."transcripts" ORDER BY 1`);
	const cohorts = ['all', ...rowsOf(cohortList).map((r) => S(r.cohort))];
	const cohortSql = cohort !== 'all' && cohorts.includes(cohort) ? `AND tu.cohort = ${sqlStr(cohort)}` : '';

	const tree = useSQLQuery(`
    SELECT g.group_id, g.group_name, t.theme_id, t.theme_name, t.description, t.importance,
      count(tt.turn_id) AS answers
    FROM ${DB}."groups" g
    LEFT JOIN ${DB}."themes" t USING (group_id)
    LEFT JOIN ${DB}."turn_themes" tt USING (theme_id)
    GROUP BY ALL ORDER BY g.group_id, t.theme_id
  `);
	const stale = useSQLQuery(`
    SELECT count(*) AS edits FROM ${DB}."pipeline_runs"
    WHERE phase = 'edit' AND finished_at > (
      SELECT coalesce(max(finished_at), TIMESTAMP '1970-01-01') FROM ${DB}."pipeline_runs"
      WHERE phase IN ('discover', 'classify'))
  `);

	const { groups, themes } = useMemo(() => {
		const groups = new Map<number, string>();
		const themes: Theme[] = [];
		for (const r of rowsOf(tree)) {
			groups.set(N(r.group_id), S(r.group_name));
			if (r.theme_id == null) continue;
			themes.push({
				id: N(r.theme_id),
				groupId: N(r.group_id),
				group: S(r.group_name),
				name: S(r.theme_name),
				description: S(r.description),
				importance: N(r.importance) || 2,
				n: N(r.answers),
			});
		}
		return { groups, themes };
	}, [tree.data]);
	const theme = themes.find((t) => t.id === themeId);
	const tabs: [Sub, string][] = [
		['summary', 'Summary'],
		['documents', 'Documents'],
		['quotes', 'Insights & quotes'],
		['metadata', 'Metadata'],
	];

	return (
		<div className="flex flex-col gap-3">
			{N(rowsOf(stale)[0]?.edits) > 0 && (
				<p className="text-sm px-1" style={{ color: C.muted }}>
					The codebook changed since the last classification. Counts, quotes and summaries still use the old themes
					until you classify again from the Codebook.
				</p>
			)}
			<div className="flex gap-5">
				<CategoryTree
					themes={themes}
					groups={[...groups.entries()]}
					loading={tree.isLoading}
					selected={themeId}
					onSelect={setThemeId}
				/>
				<div className="flex-1 flex flex-col gap-3" style={{ minWidth: 0 }}>
					<Err q={tree} />
					{!theme ? (
						<div className="p-8 text-sm" style={{ ...panel, color: C.muted }}>
							Select a theme on the left.
						</div>
					) : (
						<>
							<div className="px-5 pt-4" style={panel}>
								<div className="flex items-start justify-between gap-3">
									<div>
										<h2 className="text-xl font-semibold flex items-center gap-2">
											<Folder size={18} color={C.muted} />
											{theme.name}
										</h2>
										<p className="text-xs uppercase mt-1 mb-4" style={label}>
											in {theme.group} · {importanceName(theme.importance)} importance
										</p>
									</div>
									<Btn onClick={onEdit}>
										<Pencil size={13} /> Edit in codebook
									</Btn>
								</div>
								<div className="flex items-end justify-between">
									<div className="flex gap-6">
										{tabs.map(([key, name]) => (
											<Tab key={key} active={sub === key} onClick={() => setSub(key)}>
												{name}
											</Tab>
										))}
									</div>
									<select
										value={cohort}
										onChange={(e) => setCohort(e.target.value)}
										className="text-xs px-2 py-1 mb-2 rounded"
										style={{ border: `1px solid ${C.line}`, background: 'white' }}
									>
										{cohorts.map((c) => (
											<option key={c} value={c}>
												{c === 'all' ? 'All cohorts' : c}
											</option>
										))}
									</select>
								</div>
							</div>
							<div className="p-5" style={panel}>
								{sub === 'summary' && <ThemeSummary themeId={theme.id} cohortSql={cohortSql} />}
								{sub === 'documents' && <Documents themeId={theme.id} cohortSql={cohortSql} />}
								{sub === 'quotes' && <InsightsQuotes themeId={theme.id} cohortSql={cohortSql} />}
								{sub === 'metadata' && <Metadata themeId={theme.id} cohortSql={cohortSql} />}
							</div>
						</>
					)}
				</div>
			</div>
		</div>
	);
}

/* ------------------------------------------------------------------ root */

type View = 'overview' | 'codebook' | 'categories';

const VIEWS: [View, string][] = [
	['overview', 'Overview'],
	['codebook', 'Codebook'],
	['categories', 'Categories'],
];

function RailButton({ active, onClick, name, children }: { active: boolean; onClick: () => void; name: string; children: any }) {
	return (
		<button
			onClick={onClick}
			title={name}
			aria-label={name}
			className="flex items-center justify-center rounded"
			style={{ width: 34, height: 34, background: active ? C.select : 'transparent', color: active ? C.text : C.muted }}
		>
			{children}
		</button>
	);
}

export default function InterviewInsights() {
	const [stored, setView] = useDiveState<View>('view', 'overview');
	const view = VIEWS.some(([v]) => v === stored) ? stored : 'overview';
	const header = useSQLQuery(`
    SELECT (SELECT details->>'source' FROM ${DB}."pipeline_runs" WHERE phase = 'load' ORDER BY finished_at DESC LIMIT 1) AS name,
      (SELECT count(*) FROM ${DB}."transcripts") AS n
  `);
	const icons = { overview: <Workflow size={18} />, codebook: <BookOpen size={18} />, categories: <ListTree size={18} /> };
	return (
		<main className="flex" style={{ background: C.bg, color: C.text, minHeight: '100%' }}>
			<nav className="flex flex-col items-center gap-2 py-5" style={{ width: 52, borderRight: `1px solid ${C.line}` }}>
				{VIEWS.map(([v, name]) => (
					<RailButton key={v} active={view === v} onClick={() => setView(v)} name={name}>
						{icons[v]}
					</RailButton>
				))}
			</nav>
			<div className="flex-1 p-5" style={{ minWidth: 0 }}>
				<p className="text-sm mb-4 flex items-center gap-1" style={{ color: C.muted }}>
					{S(rowsOf(header)[0]?.name) || 'Interview insights'}
					{N(rowsOf(header)[0]?.n) > 0 && `: ${fmt(N(rowsOf(header)[0]?.n))} conversations`}
					<ChevronRight size={14} />
					<span className="font-semibold" style={{ color: C.text }}>
						{VIEWS.find(([v]) => v === view)?.[1]}
					</span>
				</p>
				{view === 'overview' && (
					<Pipeline onClassified={() => setView('categories')} onReview={() => setView('codebook')} />
				)}
				{view === 'codebook' && <Codebook />}
				{view === 'categories' && <Categories onEdit={() => setView('codebook')} />}
			</div>
		</main>
	);
}
