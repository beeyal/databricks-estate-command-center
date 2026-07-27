import type { QueryFlag } from "../../types";

// Human labels + tone per flag. Tone drives the pill colour so severe flags
// (spill / capacity) read hotter than layout hints.
const FLAG_META: Record<QueryFlag, { label: string; tone: "danger" | "warning" | "info" }> = {
  slow: { label: "slow", tone: "warning" },
  "high-spill": { label: "high spill", tone: "danger" },
  "capacity-bound": { label: "capacity-bound", tone: "danger" },
  "full-scan": { label: "full scan", tone: "warning" },
};

const TONE_CLS = {
  danger: "bg-danger/15 text-danger",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
} as const;

export function FlagChip({ flag }: { flag: QueryFlag }) {
  const meta = FLAG_META[flag] ?? { label: flag, tone: "warning" as const };
  return <span className={`pill ${TONE_CLS[meta.tone]}`}>{meta.label}</span>;
}

export function QueryFlags({ flags }: { flags: QueryFlag[] }) {
  if (!flags.length) return <span className="text-neutral text-xs">—</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {flags.map((f) => (
        <FlagChip key={f} flag={f} />
      ))}
    </div>
  );
}

// Compact count badge for the dense table column.
export function FlagCount({ flags }: { flags: QueryFlag[] }) {
  if (!flags.length) return <span className="text-neutral text-xs">0</span>;
  const hot = flags.some((f) => f === "high-spill" || f === "capacity-bound");
  const cls = hot ? "bg-danger/15 text-danger" : "bg-warning/15 text-warning";
  return <span className={`pill ${cls} tabular-nums`}>{flags.length}</span>;
}
