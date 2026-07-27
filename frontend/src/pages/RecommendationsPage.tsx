import { useEffect, useState } from "react";
import { fetchRecommendationsHub, fetchWorkspaceOptions } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { PreviewBadge } from "../components/shared/PreviewBadge";
import { PREVIEW_NOTES } from "../buildStatus";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import { FilterBar, Dropdown, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { fmtMoney, fmtPct, byNamedFirst } from "../components/shared/format";
import { InfoTip } from "../components/shared/InfoTip";
import { useCurrency, useFeatures, useAppStore, useBuEnabled, useTeamEnabled } from "../store/appStore";
import { PriorityPill, CategoryPill, EffortChip, EvidenceChips } from "../components/recommendations/RecPills";
import { RecCard } from "../components/recommendations/RecCard";
import { CostAttributionPanel } from "../components/recommendations/CostAttributionPanel";
import type { HubRec, Priority } from "../types";

const CATEGORY_OPTIONS = [
  { value: "behavioural", label: "Behavioural / enablement" },
  { value: "compute", label: "Compute" },
  { value: "storage", label: "Storage" },
  { value: "tagging", label: "Tagging & attribution" },
  { value: "genai", label: "AI / GenAI spend" },
  { value: "access", label: "Access & governance" },
  { value: "governance", label: "Governance" },
];

// Scope = the granularity a recommendation applies at. "Account" was merged into
// "Estate-wide" (they filtered identically and the catalog never distinguished
// them). Business unit / Team options only make sense when those mappings are
// configured, so they're gated on bu_enabled / team_enabled (built at runtime).
function scopeOptions(buEnabled: boolean, teamEnabled: boolean) {
  return [
    { value: "global", label: "Estate-wide" },
    { value: "workspace", label: "Workspace" },
    ...(buEnabled ? [{ value: "bu", label: "Business unit" }] : []),
    ...(teamEnabled ? [{ value: "team", label: "Team" }] : []),
    { value: "user", label: "User" },
  ];
}

const PRIORITIES: Priority[] = ["P1", "P2", "P3"];

const CATEGORY_LABEL: Record<string, string> = {
  behavioural: "Behavioural",
  compute: "Compute",
  storage: "Storage",
  tagging: "Tagging",
  genai: "AI / GenAI",
  access: "Access",
  governance: "Governance",
};

export function RecommendationsPage() {
  const cur = useCurrency();
  const features = useFeatures();
  const buEnabled = useBuEnabled();
  const teamEnabled = useTeamEnabled();
  const SCOPE_OPTIONS = scopeOptions(buEnabled, teamEnabled);
  const [view, setView] = useState<"overall" | "workspace">("overall");
  const [workspace, setWorkspace] = useState("all");
  const [priority, setPriority] = useState("all");
  const [category, setCategory] = useState<string[]>([]);
  const [scope, setScope] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  // Acknowledge/ignore status filter (writeback). Default 'open' hides acked
  // items (reopened ones still show). A bump counter forces a refetch after acking.
  const [status, setStatus] = useState("open");
  const [ackNonce, setAckNonce] = useState(0);

  // Deep-link from Overview: when a specific rec was clicked, widen the filters
  // to "all" so the target row is guaranteed to be in the list (it may be a P2,
  // a different scope, or acknowledged), then DataTable scrolls to + expands it.
  const focusRecId = useAppStore((s) => s.focusRecId);
  const clearFocusRec = useAppStore((s) => s.clearFocusRec);
  useEffect(() => {
    if (!focusRecId) return;
    setView("overall");
    setWorkspace("all");
    setPriority("all");
    setCategory([]);
    setScope([]);
    setSearch("");
    setStatus("all");
  }, [focusRecId]);
  // Clear the deep-link target on unmount so navigating away and back doesn't
  // re-force the filters-to-all (if the target rec was never actually found).
  useEffect(() => () => clearFocusRec(), [clearFocusRec]);

  // Deep-link scoped to a workspace (Overview "Top savings opportunities" rows):
  // switch to per-workspace view filtered to that workspace, clear other filters.
  const focusRecWorkspace = useAppStore((s) => s.focusRecWorkspace);
  const clearFocusRecWorkspace = useAppStore((s) => s.clearFocusRecWorkspace);
  useEffect(() => {
    if (!focusRecWorkspace) return;
    setView("workspace");
    setWorkspace(focusRecWorkspace);
    setPriority("all");
    setCategory([]);
    setScope([]);
    setSearch("");
    setStatus("all");
    clearFocusRecWorkspace();
  }, [focusRecWorkspace, clearFocusRecWorkspace]);

  const wsData = useApi(() => fetchWorkspaceOptions(), []);
  const workspaces = wsData.data?.data ?? [];

  // In per-workspace view the workspace filter is required; in overall view it
  // is forced to "all" so the estate ranking is shown.
  const effectiveWorkspace = view === "workspace" ? workspace : "all";

  const { data, loading, error } = useApi(
    () => fetchRecommendationsHub({
      workspace: effectiveWorkspace,
      priority,
      category: category.length ? category.join(",") : "all",
      scope: scope.length ? scope.join(",") : "all",
      ...(features.writeback ? { status } : {}),
    }),
    [effectiveWorkspace, priority, category.join(","), scope.join(","), status, ackNonce],
  );

  const recs = data?.data ?? [];
  const summary = data?.summary;
  const attribution = data?.attribution;

  // client-side search over title / scope / evidence
  const filtered = search
    ? recs.filter((r) => {
        const s = search.toLowerCase();
        return (
          r.title.toLowerCase().includes(s) ||
          r.scope_label.toLowerCase().includes(s) ||
          r.evidence.some((e) => e.toLowerCase().includes(s))
        );
      })
    : recs;

  const columns: Column<HubRec>[] = [
    {
      key: "priority",
      header: (
        <span className="inline-flex items-center gap-1">
          Pri
          <InfoTip text="Priority tier from P1 to P3. P1 is critical, the highest impact and most urgent plays to act on first; P3 is lowest priority." />
        </span>
      ),
      align: "center",
      sortValue: (r) => ({ P1: 0, P2: 1, P3: 2 }[r.priority]),
      render: (r) => <PriorityPill priority={r.priority} />,
    },
    {
      key: "category",
      header: "Category",
      align: "center",
      sortValue: (r) => r.category,
      render: (r) => <CategoryPill category={r.category} />,
    },
    {
      key: "title",
      header: "Recommendation",
      sortValue: (r) => r.title,
      render: (r) => (
        <div className="flex flex-col gap-1 max-w-[520px]">
          <div className="font-medium leading-snug flex items-center gap-1.5">
            {r.title}
          </div>
          <div className="text-xs text-neutral">
            <span className="font-mono">{r.scope_label}</span>
          </div>
          <EvidenceChips evidence={r.evidence} />
        </div>
      ),
    },
    {
      key: "savings",
      header: (
        <span className="inline-flex items-center gap-1">
          Est. $/mo
          <InfoTip text="Estimated dollars per month this play could recover if actioned. Plays that reduce risk or enable a capability rather than cut cost show risk or enable instead." />
        </span>
      ),
      align: "right",
      sortValue: (r) => r.est_savings_usd_month,
      render: (r) =>
        r.savings_estimatable === false ? (
          <span className="text-xs text-warning" title="Serverless jobs DBUs list ~2x classic and net cost can rise — quantify with an A/B run before migrating.">A/B required</span>
        ) : r.est_savings_usd_month > 0 ? (
          <span className="tabular-nums font-semibold text-success">{fmtMoney(r.est_savings_usd_month, cur)}</span>
        ) : (
          <span className="text-xs text-neutral">risk / enable</span>
        ),
    },
    {
      key: "effort",
      header: (
        <span className="inline-flex items-center gap-1">
          Effort
          <InfoTip text="Rough implementation effort to complete the play, rated Low, Med, or High. Use it with estimated savings to weigh quick wins against bigger projects." />
        </span>
      ),
      align: "center",
      sortValue: (r) => ({ Low: 0, Med: 1, High: 2 }[r.effort]),
      render: (r) => <EffortChip effort={r.effort} />,
    },
    {
      key: "owner",
      header: "Owner",
      sortValue: (r) => r.owner,
      render: (r) => <span className="text-xs text-neutral">{r.owner}</span>,
    },
  ];

  return (
    <PageShell
      title="Recommendations"
      subtitle="Unified AI-advisor hub — every cost-optimisation play across the estate, scored by priority and $"
    >
      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}

      {summary && (
        <KpiRow cols={5}>
          <KpiCard
            label="Addressable savings / mo"
            value={fmtMoney(summary.total_addressable_savings_usd_month, cur, { compact: true })}
            tone="success"
            hint={summary.savings_is_upper_bound ? "upper bound (est.)" : "estimated, across all plays"}
            info="A modeled UPPER BOUND on recoverable spend, not a bankable sum: it adds up every play's estimate, and some plays target the same dollars at different granularity (e.g. an estate storage play and the per-table actions under it, or Genie deflection and per-query rewrites). Treat it as the ceiling of the opportunity, then size the specific plays you'll actually run."
          />
          <KpiCard label="P1 recommendations" value={String(summary.num_p1)} tone="danger" hint="do first" info="Count of priority 1 plays, the highest impact and most urgent recommendations. Start with these to capture the most savings or reduce the most risk first." />
          <KpiCard label="Total recommendations" value={String(summary.num_recs)} tone="accent" info="Total number of recommended operator actions across the estate, spanning all priorities and categories, that match the current filters." />
          <KpiCard
            label="Untagged spend / mo"
            value={fmtMoney(summary.untagged_spend_usd_month, cur, { compact: true })}
            tone="warning"
            delta={{ text: fmtPct(summary.untagged_pct), tone: "negative" }}
            hint="cannot attribute"
            info="Dollars per month of spend that lacks cost attribution tags, so it cannot be charged back to a business unit, project, or owner. The percentage is its share of total spend."
          />
          <KpiCard
            label="Top category"
            value={summary.top_category ? CATEGORY_LABEL[summary.top_category] ?? summary.top_category : "—"}
            tone="info"
            hint="by savings"
            info="The recommendation category with the largest estimated savings, showing where the biggest cost optimization opportunity sits right now."
          />
        </KpiRow>
      )}

      {/* DEMO-ONLY note. Live mode shows no mode chrome — it just IS live. */}
      {data && data.mode === "demo" && (
        <div className="flex items-center gap-2 text-xs text-neutral">
          <PreviewBadge tip={PREVIEW_NOTES.recommendations_feed} />
          <span>Recommendation plays are illustrative in this build.</span>
        </div>
      )}

      {/* View toggle: Overall (estate ranking) vs Per-workspace */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-lg border border-border overflow-hidden">
          {(["overall", "workspace"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`px-3.5 py-1.5 text-xs font-medium transition ${
                view === v ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
              }`}
            >
              {v === "overall" ? "Overall" : "Per-workspace"}
            </button>
          ))}
        </div>
        {view === "workspace" && (
          <Dropdown
            label="Workspace"
            value={workspace}
            onChange={setWorkspace}
            options={[
              { value: "all", label: "All workspaces" },
              ...workspaces
                .map((w) => ({ value: w.workspace, label: w.workspace }))
                .sort(byNamedFirst),
            ]}
          />
        )}
        <span className="text-xs text-neutral">
          {view === "overall"
            ? "Ranked by priority then $ across the whole estate"
            : "Recs scoped to the selected workspace (its BU + estate-wide plays)"}
        </span>
      </div>

      <FilterBar>
        <span className="text-xs text-neutral">Priority</span>
        <div className="inline-flex rounded-lg border border-border overflow-hidden">
          <button
            type="button"
            onClick={() => setPriority("all")}
            className={`px-2.5 py-1 text-xs font-medium transition ${
              priority === "all" ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
            }`}
          >
            All
          </button>
          {PRIORITIES.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPriority(p)}
              className={`px-2.5 py-1 text-xs font-medium transition ${
                priority === p ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
              }`}
            >
              {p}
              {summary && <span className="opacity-70"> · {summary.priority_counts[p] ?? 0}</span>}
            </button>
          ))}
        </div>
        <MultiDropdown label="Category" value={category} onChange={setCategory} options={CATEGORY_OPTIONS} allLabel="All categories" />
        <MultiDropdown label="Scope" value={scope} onChange={setScope} options={SCOPE_OPTIONS} allLabel="All scopes" />
        <SearchBox value={search} onChange={setSearch} placeholder="Search recommendations…" />
      </FilterBar>

      {data && (
        <DataTable
          columns={columns}
          updating={loading && !!data}
          rows={filtered}
          rowKey={(r) => r.id}
          initialSort={{ key: "priority", dir: "asc" }}
          emptyMessage={features.writeback ? ackEmptyMessage(status, "recommendations", "recommendation") : "No recommendations match the current filters."}
          renderExpanded={(r) => <RecCard rec={r} />}
          bulkAck={features.writeback ? {
            source: "rec",
            metricName: "est_savings_usd_month",
            itemOf: (r) => ({ entityKey: r.id, metricValue: r.est_savings_usd_month, acked: (r.ack_status ?? "open") !== "open" }),
            statusOf: (r) => ({ status: r.ack_status, reopened: r.reopened }),
            onChanged: () => setAckNonce((n) => n + 1),
            status, onStatusChange: setStatus, statusCounts: (data as { ack_counts?: Record<string, number> } | undefined)?.ack_counts,
          } : undefined}
          focusKey={focusRecId}
          onFocusConsumed={useAppStore.getState().clearFocusRec}
        />
      )}

      {attribution && <CostAttributionPanel attribution={attribution} workspace={effectiveWorkspace} />}
    </PageShell>
  );
}
