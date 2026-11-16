// Small MotherDuck-styled primitives (buttons, capsules, chips, fields, sliders).
import * as React from "react";
import { useState } from "react";
import { C, MONO, SANS, border, shadow } from "./theme";

export const fmtInt = (v: number) => Math.round(v).toLocaleString("en-US");
export const fmtMoney = (v: number) => {
  const a = Math.abs(v);
  const s = a >= 1e6 ? `$${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `$${(a / 1e3).toFixed(1)}K` : `$${a.toFixed(0)}`;
  return v < 0 ? `−${s}` : s;
};
export const fmtPct = (v: number, digits = 1) => `${(v * 100).toFixed(digits)}%`;
export const signed = (s: string, v: number) => (v > 0 ? `+${s}` : v < 0 ? `−${s.replace(/^[−-]/, "")}` : s);

export function SectionTitle({ children, size = 13 }: { children: React.ReactNode; size?: number }) {
  return (
    <h2 style={{ margin: 0, fontFamily: MONO, fontWeight: 400, fontSize: size, lineHeight: 1.3, textTransform: "uppercase", letterSpacing: "0.02em", color: C.ink }}>
      {children}
    </h2>
  );
}

type BtnVariant = "primary" | "secondary" | "ghost" | "sun" | "danger" | "go";
export function Btn({
  children, onClick, variant = "secondary", disabled, testId, title, small, fill,
}: {
  children: React.ReactNode; onClick?: () => void; variant?: BtnVariant; disabled?: boolean; testId?: string; title?: string; small?: boolean;
  /** Take the rest of a flex row (a fixed width whatever the label). */
  fill?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const bg = { primary: C.sky, secondary: C.white, ghost: "transparent", sun: C.sun, danger: C.watermelon, go: C.lighterGarden }[variant];
  const lift = hover && !disabled && variant !== "ghost";
  return (
    <button
      type="button"
      data-testid={testId}
      title={title}
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6, height: small ? 28 : 34,
        ...(fill ? { flex: "1 1 0", minWidth: 0, justifyContent: "center" } : {}), padding: small ? "0 10px" : "0 14px",
        border: variant === "ghost" ? "2px solid transparent" : border, borderRadius: 2, background: bg, color: C.ink,
        fontFamily: SANS, fontWeight: 400, fontSize: small ? 12 : 13, lineHeight: 1, textTransform: "uppercase", letterSpacing: "0.02em",
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.45 : 1, whiteSpace: "nowrap",
        transform: lift ? "translate(2px, -2px)" : "none", boxShadow: lift ? shadow(2) : "none",
        textDecoration: variant === "ghost" && hover ? "underline" : "none", textUnderlineOffset: "0.22em",
        transition: "transform 120ms ease-in-out, box-shadow 120ms ease-in-out",
      }}
    >
      {children}
    </button>
  );
}

export function Capsule({ children, bg = C.sun, testId }: { children: React.ReactNode; bg?: string; testId?: string }) {
  return (
    <span data-testid={testId} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "2px 8px", border, borderRadius: 2, background: bg, fontFamily: MONO, fontSize: 11, lineHeight: 1.5, textTransform: "uppercase", color: C.ink, whiteSpace: "nowrap" }}>
      {children}
    </span>
  );
}

export function Chip({ children, on, onClick, disabled }: { children: React.ReactNode; on: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      style={{ height: 28, padding: "0 10px", border, borderRadius: 2, background: on ? C.ink : C.white, color: on ? C.white : C.ink, fontFamily: SANS, fontSize: 12, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1 }}
    >
      {children}
    </button>
  );
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="grid gap-1" style={{ fontSize: 12, color: C.ink, minWidth: 0 }}>
      <span style={{ fontFamily: MONO, fontSize: 11, textTransform: "uppercase" }}>{label}{hint && <span style={{ color: C.darkGrey, textTransform: "none", fontFamily: SANS }}> · {hint}</span>}</span>
      {children}
    </label>
  );
}

export function Select({ value, onChange, options, testId, disabled }: { value: string; onChange: (v: string) => void; options: Array<[string, string]>; testId?: string; disabled?: boolean }) {
  return (
    <select
      data-testid={testId}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      style={{ height: 34, maxWidth: "100%", minWidth: 0, padding: "0 24px 0 8px", border, borderRadius: 2, background: C.white, color: C.ink, fontFamily: SANS, fontSize: 13 }}
    >
      {options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>
  );
}

/** Range slider with the value shown and editable as a number. */
export function Slider({
  label, value, min, max, step, onChange, disabled, unit = "%", hint, testId, badge,
}: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void;
  disabled?: boolean; unit?: string; hint?: string; testId?: string;
  /** Shown before the label, e.g. the auto-optimize mark on a lever the optimizer sets. */
  badge?: React.ReactNode;
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  return (
    <div className="grid gap-1" style={{ opacity: disabled ? 0.55 : 1, minWidth: 0 }}>
      <div className="flex items-center justify-between gap-2" style={{ fontSize: 12, minWidth: 0 }}>
        <span title={hint ? `${label} · ${hint}` : label} style={{ color: C.ink, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{badge}{label}{hint && <span style={{ color: C.darkGrey }}> · {hint}</span>}</span>
        <span className="inline-flex items-center gap-1" style={{ flex: "none" }}>
          <input
            aria-label={`${label} value`}
            data-testid={testId ? `${testId}-num` : undefined}
            type="number"
            value={value}
            min={min}
            max={max}
            step={step}
            disabled={disabled}
            onChange={(e) => e.target.value !== "" && onChange(clamp(Number(e.target.value)))}
            style={{ width: 58, height: 24, padding: "0 4px", border: `1.5px solid ${C.ink}`, borderRadius: 2, fontSize: 12, textAlign: "right", fontVariantNumeric: "tabular-nums", background: value !== 0 ? C.lighterSun : C.white }}
          />
          <span style={{ fontSize: 12, color: C.darkGrey, width: 12 }}>{unit}</span>
        </span>
      </div>
      <input
        aria-label={label}
        data-testid={testId}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ width: "100%", minWidth: 0, margin: 0, accentColor: C.darkSky }}
      />
    </div>
  );
}

/** `stackDelta` puts the delta callout on its own line under the label instead of wrapping after it. */
export function Kpi({ label, value, delta, deltaGood, loading, testId, stackDelta }: { label: string; value: string; delta?: string; deltaGood?: boolean | null; loading?: boolean; testId?: string; stackDelta?: boolean }) {
  return (
    <div data-testid={testId}>
      {loading ? (
        <div className="animate-pulse rounded" style={{ height: 40, width: 120, background: C.darkSnow }} />
      ) : (
        <div style={{ fontSize: 34, lineHeight: 1.15, fontWeight: 700, color: C.ink, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.01em" }}>{value}</div>
      )}
      <div className={`text-xs mt-1 flex ${stackDelta ? "flex-col items-start gap-1" : "flex-wrap items-center gap-2"}`} style={{ color: C.darkerGrey }}>
        <span style={{ fontFamily: MONO, textTransform: "uppercase", whiteSpace: "nowrap" }}>{label}</span>
        {delta && (
          <span
            data-testid={testId ? `${testId}-delta` : undefined}
            style={{ padding: "0 5px", borderRadius: 2, border: `1.5px solid ${C.ink}`, background: deltaGood == null ? C.white : deltaGood ? C.lighterGarden : C.lightWatermelon, color: C.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
          >
            {delta}
          </span>
        )}
      </div>
    </div>
  );
}
