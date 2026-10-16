import { useMemo, useState } from "react";
import { fetchOverview, fetchWorkspaceOptions } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useAppStore, useCurrency } from "../store/appStore";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { SpendSavingsChart } from "../components/charts/SpendSavingsChart";
import { DriverBar, DriverTrendChart, MoMChangeBar } from "../components/charts/CostDriverCharts";
import { ExecutiveSummary } from "../components/overview/ExecutiveSummary";
import { CostLenses } from "../components/overview/CostLenses";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { FilterBar, MultiDropdown } from "../components/shared/FilterBar";
import { InfoTip } from "../components/shared/InfoTip";
import { fmtMoney, fmtNum, fmtPct, byNamedFirst } from "../components/shared/format";
export function OverviewPage() {
  const { timeRange, setActivePage, gotoRecommendationsForWorkspace } = useAppStore();
  const effectiveDiscount = useAppStore((s) => s.effectiveDiscount);
  const cur = useCurrency();
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const wsParam = workspaces.length ? workspaces.join(",") : "all";
  const { data, loading, error } = useApi(
    () => fetchOverview({ workspace: wsParam, time_range: timeRange }),
    [wsParam, timeRange],
  );

  // Workspace options from the full list (independent of the current filter).
  const { data: wsData } = useApi(() => fetchWorkspaceOptions(), []);
  const workspaceOptions = useMemo(
    () => (wsData?.data ?? [])
      .map((w) => ({ value: w.workspace, label: w.workspace }))
      .sort(byNamedFirst),
    [wsData],
  );

  return (
    <PageShell
      title="Overview"
      subtitle="Global cost, savings potential, and top opportunities across all workspaces"
    >
      <FilterBar>
        <MultiDropdown label="Workspace" value={workspaces} onChange={setWorkspaces} options={workspaceOptions} allLabel="All workspaces" />
      </FilterBar>

      {/* Executive summary — sits directly under the title + filters, gives an
          at-a-glance read of the whole page. Baseline is pure-SQL (works with
          AI/Genie off); an AI-written version is one click away when enabled. */}
      {data && <ExecutiveSummary ov={data.data} onGoto={setActivePage} />}

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={5}>
            <KpiCard
              label="Total spend / mo"
              value={fmtMoney(data.data.total_spend_usd_month, cur, { compact: true })}
              tone="accent"
              hint={cur.code === "USD" ? "list price" : `list price × ${cur.rate} FX`}
              // Month-over-month change (like-for-like month-to-date). Rising
              // spend is the "bad" direction here, so tone it negative when up.
              delta={data.data.spend_mom_pct != null && Math.abs(data.data.spend_mom_pct) >= 0.005
                ? { text: `${data.data.spend_mom_pct > 0 ? "+" : "−"}${fmtPct(Math.abs(data.data.spend_mom_pct))} MoM`,
                    tone: data.data.spend_mom_pct > 0 ? "negative" : "positive" }
                : undefined}
              info="Estimated monthly cost across all workspaces at list price (DBUs × effective price). Your headline cost-control number. The MoM delta compares this month-to-date against the same span of last month." />

            <KpiCard
              label={data.data.savings_estimated ? "Savings potential (est.)" : "Savings potential"}
              value={fmtMoney(data.data.total_savings_usd_month, cur, { compact: true })}
              tone="success"
              delta={{ text: fmtPct(data.data.savings_pct), tone: "positive" }}
              hint="of spend"
              info={data.data.savings_estimated
                ? "Estimated optimisation headroom derived from real signals (serverless-shift + automation headroom). This is an estimate, not a billing fact — the recommendation engine refines it per workspace."
                : "Estimated dollars per month you could recover by acting on all recommendations. The delta shows this as a percentage of total spend."}
            />
            <KpiCard label="Total DBUs / mo" value={fmtNum(data.data.total_dbus_month, { compact: true })} tone="info" info="Total Databricks Units consumed per month. A DBU is the unit of processing you are billed for, so it is the volume driver behind spend." />
            {(() => {
              const top = data.data.cost_drivers?.drivers?.[0];
              return (
                <KpiCard
                  label="Top cost driver"
                  value={top ? (top.label ?? top.driver) : "—"}
                  tone="accent"
                  hint={top ? `${fmtPct(top.pct_of_total)} · ${fmtMoney(top.spend_usd_month, cur, { compact: true })}/mo` : undefined}
                  info="The single billing_origin_product (e.g. SQL, Jobs, Model Serving) responsible for the largest share of spend — where to focus cost work first. See the cost-by-driver breakdown below."
                />
              );
            })()}
            <KpiCard label="Need attention" value={String(data.data.num_critical)} tone="warning" hint="critical health" info="Number of workspaces with a critical health status, meaning they most urgently need cost or hygiene attention." />
          </KpiRow>

          {/* When a workspace filter is active the KPIs above scope to it, but the
              trend + cost-driver breakdowns below remain ESTATE-WIDE (the Overview
              endpoint doesn't recompute per-workspace driver/trend series). Say so
              explicitly rather than let the two silently disagree — drill into the
              Workspaces tab for a per-workspace breakdown. */}
          {workspaces.length > 0 && (
            <div className="text-xs text-neutral flex flex-wrap items-center gap-1.5 px-1">
              <span className="pill bg-info/15 text-info">estate-wide</span>
              The spend/savings trend &amp; cost-driver charts below show the whole estate, not the selected workspace{workspaces.length > 1 ? "s" : ""}.
              <button type="button" onClick={() => setActivePage("workspaces")} className="text-accent hover:underline">
                Open Workspaces for a per-workspace breakdown →
              </button>
            </div>
          )}

          {/* Cost basis + reconciliation note (Chris feedback #1/#2f) — honest about
              what the figures are and what they exclude, so they can be calibrated
              against Azure billing. */}
          <div className="card bg-surface/40 border-l-4 border-l-info text-xs text-neutral">
            <div className="flex items-baseline gap-2 mb-1">
              <h3 className="text-sm font-semibold text-brand-dark">Cost basis</h3>
              {effectiveDiscount > 0
                ? <span className="pill bg-success/15 text-success">effective − {fmtPct(effectiveDiscount)}</span>
                : <span className="pill bg-border/50 text-neutral">list price</span>}
            </div>
            <p className="max-w-4xl">
              Figures are <span className="font-medium text-brand-dark">list price</span> — Databricks DBUs × public list rate
              from <code className="text-[11px]">system.billing.usage</code>
              {cur.code !== "USD" && <> (× {cur.rate} FX for AUD, indicative)</>}.
              {effectiveDiscount > 0 ? (
                <> Your configured effective rate applies a <span className="font-medium text-brand-dark">{fmtPct(effectiveDiscount)}</span> blended
                  discount, so effective ≈ <span className="font-medium text-success">{fmtMoney(Math.round(data.data.total_spend_usd_month * (1 - effectiveDiscount)), cur, { compact: true })}/mo</span>
                  {" "}vs {fmtMoney(data.data.total_spend_usd_month, cur, { compact: true })}/mo list.</>
              ) : (
                <> To calibrate to your invoice, set your blended committed-use / reservation discount
                  (<code className="text-[11px]">FINOPS_EFFECTIVE_DISCOUNT</code>) and every figure shows list <em>and</em> effective.</>
              )}
              {" "}<span className="font-medium text-brand-dark">Not included:</span> Azure VM / infrastructure cost — that lives in
              Azure Cost Management, not Databricks system tables, and must be reconciled there. This is the variance to expect
              vs a raw Azure total.
            </p>
          </div>

          <SpendSavingsChart data={data.data.trend} savingsEstimated={!!data.data.savings_estimated} />

          {/* Cost by cost-driver (product) + top-5 monthly trend — SPEC §9 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DriverBar drivers={data.data.cost_drivers?.drivers ?? []} />
            <DriverTrendChart trend={data.data.cost_drivers?.trend ?? { months: [], series: [] }} />
          </div>

          {/* Driver change vs prior 30 days (%) — spike detection — SPEC §9 */}
          <MoMChangeBar mom={data.data.cost_drivers?.mom ?? []} />

          {/* Operational cost lenses — workload / compute-type / environment / group
              (Chris feedback #2a/#2b/#2c/#2e) */}
          <CostLenses />

          <div className="card">
            <div className="flex items-baseline justify-between mb-3">
              <h3 className="text-sm font-semibold flex items-center gap-1.5">
                Top savings opportunities
                <InfoTip text="The highest-value recommendations across the estate, ranked by estimated dollars per month recoverable. Access risks show no dollar value." />
              </h3>
              <button
                type="button"
                onClick={() => setActivePage("recommendations")}
                className="text-xs text-accent hover:underline"
              >
                View all in Recommendations →
              </button>
            </div>
            <div className="flex flex-col divide-y divide-border/60">
              {data.data.top_opportunities.map((opp, i) => (
                <button
                  key={`${opp.type}:${opp.target}`}
                  type="button"
                  onClick={() => (opp.type === "workspace"
                    ? gotoRecommendationsForWorkspace(opp.target)
                    : setActivePage("recommendations"))}
                  className="flex items-center gap-3 py-2.5 text-left w-full hover:bg-surface/50 -mx-2 px-2 rounded transition"
                >
                  {/* Rank number instead of a repetitive type pill — the list is
                      already ranked by $, and every live row is a workspace. */}
                  <span className="text-xs tabular-nums text-neutral w-5 shrink-0 text-right">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    {/* Target (name) is the headline, not a mono id buried mid-line. */}
                    <div className="text-sm font-medium truncate text-brand-dark">{opp.target}</div>
                    <div className="text-xs text-neutral truncate">
                      <span className="text-accent font-medium">{opp.insight}</span>
                      <span className="mx-1.5">·</span>
                      {opp.detail}
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    {opp.est_savings_usd_month > 0 ? (
                      <>
                        <div className="text-sm font-semibold text-success tabular-nums">
                          {fmtMoney(opp.est_savings_usd_month, cur)}/mo
                        </div>
                        <div className="text-[10px] uppercase tracking-wide text-neutral">potential saving</div>
                      </>
                    ) : (
                      <div className="text-xs text-neutral">risk</div>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </PageShell>
  );
}
