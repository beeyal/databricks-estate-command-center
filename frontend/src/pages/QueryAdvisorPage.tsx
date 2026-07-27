import { useMemo, useState } from "react";
import { fetchQueries } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, TimeRangeChips, MultiDropdown, SearchBox, ThresholdSlider } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { InfoTip } from "../components/shared/InfoTip";
import { KpiRow } from "../components/kpi/KpiRow";
import { Pill } from "../components/shared/Pill";
import { ScoreBar } from "../components/shared/ProgressBar";
import { QueryInsightCell } from "../components/queries/QueryInsightCell";
import { QueryDetail } from "../components/queries/QueryDetail";
import { FlagCount } from "../components/queries/QueryFlags";
import { fmtMoney, fmtSeconds, fmtNum, fmtGb, byNamedFirst } from "../components/shared/format";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import { useAppStore, useCurrency, useFeatures, useBuEnabled } from "../store/appStore";
import type { QueryRow } from "../types";

// Only flags derivable from system.query.history. skew / small-files /
// cross-region aren't in query.history, so they're not offered.
const FLAGS = ["slow", "high-spill", "capacity-bound", "full-scan"];
const INSIGHT_TYPES = ["cluster", "resize", "rewrite", "vacuum", "optimize", "monitor"];

// Threshold where a query counts as "Critical" (impact score 0–100).
const CRITICAL_IMPACT = 70;

