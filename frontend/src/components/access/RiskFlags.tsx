import { useState } from "react";
import { fetchRisks } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { LoadingCard, ErrorCard } from "../layout/PageShell";
import { DataTable } from "../shared/DataTable";
import type { Column } from "../shared/DataTable";
import { FilterBar, Dropdown, SearchBox } from "../shared/FilterBar";
import { SeverityPill } from "../shared/Pill";
import { InfoTip } from "../shared/InfoTip";
import type { Risk } from "../../types";

// Factual risk flags — over-privilege, direct-to-user, PUBLIC/broad grants,
// admin sprawl, stale PATs, external-write. NOT AI advice: each is a
// deterministic flag with a recommended remediation action.
export function RiskFlags({ onSelectPrincipal }: { onSelectPrincipal?: (p: string) => void }) {
  const [severity, setSeverity] = useState("all");
  const [risk, setRisk] = useState("all");
  const [search, setSearch] = useState("");
  const { data, loading, error } = useApi(() => fetchRisks({ severity, risk, search }), [severity, risk, search]);
  const rows = data?.data ?? [];

  const columns: Column<Risk>[] = [
    { key: "sev", header: "Severity", align: "center", sortValue: (r) => (r.severity === "critical" ? 0 : 1), render: (r) => <SeverityPill severity={r.severity} /> },
    { key: "risk", header: "Flag", sortValue: (r) => r.risk, render: (r) => <span className="font-medium capitalize">{r.risk.replace(/-/g, " ")}</span> },
    {
      key: "principal",
      header: (
        <>
          Principal <InfoTip text="The identity the flag is raised against: a user, group, or service principal. Click one to see everything it can access." />
        </>
      ),
      sortValue: (r) => r.principal,
      render: (r) =>
        onSelectPrincipal ? (
          <button type="button" onClick={() => onSelectPrincipal(r.principal)} className="text-xs font-mono text-brand-dark hover:text-accent">
            {r.principal}
          </button>
        ) : (
          <span className="text-xs font-mono">{r.principal}</span>
        ),
    },
    { key: "detail", header: "Detail", sortValue: (r) => r.detail, render: (r) => <span className="text-xs text-neutral">{r.detail}</span> },
    { key: "action", header: "Recommended action", sortValue: (r) => r.recommended_action, render: (r) => <span className="text-xs">{r.recommended_action}</span> },
  ];

  return (
    <div className="flex flex-col gap-3">
      <FilterBar>
        <Dropdown
          label="Severity"
          value={severity}
          onChange={setSeverity}
          options={[
            { value: "all", label: "All" },
            { value: "critical", label: "Critical" },
            { value: "warning", label: "Warning" },
          ]}
        />
        <Dropdown
          label="Flag"
          value={risk}
          onChange={setRisk}
          options={[
            { value: "all", label: "All flags" },
            { value: "over-privileged", label: "Over-privileged" },
            { value: "direct-to-user", label: "Direct-to-user" },
            { value: "broad-grant", label: "Broad / PUBLIC grant" },
            { value: "admin-sprawl", label: "Admin sprawl" },
            { value: "stale-token", label: "Stale PAT" },
            { value: "external-write", label: "External write" },
          ]}
        />
        <SearchBox value={search} onChange={setSearch} placeholder="Search flags…" />
      </FilterBar>
      {loading && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && <DataTable columns={columns} rows={rows} rowKey={(r) => r.id} initialSort={{ key: "sev", dir: "asc" }} emptyMessage="No risk flags match the filters." />}
    </div>
  );
}
