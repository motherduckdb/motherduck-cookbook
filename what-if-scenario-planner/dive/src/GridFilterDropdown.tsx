// Excel-style column filter for the grid's header row, taken from the
// MotherDuck pivot Dive's drilldown table (ColumnFilterDropdown, its header
// filter button and its apply-on-outside-click handler). Same interaction:
// a search box, All / None, a checkbox list with arrow-key / space navigation,
// Cancel / Apply; Enter applies, Escape cancels, clicking outside applies.
// Adapted: it takes a value list instead of data rows (no numeric compare
// mode), keeps the caller's value order, styled with this app's tokens, and
// Apply needs at least one value (the side Filters panel has no "none").
import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Filter } from "lucide-react";
import { C, SANS, border, shadow } from "./theme";

const ACCENT = C.darkerSky;

export function ColumnFilterDropdown({ label, values, available, currentSelection, onApply, onCancel, applyRef, style }: {
  label: string;
  /** Every value of the column, in display order. */
  values: string[];
  /** Values present given the other columns' filters (default: all). */
  available?: Set<string>;
  currentSelection: Set<string> | null; // null = show all
  onApply: (selected: Set<string> | null) => void;
  onCancel: () => void;
  applyRef?: { current: (() => void) | null };
  style?: React.CSSProperties;
}) {
  const [search, setSearch] = useState("");
  // null means "all": start with all values selected
  const [pending, setPending] = useState<Set<string>>(() => new Set(currentSelection ?? values));
  const [hasManualSelection, setHasManualSelection] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const listRef = useRef<HTMLDivElement>(null);

  const allValues = values;
  const availableValues = useMemo(() => available ?? new Set(values), [available, values]);
  const hasCascading = !!available;

  // Values matching the search, split by availability
  const { filteredAvailable, filteredUnavailableSelected } = useMemo(() => {
    const q = search.trim().toLowerCase();
    const base = q ? allValues.filter((v) => v.toLowerCase().includes(q)) : allValues;
    return {
      filteredAvailable: base.filter((v) => availableValues.has(v)),
      filteredUnavailableSelected: base.filter((v) => !availableValues.has(v) && pending.has(v)),
    };
  }, [allValues, search, availableValues, pending]);

  // Reset highlight when search changes
  useEffect(() => setHighlightIdx(-1), [search]);

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlightIdx < 0 || !listRef.current) return;
    listRef.current.querySelectorAll("[data-filter-option]")[highlightIdx]?.scrollIntoView({ block: "nearest" });
  }, [highlightIdx]);

  const toggleValue = (val: string) => {
    setHasManualSelection(true);
    setPending((prev) => {
      const next = new Set(prev);
      if (next.has(val)) next.delete(val);
      else next.add(val);
      return next;
    });
  };

  // Unavailable values can only be deselected, never re-selected
  const toggleUnavailableValue = (val: string) => {
    setHasManualSelection(true);
    setPending((prev) => {
      if (!prev.has(val)) return prev;
      const next = new Set(prev);
      next.delete(val);
      return next;
    });
  };

  const selectAllFiltered = () => {
    setHasManualSelection(true);
    setPending((prev) => new Set([...prev, ...filteredAvailable]));
  };

  const selectNoneFiltered = () => {
    setHasManualSelection(true);
    if (!search.trim()) setPending(new Set()); // no search: clear all selections
    else setPending((prev) => {
      const next = new Set(prev);
      for (const v of filteredAvailable) next.delete(v);
      for (const v of filteredUnavailableSelected) next.delete(v);
      return next;
    });
  };

  // What Apply would set (null = no filter), or "none" when nothing is selected.
  const result = (): Set<string> | null | "none" => {
    const hasSearch = search.trim() !== "";
    let sel: Set<string>;
    if (hasSearch && !hasManualSelection) sel = new Set(filteredAvailable); // filter to just the search results
    else if (hasSearch) sel = new Set([...pending, ...filteredAvailable]); // manual selections plus the search results
    else {
      // all available values selected (and no unavailable stragglers): clear the filter
      const allAvailableSelected = filteredAvailable.length > 0 && filteredAvailable.every((v) => pending.has(v)) && filteredUnavailableSelected.length === 0;
      if (allAvailableSelected) return null;
      sel = new Set(pending);
    }
    if (!sel.size) return "none";
    return allValues.every((v) => sel.has(v)) ? null : sel;
  };
  const canApply = result() !== "none";

  const handleApply = () => {
    const r = result();
    if (r === "none") onCancel(); // nothing to show: keep the current filter
    else onApply(r);
  };

  // Register apply handler so the parent's outside click can trigger it
  useEffect(() => {
    if (applyRef) applyRef.current = handleApply;
    return () => { if (applyRef) applyRef.current = null; };
  });

  const totalNavigable = filteredAvailable.length + filteredUnavailableSelected.length;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // keep keys away from the spreadsheet's shortcuts and cell editor
    e.stopPropagation();
    const isInput = (e.target as HTMLElement).tagName === "INPUT";
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx((i) => Math.min(i + 1, totalNavigable - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === " " && highlightIdx >= 0 && !isInput) {
      e.preventDefault();
      if (highlightIdx < filteredAvailable.length) toggleValue(filteredAvailable[highlightIdx]);
      else toggleUnavailableValue(filteredUnavailableSelected[highlightIdx - filteredAvailable.length]);
    } else if (e.key === "Enter") {
      e.preventDefault();
      handleApply();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };

  const isAllFilteredSelected = filteredAvailable.every((v) => pending.has(v));
  const isNoneFilteredSelected = filteredAvailable.every((v) => !pending.has(v)) && filteredUnavailableSelected.length === 0;

  const option = (val: string, idx: number, onClick: () => void, dim: boolean) => {
    const checked = pending.has(val);
    const highlighted = idx === highlightIdx;
    return (
      <div key={(dim ? "unavail-" : "") + val} data-filter-option="true" role="option" aria-selected={checked} onClick={onClick}
        onMouseEnter={() => setHighlightIdx(idx)}
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", cursor: "pointer", fontSize: 13, opacity: dim ? 0.5 : 1, background: highlighted ? C.snow : "transparent" }}>
        <div style={{
          width: 14, height: 14, borderRadius: 2, border: `1px solid ${checked ? ACCENT : C.grey}`, background: checked ? ACCENT : C.white,
          display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
        }}>
          {checked && (
            <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          )}
        </div>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={val}>{val || <i style={{ color: C.grey }}>(empty)</i>}</span>
      </div>
    );
  };
  const smallBtn = (primary: boolean, faded: boolean): React.CSSProperties => ({
    fontSize: 11, fontWeight: 600, fontFamily: SANS, color: primary ? C.white : C.ink, background: primary ? ACCENT : C.white,
    border: `1px solid ${primary ? ACCENT : C.grey}`, borderRadius: 2, cursor: "pointer", padding: "1px 8px", opacity: faded ? 0.5 : 1,
  });
  const footBtn = (primary: boolean, disabled = false): React.CSSProperties => ({
    fontSize: 12, fontFamily: SANS, fontWeight: primary ? 600 : 400, padding: "4px 12px", borderRadius: 2, border: primary ? `1px solid ${ACCENT}` : `1px solid ${C.grey}`,
    background: primary ? ACCENT : C.white, color: primary ? C.white : C.ink, cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1,
  });

  return (
    <div data-filter-popup="true" data-testid={`grid-filter-${label.toLowerCase()}-menu`} role="dialog" aria-label={`Filter ${label}`} onKeyDown={handleKeyDown}
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: "absolute", zIndex: 30, width: 220, maxHeight: 350, display: "flex", flexDirection: "column",
        background: C.white, border, borderRadius: 2, boxShadow: shadow(4), fontFamily: SANS, color: C.ink, ...style,
      }}>
      {/* Search box */}
      <div style={{ padding: "8px 8px 4px" }}>
        <input autoFocus placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} aria-label={`Search ${label}`}
          style={{ fontSize: 12, padding: "5px 8px", border: `1px solid ${C.grey}`, borderRadius: 2, outline: "none", width: "100%", fontFamily: SANS, boxSizing: "border-box" }} />
      </div>
      {/* All / None */}
      <div style={{ display: "flex", gap: 4, padding: "2px 8px 6px", borderBottom: `1px solid ${C.darkSnow}` }}>
        <button type="button" onClick={selectAllFiltered} style={smallBtn(true, isAllFilteredSelected)}>All</button>
        <button type="button" onClick={selectNoneFiltered} style={smallBtn(false, isNoneFilteredSelected)}>None</button>
        <span style={{ fontSize: 11, color: C.darkGrey, marginLeft: "auto", alignSelf: "center" }}>
          {pending.size}/{allValues.length}{hasCascading && availableValues.size < allValues.length ? ` (${availableValues.size} avail)` : ""}
        </span>
      </div>
      {/* Scrollable option list */}
      <div ref={listRef} role="listbox" aria-multiselectable="true" style={{ flex: 1, overflowY: "auto", padding: "4px 0", minHeight: 0 }}>
        {totalNavigable === 0 && <div style={{ fontSize: 12, color: C.darkGrey, padding: "8px 12px", textAlign: "center" }}>No matches</div>}
        {filteredAvailable.map((val, i) => option(val, i, () => toggleValue(val), false))}
        {/* Unavailable but selected values: greyed out, can only deselect */}
        {filteredUnavailableSelected.length > 0 && (
          <div style={{ borderTop: `1px dashed ${C.lightGrey}`, margin: "4px 8px 2px", paddingTop: 4, fontSize: 10, color: C.darkGrey, textTransform: "uppercase", letterSpacing: "0.5px" }}>
            not in filtered data
          </div>
        )}
        {filteredUnavailableSelected.map((val, i) => option(val, filteredAvailable.length + i, () => toggleUnavailableValue(val), true))}
      </div>
      {/* Cancel / Apply */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 6, padding: "6px 8px", borderTop: `1px solid ${C.darkSnow}` }}>
        <button type="button" onClick={onCancel} style={footBtn(false)}>Cancel</button>
        <button type="button" onClick={handleApply} disabled={!canApply} title={canApply ? undefined : "Pick at least one value"} style={footBtn(true, !canApply)}>Apply</button>
      </div>
    </div>
  );
}

