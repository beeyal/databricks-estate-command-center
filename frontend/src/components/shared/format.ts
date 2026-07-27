// Formatting helpers shared across pages.

export interface MoneyCurrency {
  symbol: string;
  rate: number; // multiply a USD figure by this to get the display currency
}

// A non-finite input (undefined/NaN/Infinity — e.g. a numeric field the backend
// omitted) renders as an honest em-dash, never "$NaN" / "NaN%".
const DASH = "—";

// Currency-aware money formatter (SPEC §9 dual currency). Takes a USD figure and
// the active currency option; applies the FX rate + symbol.
export function fmtMoney(
  usd: number,
  cur: MoneyCurrency,
  opts: { compact?: boolean } = {},
): string {
  const n = usd * cur.rate;
  if (!Number.isFinite(n)) return DASH;
  const s = cur.symbol;
  if (opts.compact) {
    if (Math.abs(n) >= 1e6) return `${s}${(n / 1e6).toFixed(2)}M`;
    if (Math.abs(n) >= 1e3) return `${s}${(n / 1e3).toFixed(1)}K`;
    return `${s}${n.toFixed(0)}`;
  }
  return `${s}${Math.round(n).toLocaleString("en-AU")}`;
}

export function fmtNum(n: number, opts: { compact?: boolean } = {}): string {
  if (!Number.isFinite(n)) return DASH;
  if (opts.compact) {
    if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return `${Math.round(n)}`;
  }
  return Math.round(n).toLocaleString("en-AU");
}

export function fmtPct(n: number, decimals = 0): string {
  if (!Number.isFinite(n)) return DASH;
  return `${(n * 100).toFixed(decimals)}%`;
}

// Fixed-decimal formatter with the same non-finite guard as fmtNum, for raw
// numeric cells (durations, GB, ratios) that would otherwise call `.toFixed()`
// directly and throw on an undefined/null field from a degraded live query.
export function fmtFixed(n: number, decimals = 1, suffix = ""): string {
  if (!Number.isFinite(n)) return DASH;
  return `${n.toFixed(decimals)}${suffix}`;
}

// Seconds → "12.3s" (guarded). decimals defaults to 1.
export function fmtSeconds(n: number, decimals = 1): string {
  if (!Number.isFinite(n)) return DASH;
  return `${n.toFixed(decimals)}s`;
}

// Gigabytes → "25 GB" (guarded). decimals defaults to 0.
export function fmtGb(n: number, decimals = 0): string {
  if (!Number.isFinite(n)) return DASH;
  return `${n.toFixed(decimals)} GB`;
}

export function fmtBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return DASH;
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

// Dropdown option {value,label} plus a comparator that puts human-NAMED options
// before bare-ID ones (a workspace with no name in system.access.workspaces_latest
// falls back to its numeric id — those should sink to the bottom of a picker so
// the named ones read first), then alphabetical (case-insensitive, numeric-aware).
// Use as: options.slice().sort(byNamedFirst) — where each option's `label` is the
// display string. Bare-id = label is all digits (a workspace/warehouse id).
export function byNamedFirst(a: { label: string }, b: { label: string }): number {
  const aId = /^\d{6,}$/.test(a.label);
  const bId = /^\d{6,}$/.test(b.label);
  if (aId !== bId) return aId ? 1 : -1;             // named first, id-only last
  return a.label.localeCompare(b.label, undefined, { sensitivity: "base", numeric: true });
}
