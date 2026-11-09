// ---------------------------------------------------------------------------
// MotherDuck Dive components: form fields
// After shadcn/ui Field (ui.shadcn.com/docs/components/field): Field wraps a
// FieldLabel, a control, a FieldDescription and a FieldError; FieldGroup stacks
// fields; FieldSet groups them under a FieldLegend. Field takes orientation
// "vertical" (default), "horizontal" or "responsive" and `invalid`. Controls:
// Input, Textarea, Select (native, so it works in every Dive), ChoiceGroup
// (radio or checkbox choice cards, or compact chips).
// Data for the `form` block: { title?, description?, fields: [{ id, kind: text|
//   textarea|select|choice|multi, label, description?, placeholder?, options?:
//   [string | { value, label, description? }], required? }], submit_label? }.
// nav.submit(blockId, values) receives the answers; without it they stay local.
// ---------------------------------------------------------------------------

// @imports
import { useState } from 'react';
import { Check, ClipboardList } from 'lucide-react';
// @imports-end

const fieldControl = (t: any, invalid = false) => ({
	border: `1px solid ${invalid ? t.tones.critical.line : t.line}`,
	borderRadius: Math.min(t.radius, 6),
	background: t.panel === t.bg ? (t.name === 'Night' ? '#151a21' : '#ffffff') : t.panel,
	color: t.text,
	fontSize: 14,
	lineHeight: '20px',
	padding: '6px 10px',
	outline: 'none',
	width: '100%',
	minWidth: 0,
});

function FieldSet({ children, style }: any) {
	return (
		<fieldset className="flex min-w-0 flex-col gap-4" style={{ border: 0, padding: 0, margin: 0, ...style }}>
			{children}
		</fieldset>
	);
}

function FieldLegend({ children, variant = 'legend' }: any) {
	const t = useTheme();
	return (
		<legend className={variant === 'label' ? 'mb-1 text-sm font-medium' : 'mb-2 text-base font-semibold'} style={{ color: t.text, padding: 0, fontFamily: variant === 'label' ? t.font : t.heading }}>
			{children}
		</legend>
	);
}

function FieldGroup({ children, gap = 20 }: any) {
	return (
		<div className="flex min-w-0 flex-col" style={{ gap }}>
			{children}
		</div>
	);
}

// orientation: vertical stacks label over control; horizontal puts them side by side;
// responsive is vertical in narrow containers and horizontal from 640px.
function Field({ children, orientation = 'vertical', invalid = false, style }: any) {
	const t = useTheme();
	const cls =
		orientation === 'horizontal'
			? 'flex flex-row items-center gap-3'
			: orientation === 'responsive'
				? 'flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3'
				: 'flex flex-col gap-1.5';
	return (
		<div data-invalid={invalid || undefined} className={`${cls} min-w-0`} style={{ color: invalid ? t.tones.critical.fg : undefined, ...style }}>
			{children}
		</div>
	);
}

function FieldContent({ children }: any) {
	return <div className="flex min-w-0 flex-1 flex-col gap-0.5">{children}</div>;
}

function FieldLabel({ children, htmlFor, required, style }: any) {
	const t = useTheme();
	return (
		<label htmlFor={htmlFor} className="text-sm font-medium leading-snug" style={{ color: t.text, ...style }}>
			{children}
			{required && <span style={{ color: t.tones.critical.fg }}> *</span>}
		</label>
	);
}

function FieldTitle({ children }: any) {
	const t = useTheme();
	return (
		<div className="text-sm font-medium leading-snug" style={{ color: t.text }}>
			{children}
		</div>
	);
}

function FieldDescription({ children }: any) {
	const t = useTheme();
	return (
		<p className="text-sm leading-normal" style={{ color: t.muted, margin: 0 }}>
			{children}
		</p>
	);
}