/** The header cell's filter button: grey funnel, filled sky when the column is filtered (as in the pivot drilldown). */
export function FilterButton({ label, active, open, onClick, style }: {
  label: string; active: boolean; open: boolean; onClick: () => void; style?: React.CSSProperties;
}) {
  return (
    <button type="button" data-filter-popup="true" data-testid={`grid-filter-${label.toLowerCase()}`} aria-label={`Filter ${label}${active ? " (filtered)" : ""}`}
      aria-haspopup="dialog" aria-expanded={open} title={active ? `${label}: filtered` : `Filter ${label}`}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      style={{
        position: "absolute", display: "flex", alignItems: "center", justifyContent: "center", padding: 0, cursor: "pointer", borderRadius: 2,
        border: active ? `1px solid ${ACCENT}` : "1px solid transparent", background: active ? C.lightestSky : open ? C.darkSnow : "transparent",
        color: active ? ACCENT : C.darkGrey, ...style,
      }}>
      <Filter size={11} fill={active ? ACCENT : "none"} />
    </button>
  );
}

/** While a filter popup is open, a mousedown outside it applies it (or closes it). */
export function useApplyOnOutsideClick(open: boolean, applyRef: { current: (() => void) | null }, close: () => void) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (!t.closest?.("[data-filter-popup]")) {
        if (applyRef.current) applyRef.current();
        else close();
      }
    };
    document.addEventListener("mousedown", handler, true);
    return () => document.removeEventListener("mousedown", handler, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}
