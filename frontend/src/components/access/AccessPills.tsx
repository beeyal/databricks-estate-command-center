import type { ReactNode } from "react";
import type { PrincipalType } from "../../types";

// Privilege pill — read-ish privileges are calm, write/all privileges are hot.
const WRITE_PRIVS = new Set(["MODIFY", "ALL PRIVILEGES", "WRITE_VOLUME", "CREATE_TABLE", "CREATE_SCHEMA"]);

export function PrivilegePill({ privilege }: { privilege: string }) {
  const hot = WRITE_PRIVS.has(privilege);
  const cls = hot ? "bg-danger/15 text-danger" : "bg-info/15 text-info";
  return <span className={`pill font-mono tracking-tight ${cls}`}>{privilege}</span>;
}

// direct vs inherited / via-group provenance.
export function ViaPill({ via, sourceGroup }: { via: "direct" | "group"; sourceGroup?: string | null }) {
  if (via === "direct") {
    return <span className="pill bg-warning/15 text-warning">direct</span>;
  }
  return (
    <span className="pill bg-border/60 text-neutral" title={sourceGroup ? `via ${sourceGroup}` : "via group"}>
      via {sourceGroup ?? "group"}
    </span>
  );
}

export function InheritedPill({ inherited, from }: { inherited: boolean; from?: string | null }) {
  if (!inherited) return <span className="pill bg-success/15 text-success">direct</span>;
  return (
    <span className="pill bg-info/15 text-info" title={from ? `inherited from ${from}` : "inherited"}>
      inherited
    </span>
  );
}

const TYPE_STYLE: Record<PrincipalType, string> = {
  user: "bg-border/60 text-neutral",
  service_principal: "bg-accent/15 text-accent",
  group: "bg-info/15 text-info",
};

const TYPE_ICON: Record<PrincipalType, string> = {
  user: "◐",
  service_principal: "▣",
  group: "◈",
};

export function PrincipalTypePill({ type }: { type: PrincipalType }) {
  return (
    <span className={`pill ${TYPE_STYLE[type]}`}>
      <span className="mr-1 opacity-70">{TYPE_ICON[type]}</span>
      {type.replace("_", " ")}
    </span>
  );
}

const SEC_STYLE: Record<string, string> = {
  catalog: "bg-insight-convert/15 text-insight-convert",
  schema: "bg-insight-resize/15 text-insight-resize",
  table: "bg-insight-rewrite/15 text-insight-rewrite",
  volume: "bg-insight-vacuum/15 text-insight-vacuum",
  function: "bg-insight-cluster/15 text-insight-cluster",
};

export function SecurableTypePill({ type }: { type: string }) {
  return <span className={`pill ${SEC_STYLE[type] ?? "bg-border/60 text-neutral"}`}>{type}</span>;
}

const POLICY_STYLE: Record<string, string> = {
  row_filter: "bg-warning/15 text-warning",
  column_mask: "bg-accent/15 text-accent",
  abac: "bg-info/15 text-info",
};

const POLICY_LABEL: Record<string, string> = {
  row_filter: "row filter",
  column_mask: "column mask",
  abac: "ABAC",
};

export function PolicyPill({ kind }: { kind: string }) {
  return <span className={`pill ${POLICY_STYLE[kind] ?? "bg-border/60 text-neutral"}`}>{POLICY_LABEL[kind] ?? kind}</span>;
}

export function EntitlementPill({ children }: { children: ReactNode }) {
  return <span className="pill bg-border/60 text-neutral font-mono tracking-tight">{children}</span>;
}
