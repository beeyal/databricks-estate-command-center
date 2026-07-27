import { useMemo, useState } from "react";
import { fetchWarehouses } from "../api/client";
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
import { fmtMoney, fmtNum, fmtPct, fmtSeconds, fmtGb, byNamedFirst } from "../components/shared/format";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import { WarehouseDetail } from "../components/warehouses/WarehouseDetail";
import type { WarehouseReason, WarehouseRow } from "../types";

const TYPE_STYLE: Record<string, string> = {
  Serverless: "bg-success/15 text-success",
  Pro: "bg-info/15 text-info",
  Classic: "bg-warning/15 text-warning",
};
const REASON_STYLE: Record<WarehouseReason, string> = {
  "capacity-bound": "bg-danger/15 text-danger",
  slow: "bg-warning/15 text-warning",
  spilling: "bg-accent/15 text-accent",
  idle: "bg-info/15 text-info",
};
const REASON_LABEL: Record<WarehouseReason, string> = {
  "capacity-bound": "capacity-bound",
  slow: "slow",
  spilling: "spilling",
  idle: "idle",
};
// Plain-English recommended action per watch reason, so a platform owner knows
// what to actually do — surfaced as the pill's hover tooltip.
const REASON_ACTION: Record<WarehouseReason, string> = {
  "capacity-bound": "Queries are queueing at peak (>15% of runtime waiting). Enable multi-cluster load balancing or size the warehouse up so peak demand is absorbed; consider a separate warehouse for the heaviest workload.",
  slow: "High p95 latency — investigate the slowest queries on the Query Advisor tab (full scans, spill), or size the warehouse up a tier if it's consistently under-powered for its workload.",
  spilling: "Sustained disk spill (≥25 GB) means the warehouse runs out of memory and re-reads from disk. Size up one tier, or split/optimise the heaviest queries so they fit in memory.",
  idle: "Low query volume but non-trivial cost — the warehouse is likely left running. Set (or shorten) the auto-stop timeout so it suspends when idle.",
};

const TIME_RANGES = [
  { value: "30d", label: "Last 30 days" },
  { value: "7d", label: "Last 7 days" },
  { value: "24h", label: "Last 24 hours" },
];
const RANGE_LABEL: Record<string, string> = { "30d": "30d", "7d": "7d", "24h": "24h" };

