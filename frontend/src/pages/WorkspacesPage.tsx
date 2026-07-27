import { useMemo, useState } from "react";
import { fetchWorkspaces, fetchCostDrivers } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useCurrency, useBuEnabled } from "../store/appStore";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, Dropdown, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { InfoTip } from "../components/shared/InfoTip";
import { Pill } from "../components/shared/Pill";
import { ProgressBar } from "../components/shared/ProgressBar";
import { Sparkline } from "../components/shared/Sparkline";
import { fmtMoney, fmtPct, byNamedFirst } from "../components/shared/format";
import { WorkspaceDetail } from "../components/workspaces/WorkspaceDetail";
import {
  DriverBar,
  DriverTrendChart,
  MoMChangeBar,
  SkuStackedBar,
  DbuBySkuBar,
  SkuBreakdownTable,
} from "../components/charts/CostDriverCharts";
import type { Workspace } from "../types";

type WorkspacesView = "workspaces" | "sku";

export function WorkspacesPage() {
  const cur = useCurrency();
  const buEnabled = useBuEnabled();
  const [view, setView] = useState<WorkspacesView>("workspaces");
  const [customer, setCustomer] = useState<string[]>([]);
  const [complexity, setComplexity] = useState<string[]>([]);
  const [health, setHealth] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const { data, loading, error } = useApi(
    () => fetchWorkspaces({
      complexity: complexity.length ? complexity.join(",") : "all",
      health: health.length ? health.join(",") : "all",
      search,
    }),
    [complexity.join(","), health.join(","), search],
  );

  // BU/Customer options come from a SEPARATE unfiltered fetch (matching Jobs/
  // Clusters/Warehouses/QueryAdvisor) so the dropdown doesn't shrink — and drop
  // options a user has selected — when they narrow complexity/health/search.
  const { data: allWs } = useApi(() => fetchWorkspaces({}), []);
  const buOptions = useMemo(() => {
    const set = Array.from(new Set((allWs?.data ?? []).map((r) => r.bu).filter(Boolean)))
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }));
    return set.map((b) => ({ value: b, label: b }));
  }, [allWs]);

  const allRows = data?.data ?? [];

  const rows = customer.length ? allRows.filter((r) => customer.includes(r.bu)) : allRows;

  // KPI summary reconciles to the currently filtered rows.
  const totalSpend = rows.reduce((s, r) => s + r.spend_usd_month, 0);
  const totalSavings = rows.reduce((s, r) => s + r.savings_usd_month, 0);
  const savingsPct = totalSpend ? totalSavings / totalSpend : 0;
  const avgAutomated = rows.length ? rows.reduce((s, r) => s + r.automated_pct, 0) / rows.length : 0;
  // "Low automation" = workspaces under 25% automated DBUs (was labelled Critical
  // "health"; renamed — low automation is a maturity signal, not a health problem).
  const lowAutomation = rows.filter((r) => (r.automated_pct ?? 0) < 0.25).length;

  const columns: Column<Workspace>[] = [
    { key: "workspace", header: "Workspace", sortValue: (r) => r.workspace, render: (r) => (
      <div className="font-medium truncate max-w-[240px]" title={r.workspace}>{r.workspace}</div>
    ) },
    { key: "workspace_id", header: "Workspace ID", sortValue: (r) => r.workspace_id, render: (r) => (
      r.workspace_id && r.workspace_id !== r.workspace
        ? <span className="text-xs font-mono text-neutral">{r.workspace_id}</span>
        : <span className="text-xs text-neutral">—</span>
    ) },
    { key: "spend", header: "Spend/mo", align: "right", sortValue: (r) => r.spend_usd_month, render: (r) => <span className="tabular-nums">{fmtMoney(r.spend_usd_month, cur, { compact: true })}</span> },
    { key: "savings", header: (<span className="inline-flex items-center gap-1">Savings/mo<InfoTip text="ESTIMATED optimisation headroom, not a measured or realised saving = this workspace's spend × the Savings % heuristic. Validate specific plays on the Recommendations tab before banking any number." /></span>), align: "right", sortValue: (r) => r.savings_usd_month, render: (r) => <span className="tabular-nums text-success">{fmtMoney(r.savings_usd_month, cur, { compact: true })}</span> },
    { key: "savings_pct", header: (<span className="inline-flex items-center gap-1">Savings %<InfoTip text="MODELED headroom estimate (not a measured or realised saving): a heuristic driven by how much of the workspace is un-automated and non-serverless (plus idle share where available) — low automation + low serverless share ⇒ more room to productionise / move to serverless. A directional signal, capped so it never claims an implausibly large fraction; the Recommendations tab has the specific, evidence-backed plays." /></span>), align: "right", sortValue: (r) => r.savings_pct, render: (r) => <span className="tabular-nums">{fmtPct(r.savings_pct)}</span> },
    { key: "automated", header: (<span className="inline-flex items-center gap-1">Automation %<InfoTip text="Share of this workspace's DBUs that run on AUTOMATED compute — scheduled Jobs + DLT/Lakeflow pipelines — vs interactive/all-purpose/SQL. Higher = more of the workload is productionised (typically cheaper + more predictable); low automation is common for exploratory/data-science workspaces and isn't inherently bad." /></span>), sortValue: (r) => r.automated_pct, render: (r) => <ProgressBar value={r.automated_pct} tone="info" /> },
    { key: "complexity", header: "Complexity", align: "center", sortValue: (r) => r.complexity, render: (r) => <Pill className="bg-border/60 text-neutral">{r.complexity}</Pill> },
    { key: "trend", header: (<span className="inline-flex items-center gap-1">Trend<InfoTip text="Weekly spend sparkline. Colour shows direction: red = spend trending up, green = trending down, grey = flat." /></span>), align: "center", sortValue: (r) => (r.trend?.length ? r.trend[r.trend.length - 1] : 0), render: (r) => <Sparkline data={r.trend} /> },
  ];

  return (
    <PageShell title="Workspaces" subtitle="Workspace-by-workspace cost, savings, and automation maturity">
      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={5}>
            <KpiCard label="Workspaces" value={String(rows.length)} tone="neutral" info="Number of Databricks workspaces matching the current filters. Fewer, well-governed workspaces are easier to control for cost." />
            <KpiCard label="Spend / mo" value={fmtMoney(totalSpend, cur, { compact: true })} tone="accent" hint={cur.code === "USD" ? "list price" : `× ${cur.rate} FX`} info="Total monthly spend across the filtered workspaces, computed as Databricks Units consumed times the effective list price. This is the main cost number to watch." />
            <KpiCard label="Savings / mo" value={fmtMoney(totalSavings, cur, { compact: true })} tone="success" delta={{ text: fmtPct(savingsPct), tone: "positive" }} hint="est. headroom" info="ESTIMATED optimisation headroom (not realised savings) across the filtered workspaces — the sum of each workspace's spend × its modeled Savings % (a heuristic on un-automated + non-serverless share, capped so it never claims an implausibly large fraction). A directional opportunity to validate on Recommendations, not money already saved." />
            <KpiCard label="Avg automation" value={fmtPct(avgAutomated)} tone="info" info="Average automation % across the filtered workspaces — the share of DBUs on scheduled Jobs + DLT/Lakeflow compute (vs interactive/all-purpose/SQL). Higher = more of the workload is productionised; low is normal for exploratory/data-science workspaces." />
            <KpiCard label="Low automation" value={String(lowAutomation)} tone="warning" hint="<25% automated" info="Workspaces where under 25% of DBUs run on automated (scheduled Jobs + DLT) compute — the rest is interactive/all-purpose/SQL. Often the biggest headroom to productionise, but low automation is normal for exploratory/data-science workspaces and isn't a fault by itself." />
          </KpiRow>

          {/* View toggle: workspace table vs By Service / SKU sub-view (SPEC §9.2) */}
          <div className="inline-flex rounded-lg border border-border overflow-hidden w-fit">
            {([
              ["workspaces", "Workspaces"],
              ["sku", "By Service / SKU"],
            ] as [WorkspacesView, string][]).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setView(id)}
                className={`px-3.5 py-1.5 text-xs font-medium transition ${
                  view === id ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {view === "workspaces" && (
            <>
              <FilterBar>
                {buEnabled && (
                  <MultiDropdown label="Customer / BU" value={customer} onChange={setCustomer} options={buOptions} allLabel="All customers / BUs" />
                )}
                <MultiDropdown label="Complexity" value={complexity} onChange={setComplexity} options={[
                  { value: "Simple", label: "Simple" }, { value: "Moderate", label: "Moderate" }, { value: "Complex", label: "Complex" },
                ]} allLabel="All" />
                <MultiDropdown label="Automation" value={health} onChange={setHealth} options={[
                  { value: "Good", label: "High (≥60%)" }, { value: "Warning", label: "Medium (25–60%)" }, { value: "Critical", label: "Low (<25%)" },
                ]} allLabel="All" />
                <SearchBox value={search} onChange={setSearch} placeholder={buEnabled ? "Search workspace or BU…" : "Search workspace…"} />
              </FilterBar>

              <div className="text-xs text-neutral -mt-1">
                Automation = share of DBUs on scheduled Jobs + DLT (vs interactive/all-purpose/SQL): High ≥60% · Medium 25–60% · Low &lt;25%. Low isn't a fault — it's common for exploratory workspaces. Click a row to drill into cost-by-driver, SKU breakdown &amp; DBU-by-SKU.
              </div>

              <DataTable
                columns={columns}
                rows={rows}
                rowKey={(r) => r.workspace_id}
                initialSort={{ key: "spend", dir: "desc" }}
                renderExpanded={(r) => <WorkspaceDetail workspaceId={r.workspace_id} />}
              />
            </>
          )}

          {view === "sku" && <SkuSubView />}
        </>
      )}
    </PageShell>
  );
}

// "By Service / SKU" sub-view (SPEC §9.2 + §9.6): estate-or-workspace cost-by-
// driver + monthly trend + cost-by-SKU stacked + DBU-by-SKU + MoM spike + the
// SKU-level table with total_cost + pct_of_total.
function SkuSubView() {
  const cur = useCurrency();
  // Scope the SKU view by an individual workspace (or the whole estate). The BU
  // facet doubles as a workspace picker here since a BU maps to workspaces.
  const [scope, setScope] = useState("all");
  const { data: wsData } = useApi(() => fetchWorkspaces({}), []);
  const wsOptions = useMemo(() => {
    const list = wsData?.data ?? [];
    // value = workspace_id (numeric): the cost-drivers endpoint scopes on the
    // numeric workspace_id (_safe_ws_id rejects names → silently whole-estate).
    // Label stays the friendly name.
    return [
      { value: "all", label: "Whole estate" },
      ...[...list]
        .map((w) => ({ value: w.workspace_id || w.workspace, label: w.workspace }))
        .sort(byNamedFirst),
    ];
  }, [wsData]);

  const { data, loading, error } = useApi(
    () => fetchCostDrivers({ workspace: scope }),
    [scope],
  );

  return (
    <div className="flex flex-col gap-4">
      <FilterBar>
        <Dropdown label="Scope" value={scope} onChange={setScope} options={wsOptions} />
      </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={3}>
            <KpiCard label="Scope spend / mo" value={fmtMoney(data.data.total_spend_usd_month, cur, { compact: true })} tone="accent" info="Total monthly spend for the selected scope, whole estate or one workspace, as Databricks Units times effective list price. The base for the breakdowns below." />
            <KpiCard label="Cost drivers" value={String(data.data.drivers.length)} tone="info" hint="billing_origin_product" info="Number of distinct products driving spend, such as DBSQL, Jobs, or Model Serving. Fewer concentrated drivers make cost easier to target." />
            <KpiCard label="SKUs" value={String(data.data.sku_breakdown.length)} tone="neutral" hint="sku_name" info="Number of distinct billing SKUs in this scope. Each SKU is a separately priced product line, useful for pinpointing where cost sits." />
          </KpiRow>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DriverBar drivers={data.data.drivers} />
            <DriverTrendChart trend={data.data.trend} />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SkuStackedBar skus={data.data.sku_breakdown} />
            <MoMChangeBar mom={data.data.mom} />
          </div>
          <DbuBySkuBar dbu={data.data.dbu_by_sku} />

          <div>
            <div className="flex items-baseline justify-between mb-2">
              <h3 className="text-sm font-semibold flex items-center gap-1.5">
                SKU-level breakdown
                <InfoTip text="A SKU is a single priced product billing line. This table shows each SKU's total cost and its share of scope spend, so you can see exactly where money goes." />
              </h3>
              <span className="text-xs text-neutral">total_cost · % of total — SKUs ending in _AUSTRALIA_EAST (AU-in-geo)</span>
            </div>
            <SkuBreakdownTable skus={data.data.sku_breakdown} />
          </div>
        </>
      )}
    </div>
  );
}