// errors: [{ message }] or strings; empty renders nothing, several render a list.
function FieldError({ errors, children }: any) {
	const t = useTheme();
	const msgs = children ? [children] : (errors || []).map((e: any) => (typeof e === 'string' ? e : e?.message)).filter(Boolean);
	if (!msgs.length) return null;
	return (
		<div role="alert" className="text-sm" style={{ color: t.tones.critical.fg }}>
			{msgs.length === 1 ? msgs[0] : (
				<ul className="ml-4 list-disc">
					{msgs.map((m: string, i: number) => (
						<li key={i}>{m}</li>
					))}
				</ul>
			)}
		</div>
	);
}

function FieldSeparator({ children }: any) {
	const t = useTheme();
	return (
		<div className="flex items-center gap-3 text-xs" style={{ color: t.muted }}>
			<span style={{ flex: 1, borderTop: `1px solid ${t.line}` }} />
			{children}
			{children && <span style={{ flex: 1, borderTop: `1px solid ${t.line}` }} />}
		</div>
	);
}

function Input({ invalid, style, ...rest }: any) {
	const t = useTheme();
	return <input aria-invalid={invalid || undefined} {...rest} style={{ ...fieldControl(t, invalid), ...style }} />;
}

function Textarea({ invalid, style, rows = 3, ...rest }: any) {
	const t = useTheme();
	return <textarea aria-invalid={invalid || undefined} rows={rows} {...rest} style={{ ...fieldControl(t, invalid), resize: 'vertical', ...style }} />;
}

// options: [string | { value, label }]; placeholder adds an empty first option.
function Select({ options = [], placeholder, invalid, style, ...rest }: any) {
	const t = useTheme();
	return (
		<select aria-invalid={invalid || undefined} {...rest} style={{ ...fieldControl(t, invalid), width: 'auto', maxWidth: '100%', alignSelf: 'flex-start', paddingRight: 28, ...style }}>
			{placeholder != null && <option value="">{placeholder}</option>}
			{options.map((o: any) => {
				const v = typeof o === 'string' ? o : o.value;
				return (
					<option key={v} value={v}>
						{typeof o === 'string' ? o : o.label}
					</option>
				);
			})}
		</select>
	);
}

const choiceOptions = (options: any[]) => (options || []).map((o: any) => (typeof o === 'string' ? { value: o, label: o } : o));

// Radio (or, with multiple, checkbox) choices. layout "cards": shadcn's choice card, a
// bordered clickable FieldLabel with title and description; "chips": compact toggles.
function ChoiceGroup({ options, value, onChange, multiple = false, layout = 'cards', name, invalid }: any) {
	const t = useTheme();
	const opts = choiceOptions(options);
	const selected = (v: string) => (multiple ? (value || []).includes(v) : value === v);
	const toggle = (v: string) => {
		if (!multiple) return onChange(selected(v) ? '' : v);
		const cur = value || [];
		onChange(cur.includes(v) ? cur.filter((x: string) => x !== v) : [...cur, v]);
	};
	const r = Math.min(t.radius, 8);
	if (layout === 'chips') {
		return (
			<div role={multiple ? 'group' : 'radiogroup'} className="flex flex-wrap gap-1.5">
				{opts.map((o: any) => {
					const on = selected(o.value);
					return (
						<button
							key={o.value}
							type="button"
							role={multiple ? 'checkbox' : 'radio'}
							aria-checked={on}
							onClick={() => toggle(o.value)}
							className="inline-flex items-center gap-1 text-sm"
							style={{ cursor: 'pointer', padding: '4px 10px', borderRadius: t.flat ? 999 : r, border: `1px solid ${on ? t.text : invalid ? t.tones.critical.line : t.line}`, background: on ? t.text : 'transparent', color: on ? t.bg : t.text }}
						>
							{on && <Check size={12} />}
							{o.label}
						</button>
					);
				})}
			</div>
		);
	}
	return (
		<div role={multiple ? 'group' : 'radiogroup'} className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
			{opts.map((o: any) => {
				const on = selected(o.value);
				return (
					<label
						key={o.value}
						className="flex cursor-pointer items-start gap-2.5 p-3"
						style={{ borderRadius: r, border: `1px solid ${on ? (t.mono ? t.text : t.accent) : invalid ? t.tones.critical.line : t.line}`, background: on ? t.accentSoft : 'transparent' }}
					>
						<input
							type={multiple ? 'checkbox' : 'radio'}
							name={name}
							checked={on}
							onChange={() => toggle(o.value)}
							style={{ marginTop: 3, accentColor: t.mono ? t.text : t.accent }}
						/>
						<FieldContent>
							<FieldTitle>{o.label}</FieldTitle>
							{o.description && <FieldDescription>{o.description}</FieldDescription>}
						</FieldContent>
					</label>
				);
			})}
		</div>
	);
}

