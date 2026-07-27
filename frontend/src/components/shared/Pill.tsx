import type { ReactNode } from "react";
import type { InsightType, Status } from "../../types";

// Generic pill with an explicit colour class.
export function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`pill ${className}`}>{children}</span>;
}

// Insight-type pill — colour per SPEC §2.
const INSIGHT_STYLE: Record<InsightType, string> = {
  cluster: "bg-insight-cluster/15 text-insight-cluster",
  resize: "bg-insight-resize/15 text-insight-resize",
  rewrite: "bg-insight-rewrite/15 text-insight-rewrite",
  vacuum: "bg-insight-vacuum/15 text-insight-vacuum",
  optimize: "bg-insight-optimize/15 text-insight-optimize",
  monitor: "bg-border text-neutral",
  "convert-to-managed": "bg-insight-convert/15 text-insight-convert",
  "enable-PO": "bg-insight-enable/15 text-insight-enable",
  "enable-LC": "bg-insight-enable/15 text-insight-enable",
};

const INSIGHT_LABEL: Record<InsightType, string> = {
  cluster: "cluster",
  resize: "resize",
  rewrite: "rewrite",
  vacuum: "vacuum",
  optimize: "optimize",
  monitor: "monitor",
  "convert-to-managed": "convert",
  "enable-PO": "enable PO",
  "enable-LC": "enable LC",
};

export function InsightPill({ type }: { type: InsightType }) {
  return <span className={`pill uppercase tracking-wide ${INSIGHT_STYLE[type]}`}>{INSIGHT_LABEL[type]}</span>;
}

// Status pill — Good / Warning / Critical.
const STATUS_STYLE: Record<Status, string> = {
  Good: "bg-success/15 text-success",
  Warning: "bg-warning/15 text-warning",
  Critical: "bg-danger/15 text-danger",
  Unknown: "bg-border text-neutral",
};

export function StatusPill({ status }: { status: Status }) {
  return <span className={`pill ${STATUS_STYLE[status]}`}>{status}</span>;
}

// Severity pill — critical / warning.
export function SeverityPill({ severity }: { severity: "critical" | "warning" }) {
  const cls = severity === "critical" ? "bg-danger/15 text-danger" : "bg-warning/15 text-warning";
  return <span className={`pill capitalize ${cls}`}>{severity}</span>;
}
