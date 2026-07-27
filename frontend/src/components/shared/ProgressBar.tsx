interface ProgressBarProps {
  value: number; // 0..1
  label?: string;
  tone?: "accent" | "success" | "warning" | "danger" | "info";
  showPct?: boolean;
}

const TONE: Record<NonNullable<ProgressBarProps["tone"]>, string> = {
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
};

export function ProgressBar({ value, label, tone = "accent", showPct = true }: ProgressBarProps) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div className="flex flex-col gap-1 min-w-[90px]">
      {label && <span className="text-[11px] text-neutral">{label}</span>}
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 rounded-full bg-border/60 overflow-hidden">
          <div className={`h-full rounded-full ${TONE[tone]}`} style={{ width: `${pct * 100}%` }} />
        </div>
        {showPct && <span className="text-[11px] tabular-nums text-neutral w-9 text-right">{Math.round(pct * 100)}%</span>}
      </div>
    </div>
  );
}

// Compact 0-100 impact score bar used in the Query Advisor table. The NUMBER is
// always the true 0-100 impact (honest, comparable across views). The BAR fill
// is scaled to `max` — the highest impact in the current list — so priority is
// visually obvious even when the live impact range is compressed (e.g. 21-48,
// which on an absolute 0-100 bar makes every row look ~a third full and "unsorted").
// Colour tone stays keyed to the ABSOLUTE score so a low-impact query never looks
// red just because it tops a low-impact list. Defaults to max=100 (absolute).
export function ScoreBar({ score, max = 100 }: { score: number; max?: number }) {
  const s = Math.max(0, Math.min(100, score));
  const denom = Math.max(1, max);
  const fill = Math.max(0, Math.min(100, (s / denom) * 100));
  const tone = s >= 70 ? "danger" : s >= 40 ? "warning" : "success";
  const color = tone === "danger" ? "bg-danger" : tone === "warning" ? "bg-warning" : "bg-success";
  return (
    <div className="flex items-center gap-2 min-w-[70px]" title={`Impact ${s} / 100 (bar relative to the highest-impact query shown)`}>
      <div className="h-1.5 flex-1 rounded-full bg-border/60 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${fill}%` }} />
      </div>
      <span className="text-[11px] tabular-nums text-neutral w-6 text-right">{s}</span>
    </div>
  );
}
