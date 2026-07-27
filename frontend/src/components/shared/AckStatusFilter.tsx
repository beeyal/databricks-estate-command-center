// Acknowledge/ignore status filter — the segmented control that lets a user see
// Open (default, hides acked), Acknowledged, In progress, Won't fix, or All items.
// Shared across every alert surface (Recommendations, Jobs, Warehouses, Clusters,
// Tables, Query Advisor) so the writeback UX is identical everywhere. Renders
// nothing when writeback is off. Counts (from the endpoint's ack_counts) annotate
// the acknowledged/in-progress/won't-fix chips.
const OPTIONS = [
  ["open", "Open"],
  ["acknowledged", "Acknowledged"],
  ["in_progress", "In progress"],
  ["wont_fix", "Won't fix"],
  ["all", "All"],
] as const;

// A status-aware empty-table message so a filtered-to-Acknowledged view with no
// rows reads sensibly ("Nothing acknowledged yet") instead of the generic
// "no rows match". `noun` is the row label, e.g. "jobs", "warehouses".
export function ackEmptyMessage(status: string, noun: string, singular?: string): string {
  const one = singular ?? noun.replace(/s$/, "");
  switch (status) {
    case "acknowledged": return `Nothing acknowledged yet — acknowledge a ${one} to file it here.`;
    case "wont_fix": return `Nothing marked "won't fix" yet.`;
    case "in_progress": return `Nothing marked in progress yet.`;
    case "all": return `No ${noun} match the current filters.`;
    default: return `No open ${noun} match the current filters.`;
  }
}

export function AckStatusFilter({
  status,
  onChange,
  counts,
  enabled = true,
}: {
  status: string;
  onChange: (s: string) => void;
  counts?: Record<string, number>;
  enabled?: boolean;
}) {
  if (!enabled) return null;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-xs text-neutral">Status</span>
      <div className="inline-flex rounded-lg border border-border overflow-hidden" role="group" aria-label="Acknowledge status filter">
        {OPTIONS.map(([val, label]) => {
          const n = counts?.[val];
          return (
            <button
              key={val}
              type="button"
              onClick={() => onChange(val)}
              aria-pressed={status === val}
              className={`px-2.5 py-1 text-xs font-medium transition focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60 focus:outline-none ${
                status === val ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
              }`}
            >
              {label}
              {val !== "open" && val !== "all" && n != null ? <span className="opacity-70"> · {n}</span> : null}
            </button>
          );
        })}
      </div>
    </span>
  );
}
