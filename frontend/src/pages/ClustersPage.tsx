import { useMemo, useState } from "react";
import { fetchClusters } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useCurrency, useFeatures, useBuEnabled } from "../store/appStore";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, MultiDropdown, Dropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { InfoTip } from "../components/shared/InfoTip";
import { Pill } from "../components/shared/Pill";
import { fmtMoney, fmtPct, byNamedFirst } from "../components/shared/format";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import { ClusterDetail } from "../components/clusters/ClusterDetail";
import type { ClusterRow } from "../types";

const FINDING_STYLE: Record<string, string> = {
  "no-auto-stop": "bg-danger/15 text-danger",
  "long-auto-stop": "bg-warning/15 text-warning",
  idle: "bg-info/15 text-info",
  oversized: "bg-accent/15 text-accent",
  "no-autoscale": "bg-warning/15 text-warning",
  "mem-pressure": "bg-danger/15 text-danger",
};

const FINDING_OPTIONS = [
  { value: "no-auto-stop", label: "No auto-termination" },
  { value: "idle", label: "Under-utilised" },
  { value: "oversized", label: "Oversized" },
  { value: "no-autoscale", label: "Autoscaling off" },
  { value: "long-auto-stop", label: "Long auto-stop" },
  { value: "mem-pressure", label: "Memory pressure" },
];

