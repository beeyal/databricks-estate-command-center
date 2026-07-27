import { InfoTip } from "./InfoTip";

// Two small stat primitives shared across the drill-down detail panels (were
// duplicated 5×). Both show a label (+ optional InfoTip) and a value.
//
// StatTile — a bordered card with a SEMANTIC tone token (accent/success/…).
// Metric   — an inline label/value that takes a RAW Tailwind text-* class in
//            `tone` (its callers pass e.g. "text-danger" directly).

const STAT_TONE: Record<string, string> = {
  accent: "text-accent",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  neutral: "text-brand-dark",
};

export function StatTile({ label, value, tone = "neutral", info }: {
  label: string; value: string; tone?: string; info?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface/40 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-neutral flex items-center gap-1">
        {label}
        {info && <InfoTip text={info} label={`What is ${label}?`} />}
      </div>
      <div className={`text-lg font-semibold leading-tight tabular-nums ${STAT_TONE[tone] ?? STAT_TONE.neutral}`}>{value}</div>
    </div>
  );
}

export function Metric({ label, value, tone, info }: {
  label: string; value: string; tone?: string; info?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-neutral flex items-center gap-1">
        {label}
        {info && <InfoTip text={info} label={`What is ${label}?`} />}
      </span>
      <span className={`text-sm tabular-nums font-medium ${tone ?? ""}`}>{value}</span>
    </div>
  );
}