export function QueryAdvisorPage() {
  const cur = useCurrency();
  const features = useFeatures();
  const buEnabled = useBuEnabled();
  const globalWorkspace = useAppStore((s) => s.workspace);
  const [ackNonce, setAckNonce] = useState(0);
  const [timeRange, setTimeRange] = useState("24h");
  const [warehouse, setWarehouse] = useState<string[]>([]);
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [bu, setBu] = useState<string[]>([]);
  const [flag, setFlag] = useState<string[]>([]);
  const [insightType, setInsightType] = useState<string[]>([]);
  const [p95, setP95] = useState(0);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("open");

  // Effective workspace: page filter falls back to the global nav filter.
  const effWorkspace = workspace.length ? workspace.join(",") : globalWorkspace;

  const { data, loading, error } = useApi(
    () =>
      fetchQueries({
        time_range: timeRange,
        warehouse: warehouse.length ? warehouse.join(",") : "all",
        workspace: effWorkspace,
        flag: flag.length ? flag.join(",") : "all",
        insight_type: insightType.length ? insightType.join(",") : "all",
        p95_threshold: p95,
        search,
        ...(features.writeback ? { status } : {}),
      }),
    [timeRange, warehouse.join(","), effWorkspace, flag.join(","), insightType.join(","), p95, search, status, ackNonce],
  );

  // Unfiltered option lists (time-range-only) so dropdowns stay stable while
  // the user narrows other filters.
  const { data: allData } = useApi(() => fetchQueries({ time_range: timeRange }), [timeRange]);
  // Named-first string comparator: bare-id values (warehouses/workspaces with no
  // friendly name) sink below the named ones, then alphabetical.
  const _byName = (a: string, b: string) => byNamedFirst({ label: a }, { label: b });
  const warehouseOptions = useMemo(() => {
    const set = new Set((allData?.data ?? []).map((r) => r.warehouse));
    return Array.from(set).sort(_byName);
  }, [allData]);
  const workspaceOptions = useMemo(() => {
    const set = new Set((allData?.data ?? []).map((r) => r.workspace));
    return Array.from(set).sort(_byName);
  }, [allData]);

  const allRows = data?.data ?? [];
  const rows = bu.length ? allRows.filter((r) => r.bu && bu.includes(r.bu)) : allRows;
  const buOptions = useMemo(() => {
    const set = new Set(allRows.map((r) => r.bu).filter((b): b is string => !!b && b !== "—"));
    return Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true })).map((b) => ({ value: b, label: b }));
  }, [allRows]);
  // Stable impact rank (1 = highest impact), precomputed once. The "#" column
  // renders this directly — NOT rows.indexOf(r), which reflected the unsorted
  // server order (so the rank scrambled once the user sorted by another column)
  // and was O(n²).
  const impactRank = useMemo(() => {
    const m = new Map<number, number>();
    [...rows].sort((a, b) => b.impact - a.impact).forEach((r, i) => m.set(r.id, i + 1));
    return m;
  }, [rows]);
  // Highest impact in the current list — the ScoreBar fills relative to this so
  // the priority ranking is visually obvious even when the impact range is narrow.
  const maxImpact = useMemo(() => rows.reduce((m, r) => Math.max(m, r.impact), 0), [rows]);
  const kpis = {
    runs: rows.reduce((s, r) => s + r.runs, 0),
    critical: rows.filter((r) => r.impact >= CRITICAL_IMPACT).length,
    computeH: rows.reduce((s, r) => s + (r.p95_s * r.runs) / 3600, 0),
    cost: rows.reduce((s, r) => s + r.cost_usd, 0),
    savings: rows.reduce((s, r) => s + r.est_savings_usd_month, 0),
    biggestSpill: rows.reduce((m, r) => Math.max(m, r.spill_gb), 0),
  };

  const columns: Column<QueryRow>[] = [
    {
      key: "idx",
      priority: "high",
      header: "#",
      align: "right",
      // Sort by the precomputed impact rank (ascending rank = highest impact
      // first), so clicking "#" orders by rank rather than duplicating Impact's
      // raw-value sort.
      sortValue: (r) => impactRank.get(r.id) ?? 0,
      render: (r) => <span className="text-[11px] tabular-nums text-neutral">{impactRank.get(r.id) ?? "—"}</span>,
    },
    {
      key: "impact",
      priority: "critical",
      header: (
        <span className="inline-flex items-center gap-1">
          Impact
          <InfoTip text="Ranking from 0 to 100 combining query cost, run frequency, and severity. Higher means a bigger savings opportunity." />
        </span>
      ),
      sortValue: (r) => r.impact,
      render: (r) => <ScoreBar score={r.impact} max={maxImpact} />,
    },
    {
      key: "query",
      priority: "critical",
      header: "Query",
      sortValue: (r) => r.query_text,
      render: (r) => (
        <code className="text-xs font-mono text-brand-dark line-clamp-1 max-w-[260px] inline-block truncate align-middle">
          {r.query_text}
        </code>
      ),
    },
    {
      key: "insight",
      priority: "critical",
      header: "AI Insight",
      sortValue: (r) => r.insight_type,
      render: (r) => <QueryInsightCell row={r} />,
    },
    { key: "src", priority: "low", header: (<span className="inline-flex items-center gap-1">Src<InfoTip text="Query source: how the query reached the warehouse — e.g. SQL (SQL editor / warehouse), Notebook, Job/Scheduled, DBSQL dashboard, or BI/JDBC-ODBC (external tool like Power BI or Tableau). Helps tell interactive from automated workloads." /></span>), align: "center", sortValue: (r) => r.src, render: (r) => <Pill className="bg-border/60 text-neutral">{r.src}</Pill> },
    { key: "warehouse", priority: "medium", header: "Warehouse", sortValue: (r) => r.warehouse, render: (r) => (
      r.warehouse_id && r.warehouse_id !== r.warehouse
        ? <div><div className="text-xs font-medium">{r.warehouse}</div><div className="text-[11px] font-mono text-neutral">{r.warehouse_id}</div></div>
        : <span className="text-xs font-mono">{r.warehouse}</span>
    ) },
    { key: "user", priority: "medium", header: "User", sortValue: (r) => r.user, render: (r) => <span className="text-xs text-neutral">{r.user}</span> },
    { key: "runs", priority: "low", header: "Runs", align: "right", sortValue: (r) => r.runs, render: (r) => <span className="tabular-nums">{r.runs.toLocaleString("en-AU")}</span> },
    {
      key: "p95",
      priority: "low",
      header: (
        <span className="inline-flex items-center gap-1">
          p95
          <InfoTip text="95th-percentile query duration in seconds: 95 percent of runs finish faster. It captures the slow-run tail, not the typical run." />
        </span>
      ),
      align: "right",
      sortValue: (r) => r.p95_s,
      render: (r) => <span className="tabular-nums">{fmtSeconds(r.p95_s)}</span>,
    },
    { key: "cost", priority: "critical", header: "Cost", align: "right", sortValue: (r) => r.cost_usd, render: (r) => <span className="tabular-nums">{fmtMoney(r.cost_usd, cur)}</span> },
    {
      key: "flags",
      priority: "high",
      header: (
        <span className="inline-flex items-center gap-1">
          Flags
          <InfoTip text="How many diagnostic symptoms this query tripped (slow tail, disk spill, warehouse queueing, low-pruning full scan) — a count of what's wrong, measured from query.history. It's independent of the AI Insight, which is the single recommended fix. A query can trip a flag yet show 'monitor' when the symptom has no per-query fix (e.g. queueing is a warehouse-sizing issue); hover the AI Insight for why." />
        </span>
      ),
      align: "center",
      sortValue: (r) => r.flags.length,
      render: (r) => <FlagCount flags={r.flags} />,
    },
  ];

  return (
    <PageShell
      title="Query Advisor"
      subtitle="AI-recommended query and warehouse optimisations — query observability."
    >
      {/* KPIs gated on data so a fresh load doesn't flash real-looking zeros
          next to the LoadingCard (they compute from `rows = data?.data ?? []`). */}
      {data && (
        <KpiRow cols={5}>
          <KpiCard
            label="Runs"
            value={kpis.runs.toLocaleString("en-AU")}
            tone="neutral"
            info="Total number of times the queries in this view were executed over the selected time range."
          />
          <KpiCard
            label="Critical"
            value={String(kpis.critical)}
            tone="danger"
            hint={`impact ≥ ${CRITICAL_IMPACT}`}
            info={`Queries whose impact score is at least ${CRITICAL_IMPACT} out of 100. Impact ranks cost, run frequency, and severity, so these are the top savings targets.`}
          />
          <KpiCard
            label="Compute-h"
            value={fmtNum(kpis.computeH)}
            tone="info"
            info="Estimated compute hours these queries consumed, from 95th-percentile duration multiplied by run count. Higher means more warehouse time spent."
          />
          <KpiCard
            label="Est. cost"
            value={fmtMoney(kpis.cost, cur, { compact: true })}
            tone="accent"
            info="Estimated spend on these queries over the selected time range, based on run time and disk spill. Shown in your selected currency."
          />
          <KpiCard
            label="Biggest spill"
            value={fmtGb(kpis.biggestSpill)}
            tone="warning"
            info="Largest volume of data any single query spilled from memory to disk. Spill signals the warehouse is undersized or the query is inefficient."
          />
        </KpiRow>
      )}

      <FilterBar>
        <TimeRangeChips value={timeRange} onChange={setTimeRange} options={["1h", "6h", "24h", "7d"]} />
        <MultiDropdown
          label="Warehouses"
          value={warehouse}
          onChange={setWarehouse}
          options={warehouseOptions.map((w) => ({ value: w, label: w }))}
          allLabel="All warehouses"
        />
        <MultiDropdown
          label="Workspaces"
          value={workspace}
          onChange={setWorkspace}
          options={workspaceOptions.map((w) => ({ value: w, label: w }))}
          allLabel="All workspaces"
        />
        {buEnabled && buOptions.length > 0 && (
          <MultiDropdown label="Business unit" value={bu} onChange={setBu} options={buOptions} allLabel="All BUs" />
        )}
        <MultiDropdown
          label="Insight"
          value={insightType}
          onChange={setInsightType}
          options={INSIGHT_TYPES.map((t) => ({ value: t, label: t }))}
          allLabel="All insights"
        />
        <MultiDropdown label="Flags" value={flag} onChange={setFlag} options={FLAGS.map((f) => ({ value: f, label: f }))} allLabel="All flags" />
        <ThresholdSlider label="p95 ≥" value={p95} min={0} max={120} step={5} unit="s" onChange={setP95} />
        <SearchBox value={search} onChange={setSearch} placeholder="Search queries, users, warehouses…" />
      </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <div className="flex items-center justify-between text-xs text-neutral px-1">
            <span>
              {rows.length} quer{rows.length === 1 ? "y" : "ies"} · est. {fmtMoney(kpis.savings, cur)}/mo recoverable
            </span>
            {data.demo && <span className="pill bg-info/15 text-info">demo data</span>}
          </div>
          <DataTable
            columns={columns}
            updating={loading && !!data}
            rows={rows}
            rowKey={(r) => r.id}
            initialSort={{ key: "impact", dir: "desc" }}
            renderExpanded={(r) => <QueryDetail row={r} />}
            emptyMessage={features.writeback ? ackEmptyMessage(status, "queries", "query") : "No queries match the current filters."}
            bulkAck={features.writeback ? {
              source: "query",
              metricName: "est_savings_usd_month",
              itemOf: (r) => ({ entityKey: `${r.insight_type}::${r.target_table || r.warehouse}`, metricValue: r.est_savings_usd_month, acked: (r.ack_status ?? "open") !== "open" }),
              statusOf: (r) => ({ status: r.ack_status, reopened: r.reopened }),
              scopeNote: "Each acknowledgement covers all findings of that insight type on its target table/warehouse, not just one query.",
              onChanged: () => setAckNonce((n) => n + 1),
              status, onStatusChange: setStatus, statusCounts: data?.ack_counts,
            } : undefined}
          />
        </>
      )}
    </PageShell>
  );
}