const TIME_RANGES = [
  { value: "7d", label: "Last 7 days" },
  { value: "14d", label: "Last 14 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "60d", label: "Last 60 days" },
  { value: "90d", label: "Last 90 days" },
];
const RANGE_LABEL: Record<string, string> = {
  "7d": "7d", "14d": "14d", "30d": "30d", "60d": "60d", "90d": "90d",
};

export function ClustersPage() {
  const cur = useCurrency();
  const features = useFeatures();
  const buEnabled = useBuEnabled();
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [bu, setBu] = useState<string[]>([]);
  const [finding, setFinding] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState("30d");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("open");
  const [ackNonce, setAckNonce] = useState(0);
  const win = RANGE_LABEL[timeRange] ?? "30d";  // window suffix for value labels

  const { data, loading, error } = useApi(
    () => fetchClusters({
      workspace: workspace.length ? workspace.join(",") : "all",
      finding: finding.length ? finding.join(",") : "all",
      time_range: timeRange,
      search,
      ...(features.writeback ? { status } : {}),
    }),
    [workspace.join(","), finding.join(","), timeRange, search, status, ackNonce],
  );

  const { data: allData } = useApi(() => fetchClusters({}), []);
  const workspaceOptions = useMemo(() => {
    const set = new Set((allData?.data ?? []).map((r) => r.workspace));
    return Array.from(set)
      .map((w) => ({ value: w, label: w }))
      .sort(byNamedFirst);
  }, [allData]);

  const allRows = data?.data ?? [];
  const rows = bu.length ? allRows.filter((r) => r.bu && bu.includes(r.bu)) : allRows;
  const summary = data?.summary;
  const message = data?.message;
  const buOptions = useMemo(() => {
    const set = new Set(allRows.map((r) => r.bu).filter((b): b is string => !!b && b !== "—"));
    return Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true })).map((b) => ({ value: b, label: b }));
  }, [allRows]);

  const columns: Column<ClusterRow>[] = useMemo(() => [
    { key: "cluster", priority: "critical", header: "Cluster", sortValue: (r) => r.cluster, render: (r) => (
      <div className="max-w-[220px]">
        <div className="font-medium truncate" title={r.cluster}>{r.cluster}</div>
        {r.purpose && <div className="text-xs text-neutral truncate">{r.purpose}</div>}
      </div>
    ) },
    { key: "workspace", priority: "medium", header: "Workspace", sortValue: (r) => r.workspace, render: (r) => <span className="text-xs">{r.workspace}</span> },
    { key: "workers", priority: "low", header: "Workers", align: "center", sortValue: (r) => r.workers_max, render: (r) => (
      <span className="text-xs tabular-nums">{r.autoscale ? `${r.workers_min}–${r.workers_max}` : `${r.workers_max}`}{!r.autoscale && <span className="text-warning"> ·fixed</span>}</span>
    ) },
    { key: "auto_stop", priority: "medium", header: "Auto-stop", align: "center", sortValue: (r) => r.auto_stop_mins, render: (r) => (
      r.auto_stop_mins === 0
        ? <span className="text-xs text-danger font-medium">off</span>
        : <span className={`text-xs tabular-nums ${r.auto_stop_mins >= 120 ? "text-warning" : "text-neutral"}`}>{r.auto_stop_mins}m</span>
    ) },
    { key: "cpu", priority: "low", header: `CPU (${win})`, align: "right", sortValue: (r) => r.cpu_mean, render: (r) => (
      <span className={`tabular-nums ${r.cpu_mean < 0.12 ? "text-danger font-medium" : r.cpu_mean < 0.2 ? "text-warning" : ""}`}>{fmtPct(r.cpu_mean)}</span>
    ) },
    { key: "cost", priority: "critical", header: (<span className="inline-flex items-center gap-1">{`Cost (${win})`}<InfoTip text="Estimated, not billed: computed from this cluster's DBU-hours × a flat $0.55/DBU-hour proxy rate (system.compute doesn't expose per-cluster $). Real per-SKU rates vary, so treat as an order-of-magnitude figure." /></span>), align: "right", sortValue: (r) => r.cost_usd_month, render: (r) => <span className="tabular-nums">{fmtMoney(r.cost_usd_month, cur, { compact: true })}</span> },
    { key: "save", priority: "high", header: `Recoverable (${win})`, align: "right", sortValue: (r) => r.potential_savings_usd_month, render: (r) => (
      r.potential_savings_usd_month > 0
        ? <span className="tabular-nums text-success">{fmtMoney(r.potential_savings_usd_month, cur, { compact: true })}</span>
        : <span className="text-xs text-neutral">—</span>
    ) },
    { key: "findings", priority: "critical", header: "Findings", sortValue: (r) => r.findings.length, render: (r) => (
      <div className="flex flex-wrap gap-1">
        {r.findings.map((f) => <Pill key={f.code} className={FINDING_STYLE[f.code] ?? "bg-border/60 text-neutral"}>{f.label}</Pill>)}
        {r.findings.length === 0 && <span className="text-xs text-neutral">—</span>}
      </div>
    ) },
  ], [cur, win]);

  return (
    <PageShell
      title="Cluster Efficiency"
      subtitle="Interactive / all-purpose clusters — idle, oversized, no auto-stop and autoscaling gaps, with estimated recoverable spend"
    >
      <FilterBar>
            <span className="inline-flex items-center gap-1">
              <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={workspaceOptions} allLabel="All workspaces" />
              {buEnabled && buOptions.length > 0 && (
                <MultiDropdown label="Business unit" value={bu} onChange={setBu} options={buOptions} allLabel="All BUs" />
              )}
              <InfoTip text="Only workspaces that HAVE an interactive/all-purpose cluster active in the selected window appear here. Job & pipeline clusters (the bulk of most estates) are excluded from this tab, so a workspace with only those won't be listed." />
            </span>
            <MultiDropdown label="Finding" value={finding} onChange={setFinding} options={FINDING_OPTIONS} allLabel="All clusters" />
            <Dropdown label="Utilisation window" value={timeRange} onChange={setTimeRange} options={TIME_RANGES} />
            <SearchBox value={search} onChange={setSearch} placeholder="Search cluster, workspace or owner…" />
          </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={5}>
            <KpiCard label="Clusters" value={String(summary?.num_clusters ?? 0)} tone="neutral" info="Interactive (all-purpose) clusters matching the current filters." />
            <KpiCard label="Idle / no auto-stop" value={String(summary?.num_idle ?? 0)} tone="danger" info="Clusters that are under-utilised or have no auto-termination — the most common source of interactive-cluster waste." />
            <KpiCard label="Oversized" value={String(summary?.num_oversized ?? 0)} tone="warning" hint="low CPU, many workers" info={`Clusters running many workers (a config property) at low mean CPU over the last ${win} — likely over-provisioned; reduce workers or node size. The worker-count side is config-based so this moves less with the window than idle/cost do.`} />
            <KpiCard label={`Cost (${win})`} value={fmtMoney(summary?.total_cost_usd_month ?? 0, cur, { compact: true })} tone="info" hint="estimated" info={`Estimated cost of the filtered interactive clusters over the selected ${win} window — DBU-hours × a flat $0.55/DBU-hour proxy (per-cluster $ isn't in system.compute), so it's an order-of-magnitude figure, not billed cost. Scales with the time filter.`} />
            <KpiCard label={`Recoverable (${win})`} value={fmtMoney(summary?.total_potential_savings_usd_month ?? 0, cur, { compact: true })} tone="success" hint="estimated" info={`Estimated recoverable spend across these clusters over ${win}. A conservative, single-lever-per-cluster estimate — confirm before acting, not a guaranteed saving.`} />
          </KpiRow>

          {message && (
            <div className="card border-l-4 border-l-warning text-sm">
              <span className="text-warning font-medium">Live data unavailable</span>
              <span className="text-neutral ml-2">{message}</span>
            </div>
          )}

          <div className="text-xs text-neutral -mt-1 flex items-center gap-1.5">
            Shows <strong>interactive / all-purpose clusters active in the window</strong> — job &amp; pipeline
            clusters are excluded (their cost lives on the Jobs tab), so this list is usually short. Click a row to drill in.
            <InfoTip text="Scope: cluster_source IN ('UI','API') with utilisation in the selected window. Most clusters in an estate are ephemeral JOB/PIPELINE clusters — those are excluded here by design and covered by the Jobs tab. Built on system.compute.clusters (config), node_timeline (CPU/memory) and billing.usage (cost). Surfaces sizing/idle/auto-stop waste — NOT per-query tuning (that telemetry isn't in system tables; SQL-warehouse query tuning is on Query Advisor)." />
          </div>

          {data?.truncated && (data?.total_active ?? 0) > 0 && (
            <div className="text-xs text-neutral -mt-1">
              Showing the top <span className="tabular-nums">{data.shown ?? rows.length}</span> interactive clusters by cost of{" "}
              <span className="tabular-nums">{data.total_active}</span> active in this window — the KPIs above cover these top clusters.
            </div>
          )}

          <DataTable
            columns={columns}
            updating={loading && !!data}
            rows={rows}
            rowKey={(r) => r.cluster_id || r.cluster}
            initialSort={{ key: "save", dir: "desc" }}
            renderExpanded={(r) => <ClusterDetail row={r} win={win} />}
            emptyMessage={features.writeback ? ackEmptyMessage(status, "clusters") : "No clusters match the current filters."}
            bulkAck={features.writeback ? {
              source: "cluster",
              metricName: "potential_savings_usd_month",
              itemOf: (r) => ({ entityKey: r.cluster_id, metricValue: r.potential_savings_usd_month, acked: (r.ack_status ?? "open") !== "open" }),
              statusOf: (r) => ({ status: r.ack_status, reopened: r.reopened }),
              onChanged: () => setAckNonce((n) => n + 1),
              status, onStatusChange: setStatus, statusCounts: data?.ack_counts,
            } : undefined}
          />
        </>
      )}
    </PageShell>
  );
}
