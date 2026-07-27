import type { Priority, RecCategory } from "../../types";

// Priority pill — P1 (danger) / P2 (warning) / P3 (neutral).
const PRIORITY_STYLE: Record<Priority, string> = {
  P1: "bg-danger/15 text-danger",
  P2: "bg-warning/15 text-warning",
  P3: "bg-border text-neutral",
};

export function PriorityPill({ priority }: { priority: Priority }) {
  return <span className={`pill font-semibold ${PRIORITY_STYLE[priority]}`}>{priority}</span>;
}

// Category pill — colour per SPEC §2 altitude (behavioural/compute/storage/…).
const CATEGORY_STYLE: Record<RecCategory, string> = {
  behavioural: "bg-insight-rewrite/15 text-insight-rewrite",
  compute: "bg-insight-resize/15 text-insight-resize",
  storage: "bg-insight-vacuum/15 text-insight-vacuum",
  tagging: "bg-insight-cluster/15 text-insight-cluster",
  genai: "bg-info/15 text-info",
  access: "bg-danger/15 text-danger",
  governance: "bg-insight-enable/15 text-insight-enable",
};

const CATEGORY_LABEL: Record<RecCategory, string> = {
  behavioural: "behavioural",
  compute: "compute",
  storage: "storage",
  tagging: "tagging",
  genai: "ai / genai",
  access: "access",
  governance: "governance",
};

export function CategoryPill({ category }: { category: RecCategory }) {
  return (
    <span className={`pill uppercase tracking-wide ${CATEGORY_STYLE[category] ?? "bg-border text-neutral"}`}>
      {CATEGORY_LABEL[category] ?? category}
    </span>
  );
}

// Effort chip — Low (success) / Med (warning) / High (danger).
export function EffortChip({ effort }: { effort: "Low" | "Med" | "High" }) {
  const cls = effort === "Low" ? "bg-success/15 text-success" : effort === "Med" ? "bg-warning/15 text-warning" : "bg-danger/15 text-danger";
  return <span className={`pill ${cls}`}>{effort} effort</span>;
}

// Evidence chips — the metrics that fired the recommendation (e.g. "412 queries").
export function EvidenceChips({ evidence }: { evidence: string[] }) {
  if (!evidence?.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {evidence.map((e, i) => (
        <span key={i} className="pill bg-surface border border-border text-neutral tabular-nums">
          {e}
        </span>
      ))}
    </div>
  );
}
