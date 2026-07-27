import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div className="card flex flex-wrap items-center gap-3 py-3">
      {children}
    </div>
  );
}

export function TimeRangeChips({
  value,
  onChange,
  options = ["1h", "6h", "24h", "7d", "30d"],
}: {
  value: string;
  onChange: (v: string) => void;
  options?: string[];
}) {
  return (
    <div className="inline-flex rounded-lg border border-border overflow-hidden">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          className={`px-2.5 py-1 text-xs font-medium transition focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60 focus:outline-none ${
            value === opt ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
          }`}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

export function Dropdown({
  label,
  value,
  onChange,
  options,
}: {
  label?: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  const fieldName = `filter-${(label ?? "dropdown").toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-neutral">
      {label && <span className="whitespace-nowrap">{label}</span>}
      <select
        id={fieldName}
        name={fieldName}
        aria-label={label ?? "filter"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-border bg-card px-2.5 py-1 text-xs text-brand-dark focus:outline-none focus:ring-1 focus:ring-accent"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Multi-select filter: a checklist popover. `value` is the list of selected
 * option values; an EMPTY list means "All" (no filter). Use for categorical
 * filters (workspace, compute, category…). Not for mutually-exclusive ones
 * (time range) — those stay single-select Dropdowns / chips.
 */
export function MultiDropdown({
  label,
  value,
  onChange,
  options,
  allLabel = "All",
}: {
  label?: string;
  value: string[];
  onChange: (v: string[]) => void;
  options: { value: string; label: string }[];
  allLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (v: string) =>
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);

  const summary =
    value.length === 0
      ? allLabel
      : value.length === 1
        ? (options.find((o) => o.value === value[0])?.label ?? value[0])
        : `${value.length} selected`;

  return (
    <div ref={ref} className="relative inline-flex items-center gap-1.5 text-xs text-neutral">
      {label && <span className="whitespace-nowrap">{label}</span>}
      <button
        type="button"
        aria-label={label ?? "filter"}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1 text-xs text-brand-dark focus:outline-none focus:ring-1 focus:ring-accent"
      >
        <span className="max-w-[160px] truncate">{summary}</span>
        <span className="text-neutral" aria-hidden>▾</span>
      </button>
      {open && (
        <div
          role="listbox"
          aria-multiselectable
          aria-label={label ?? "filter"}
          className="absolute left-0 top-full z-30 mt-1 max-h-72 w-56 overflow-auto rounded-lg border border-border bg-card p-1 shadow-lg"
        >
          <button
            type="button"
            role="option"
            aria-selected={value.length === 0}
            onClick={() => onChange([])}
            className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-surface ${value.length === 0 ? "text-accent font-medium" : "text-brand-dark"}`}
          >
            <span className="w-3" aria-hidden>{value.length === 0 ? "✓" : ""}</span>
            {allLabel}
          </button>
          <div className="my-1 h-px bg-border" />
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={value.includes(o.value)}
              onClick={() => toggle(o.value)}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-brand-dark hover:bg-surface"
            >
              <span className="w-3" aria-hidden>{value.includes(o.value) ? "✓" : ""}</span>
              <span className="truncate">{o.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function SearchBox({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="relative flex-1 min-w-[160px]">
      <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral text-xs">⌕</span>
      <input
        type="text"
        name="search"
        aria-label={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-border bg-card pl-7 pr-3 py-1 text-xs text-brand-dark placeholder:text-neutral focus:outline-none focus:ring-1 focus:ring-accent"
      />
    </div>
  );
}

export function ThresholdSlider({
  label,
  value,
  min,
  max,
  step = 1,
  unit = "",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2 text-xs text-neutral">
      <span className="whitespace-nowrap">{label}</span>
      <input
        type="range"
        name="threshold"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-28 accent-[color:rgb(var(--color-accent))]"
      />
      <span className="tabular-nums text-brand-dark w-12">
        {value}
        {unit}
      </span>
    </label>
  );
}