export function WarehousesMonitorPage() {
  const cur = useCurrency();
  const features = useFeatures();
  const buEnabled = useBuEnabled();
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [bu, setBu] = useState<string[]>([]);
  const [whType, setWhType] = useState<string[]>([]);
  const [why, setWhy] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState("30d");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("open");
  const [ackNonce, setAckNonce] = useState(0);
  const win = RANGE_LABEL[timeRange] ?? "30d";

  const { data, loading, error } = useApi(
    () => fetchWarehouses({
      workspace: workspace.length ? workspace.join(",") : "all",
      wh_type: whType.length ? whType.join(",") : "all",
      why: why.length ? why.join(",") : "all",
      time_range: timeRange,
      search,
      ...(features.writeback ? { status } : {}),
    }),
    [workspace.join(","), whType.join(","), why.join(","), timeRange, search, status, ackNonce],
  );

  const { data: allData } = useApi(() => fetchWarehouses({}), []);
  const workspaceOptions = useMemo(() => {
    const set = new Set((allData?.data ?? []).map((r) => r.workspace));
    return Array.from(set)
      .map((w) => ({ value: w, label: w }))
      .sort(byNamedFirst);
  }, [allData]);

  const allRows = data?.data ?? [];
  // BU is a client-side filter (the endpoint returns the full matching set here).
  const rows = bu.length ? allRows.filter((r) => r.bu && bu.includes(r.bu)) : allRows;
  const summary = data?.summary;
  const message = data?.message;
  const buOptions = useMemo(() => {
    const set = new Set(allRows.map((r) => r.bu).filter((b): b is string => !!b && b !== "—"));
    return Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true })).map((b) => ({ value: b, label: b }));
  }, [allRows]);

  const columns: Column<WarehouseRow>[] = useMemo(() => [
    { key: "warehouse", priority: "critical", header: "Warehouse", sortValue: (r) => r.warehouse, render: (r) => (
      <div className="max-w-[220px]">
        <div className="font-medium truncate" title={r.warehouse}>{r.warehouse}</div>
        {r.warehouse_id && r.warehouse_id !== r.warehouse && (
          <div className="text-xs font-mono text-neutral truncate">{r.warehouse_id}</div>
        )}
      </div>
    ) },
    { key: "workspace", priority: "medium", header: "Workspace", sortValue: (r) => r.workspace, render: (r) => <span className="text-xs">{r.workspace}</span> },
    { key: "type", priority: "high", header: "Type", align: "center", sortValue: (r) => r.type, render: (r) => (
      r.type ? <Pill className={TYPE_STYLE[r.type] ?? "bg-border/60 text-neutral"}>{r.type}{r.size ? ` · ${r.size}` : ""}</Pill> : <span className="text-xs text-neutral">—</span>
    ) },
    { key: "runs", priority: "low", header: `Runs (${win})`, align: "right", sortValue: (r) => r.runs, render: (r) => <span className="tabular-nums">{fmtNum(r.runs, { compact: true })}</span> },
    { key: "p95", priority: "high", header: "p95", align: "right", sortValue: (r) => r.p95_s, render: (r) => <span className="tabular-nums">{fmtSeconds(r.p95_s)}</span> },
    { key: "queued", priority: "high", header: "Queued", align: "right", sortValue: (r) => r.queued_ratio, render: (r) => (
      <span className={`tabular-nums ${r.queued_ratio >= 0.15 ? "text-danger font-medium" : r.queued_ratio >= 0.08 ? "text-warning" : ""}`}>{fmtPct(r.queued_ratio)}</span>
    ) },
    { key: "spill", priority: "high", header: `Spill (${win})`, align: "right", sortValue: (r) => r.spill_gb, render: (r) => (
      r.spill_gb >= 1 ? <span className={`tabular-nums ${r.spill_gb >= 25 ? "text-danger" : ""}`}>{fmtGb(r.spill_gb)}</span> : <span className="text-xs text-neutral">—</span>
    ) },
    { key: "users", priority: "low", header: `Users (${win})`, align: "right", sortValue: (r) => r.distinct_users, render: (r) => <span className="tabular-nums text-neutral">{r.distinct_users}</span> },
    { key: "cost", priority: "critical", header: (<span className="inline-flex items-center gap-1">{`Cost (${win})`}<InfoTip text="Estimated, not billed: a duration-based proxy (query-hours × ~$0.55/DBU-hour) — system.query.history exposes no billed $ per warehouse. Order-of-magnitude; Account Console Usage is the source of truth for actual warehouse cost." /></span>), align: "right", sortValue: (r) => r.cost_usd_month, render: (r) => <span className="tabular-nums">{fmtMoney(r.cost_usd_month, cur, { compact: true })}</span> },
    { key: "why", priority: "critical", header: "Watch", sortValue: (r) => r.reasons.length * 1e12 + r.cost_usd_month, render: (r) => (
      <div className="flex flex-wrap gap-1 items-center">
        {r.reasons.map((x) => (
          <span key={x} className="inline-flex items-center gap-0.5">
            <Pill className={REASON_STYLE[x]}>{REASON_LABEL[x]}</Pill>
            <InfoTip text={`${REASON_LABEL[x]}: ${REASON_ACTION[x]}`} />
          </span>
        ))}
        {r.reasons.length === 0 && <Pill className="bg-success/15 text-success">healthy</Pill>}
      </div>
    ) },
  ], [cur, win]);

  return (
    <PageShell
      title="Warehouse Monitor"
      subtitle="SQL warehouse throughput, latency, queueing and cost — where capacity is a bottleneck and where it sits idle"
    >
      <FilterBar>
            <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={workspaceOptions} allLabel="All workspaces" />
            {buEnabled && buOptions.length > 0 && (
              <MultiDropdown label="Business unit" value={bu} onChange={setBu} options={buOptions} allLabel="All BUs" />
            )}
            <MultiDropdown label="Type" value={whType} onChange={setWhType} options={[
              { value: "Serverless", label: "Serverless" },
              { value: "Pro", label: "Pro" },
              { value: "Classic", label: "Classic" },
            ]} allLabel="All types" />
            <MultiDropdown label="Watch" value={why} onChange={setWhy} options={[
              { value: "capacity-bound", label: "Capacity-bound" },
              { value: "slow", label: "Slow (high p95)" },
              { value: "spilling", label: "Spilling" },
              { value: "idle", label: "Idle spend" },
            ]} allLabel="All warehouses" />
            <Dropdown label="Time period" value={timeRange} onChange={setTimeRange} options={TIME_RANGES} />
            <SearchBox value={search} onChange={setSearch} placeholder="Search warehouse or workspace…" />
          </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={5}>
            <KpiCard label="Warehouses" value={String(summary?.num_warehouses ?? 0)} tone="neutral" info="SQL warehouses (incl. serverless SQL) matching the current filters." />
            <KpiCard label="Capacity-bound" value={String(summary?.num_capacity_bound ?? 0)} tone="danger" hint=">15% queued" info="Warehouses spending more than 15% of runtime waiting at capacity — queries queue at peak. Size up, enable multi-cluster, or shift load." />
            <KpiCard label="Idle spend" value={String(summary?.num_idle ?? 0)} tone="warning" hint="low runs, real $" info="Warehouses with very few runs but non-trivial cost — likely left running or over-provisioned. Tighten auto-stop." />
            <KpiCard label="Spilling" value={String(summary?.num_spilling ?? 0)} tone="accent" hint="≥25 GB" info="Warehouses with sustained disk spill — undersized for their workload." />
            <KpiCard label={`Total cost (${win})`} value={fmtMoney(summary?.total_cost_usd_month ?? 0, cur, { compact: true })} tone="info" hint="estimated" info={`Estimated cost of the filtered warehouses over ${win} — a duration-based proxy (query-hours × ~$0.55/DBU-hour, since query.history exposes no billed $), not actual warehouse billing. Scales with the time filter.`} />
          </KpiRow>

          {message && (
            <div className="card border-l-4 border-l-warning text-sm">
              <span className="text-warning font-medium">Live data unavailable</span>
              <span className="text-neutral ml-2">{message}</span>
            </div>
          )}

          <div className="text-xs text-neutral -mt-1 flex items-center gap-1.5">
            Click a warehouse to drill into its hourly query volume, top users and query sources.
            <InfoTip text="Metrics come from system.query.history (per-query durations, queued-at-capacity, spill) aggregated per warehouse. Cost is a list-price estimate. Serverless SQL is included; interactive-cluster SQL is on the Clusters tab." />
          </div>

          <DataTable
            columns={columns}
            updating={loading && !!data}
            rows={rows}
            rowKey={(r) => r.warehouse_id || r.warehouse}
            initialSort={{ key: "why", dir: "desc" }}
            renderExpanded={(r) => <WarehouseDetail row={r} />}
            emptyMessage={features.writeback ? ackEmptyMessage(status, "warehouses") : "No warehouses match the current filters."}
            bulkAck={features.writeback ? {
              source: "warehouse",
              metricName: "cost_usd_month",
              itemOf: (r) => ({ entityKey: r.warehouse_id, metricValue: r.cost_usd_month, acked: (r.ack_status ?? "open") !== "open" }),
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