// A form generated from a spec: the fields of one question set, validated on submit.
function FormBlock({ block, nav }: any) {
	const t = useTheme();
	const fields = block.fields || [];
	const [values, setValues] = useState<Record<string, any>>({});
	const [tried, setTried] = useState(false);
	const [sent, setSent] = useState(false);
	const empty = (f: any) => (f.kind === 'multi' ? !(values[f.id] || []).length : !String(values[f.id] ?? '').trim());
	const errors = Object.fromEntries(fields.filter((f: any) => f.required && empty(f)).map((f: any) => [f.id, f.kind === 'choice' || f.kind === 'select' ? 'Choose an option.' : f.kind === 'multi' ? 'Choose at least one.' : 'Fill this in.']));
	const set = (id: string, v: any) => {
		setValues({ ...values, [id]: v });
		setSent(false);
	};
	const submit = () => {
		setTried(true);
		if (Object.keys(errors).length) return;
		nav?.submit?.(block.id, values);
		setSent(true);
	};
	return (
		<FieldSet>
			{block.title && <FieldLegend>{block.title}</FieldLegend>}
			{block.description && <FieldDescription>{block.description}</FieldDescription>}
			<FieldGroup>
				{fields.map((f: any) => {
					const id = `${block.id}-${f.id}`;
					const bad = tried && !!errors[f.id];
					const control =
						f.kind === 'textarea' ? (
							<Textarea id={id} value={values[f.id] || ''} placeholder={f.placeholder} invalid={bad} onChange={(e: any) => set(f.id, e.target.value)} />
						) : f.kind === 'select' ? (
							<Select id={id} value={values[f.id] || ''} options={f.options} placeholder={f.placeholder || 'Choose…'} invalid={bad} onChange={(e: any) => set(f.id, e.target.value)} />
						) : f.kind === 'choice' || f.kind === 'multi' ? (
							<ChoiceGroup name={id} options={f.options} value={values[f.id]} multiple={f.kind === 'multi'} layout={f.layout || 'cards'} invalid={bad} onChange={(v: any) => set(f.id, v)} />
						) : (
							<Input id={id} value={values[f.id] || ''} placeholder={f.placeholder} invalid={bad} onChange={(e: any) => set(f.id, e.target.value)} onKeyDown={(e: any) => e.key === 'Enter' && submit()} />
						);
					return (
						<Field key={f.id} invalid={bad}>
							<FieldLabel htmlFor={id} required={f.required}>
								{f.label}
							</FieldLabel>
							{f.description && <FieldDescription>{f.description}</FieldDescription>}
							{control}
							{bad && <FieldError>{errors[f.id]}</FieldError>}
						</Field>
					);
				})}
				<Field orientation="horizontal">
					<Button variant="primary" onClick={submit}>
						{block.submit_label || 'Submit'}
					</Button>
					{sent && (
						<span className="inline-flex items-center gap-1 text-sm" style={{ color: t.tones.positive.fg }}>
							<Check size={14} /> {nav?.submit ? 'Sent' : 'Saved on this page'}
						</span>
					)}
				</Field>
			</FieldGroup>
		</FieldSet>
	);
}

registerComponent('form', { label: 'Form', icon: ClipboardList, render: FormBlock });
