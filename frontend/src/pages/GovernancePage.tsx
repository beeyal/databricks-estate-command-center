import { useMemo, useState } from "react";
import { fetchGovernance } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { FilterBar, MultiDropdown } from "../components/shared/FilterBar";
import { GovernanceScore } from "../components/governance/GovernanceScore";
import { GovernanceTileCard } from "../components/governance/GovernanceTileCard";
import { TaggingTable } from "../components/governance/TaggingTable";
import { GovernedTagsClassification } from "../components/governance/GovernedTagsClassification";
import { LastRefreshed } from "../components/shared/LastRefreshed";
import type { GovernanceTile } from "../types";

// Human labels for the admin dataset keys returned in pending_admin.
const PENDING_ADMIN_LABELS: Record<string, string> = {
  budgets: "account budgets",
  policies: "cluster policies",
  tokens: "PAT / token risk",
  alerts: "spend alerts",
};

const STATUS_OPTIONS = [
  { value: "Critical", label: "Critical" },
  { value: "Warning", label: "Warning" },
  { value: "Good", label: "Good" },
];

// Stable category ordering for grouped display.
const CATEGORY_ORDER = [
  "Cost attribution",
  "Budgets & alerts",
  "Storage optimisation",
  "Compute controls",
  "Data governance",
  "Access & identity",
];

function groupByCategory(tiles: GovernanceTile[]): [string, GovernanceTile[]][] {
  const map = new Map<string, GovernanceTile[]>();
  for (const t of tiles) {
    const cat = t.category ?? "Other";
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat)!.push(t);
  }
  const cats = [...map.keys()].sort(
    (a, b) => (CATEGORY_ORDER.indexOf(a) + 1 || 99) - (CATEGORY_ORDER.indexOf(b) + 1 || 99),
  );
  return cats.map((c) => [c, map.get(c)!]);
}

export function GovernancePage() {
  const [status, setStatus] = useState<string[]>([]);
  const { data, loading, error } = useApi(() => fetchGovernance({}), []);

  // Fetch the full report unfiltered, then filter client-side so the summary
  // score + tagging table stay stable while the tile grid narrows.
  const report = data?.data;

  const filteredTiles = useMemo(() => {
    if (!report) return [];
    if (status.length === 0) return report.tiles;
    return report.tiles.filter((t) => status.includes(t.status as string));
  }, [report, status]);

  const grouped = useMemo(() => groupByCategory(filteredTiles), [filteredTiles]);

  return (
    <PageShell
      title="Governance"
      subtitle="Cost-governance posture and controls — each tile scored Good / Warning / Critical with the specific gap and next action"
    >
      {loading && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {report && (
        <>
          {data.last_refreshed && (
            <div className="flex justify-end">
              <LastRefreshed at={data.last_refreshed} />
            </div>
          )}

          <GovernanceScore report={report} />

          {data.mode === "live" && (data.pending_admin?.length ?? 0) > 0 && (
            <div className="card border-l-4 border-l-info flex flex-col gap-1 py-3">
              <div className="text-xs text-neutral">
                {data.pending_admin!.length} admin-API control
                {data.pending_admin!.length === 1 ? "" : "s"} awaiting ingestion — run the{" "}
                <span className="font-mono text-brand-dark">finops_admin_sync</span> job to populate:{" "}
                {data.pending_admin!.map((k) => PENDING_ADMIN_LABELS[k] ?? k).join(", ")}.
              </div>
            </div>
          )}

          <FilterBar>
            <MultiDropdown label="Status" value={status} onChange={setStatus} options={STATUS_OPTIONS} allLabel="All statuses" />
            <span className="text-xs text-neutral">
              {filteredTiles.length} of {report.num_tiles} controls
            </span>
          </FilterBar>

          {grouped.map(([category, tiles]) => (
            <div key={category} className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral">{category}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {tiles.map((t) => (
                  <GovernanceTileCard key={t.metric} tile={t} />
                ))}
              </div>
            </div>
          ))}

          {filteredTiles.length === 0 && (
            <div className="card text-sm text-neutral py-8 text-center">
              No controls match the selected status.
            </div>
          )}

          <TaggingTable rows={report.tagging_by_workspace} />

          <GovernedTagsClassification />
        </>
      )}
    </PageShell>
  );
}
