import { fetchWorkspaceDetail } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useCurrency, useBuEnabled } from "../../store/appStore";
import { LoadingCard, ErrorCard } from "../layout/PageShell";
import { StatusPill, Pill } from "../shared/Pill";
import { InfoTip } from "../shared/InfoTip";
import { StatTile } from "../shared/StatTile";
import { fmtMoney, fmtPct, fmtNum } from "../shared/format";
import type { MoneyCurrency } from "../shared/format";
import { WorkspaceTrendChart } from "./WorkspaceTrendChart";
import { GenieSpendCard } from "../genie/GenieSpendCard";
import {
  DriverBar,
  DriverTrendChart,
  MoMChangeBar,
  SkuStackedBar,
  DbuBySkuBar,
  SkuBreakdownTable,
} from "../charts/CostDriverCharts";
import type {
  SpendByEntity,
  ProductMixSlice,
  WorkspaceDetail as WorkspaceDetailT,
} from "../../types";

// Product/SKU colour ramp — stable order, distinct hues (design-system palette).
const PRODUCT_COLORS: Record<string, string> = {
  "Serverless SQL": "#0D9488",
  "Serverless Jobs": "#1B8A4A",
  "DLT / Lakeflow": "#3B82F6",
  "Jobs Classic": "#8B5CF6",
  "All-Purpose Compute": "#F59E0B",
  "SQL Warehouse (Classic)": "#C0392B",
  "Model Serving": "#EC4899",
  Apps: "#14B8A6",
  "Foundation Model APIs": "#6366F1",
  "Predictive Optimization": "#84CC16",
};

function colorFor(product: string, i: number): string {
  return PRODUCT_COLORS[product] ?? ["#0D9488", "#3B82F6", "#8B5CF6", "#F59E0B", "#C0392B", "#EC4899"][i % 6];
}


// Horizontal 100%-stacked product/SKU mix bar + legend rows.
function ProductMix({ mix, cur }: { mix: ProductMixSlice[]; cur: MoneyCurrency }) {
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full border border-border">
        {mix.map((s, i) => (
          <div
            key={s.product}
            title={`${s.product} · ${fmtPct(s.pct)} · ${fmtMoney(s.spend_usd_month, cur, { compact: true })}`}
            style={{ width: `${s.pct * 100}%`, backgroundColor: colorFor(s.product, i) }}
          />
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
        {mix.map((s, i) => (
          <div key={s.product} className="flex items-center gap-2 text-xs min-w-0">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: colorFor(s.product, i) }} />
            <span className="truncate text-brand-dark">{s.product}</span>
            <span className="ml-auto shrink-0 tabular-nums text-neutral">{fmtPct(s.pct)}</span>
            <span className="w-16 shrink-0 text-right tabular-nums text-neutral">{fmtMoney(s.spend_usd_month, cur, { compact: true })}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Ranked "top by $" list with an inline share bar relative to the leader.
function TopList<T extends SpendByEntity>({
  title,
  items,
  label,
  cur,
  mono = false,
  tip,
}: {
  title: string;
  items: T[];
  label: (item: T) => string;
  cur: MoneyCurrency;
  mono?: boolean;
  tip?: string;
}) {
  const list = items ?? [];
  const max = Math.max(1, ...list.map((i) => i.spend_usd_month));
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
        {title}
        {tip && <InfoTip text={tip} />}
      </div>
      <div className="flex flex-col gap-1.5">
        {list.length === 0 && <span className="text-xs text-neutral">No attributable spend in this window.</span>}
        {list.map((it, i) => (
          <div key={i} className="flex items-center gap-2 text-xs">
            <div className="flex-1 min-w-0">
              <div className={`truncate ${mono ? "font-mono" : ""} text-brand-dark`}>{label(it)}</div>
              <div className="mt-1 h-1 w-full rounded-full bg-border/60 overflow-hidden">
                <div className="h-full rounded-full bg-accent" style={{ width: `${(it.spend_usd_month / max) * 100}%` }} />
              </div>
            </div>
            <span className="w-14 shrink-0 text-right tabular-nums text-neutral">{fmtMoney(it.spend_usd_month, cur, { compact: true })}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function WorkspaceDetail({ workspaceId }: { workspaceId: string }) {
  const cur = useCurrency();
  const buEnabled = useBuEnabled();
  const { data, loading, error } = useApi(() => fetchWorkspaceDetail(workspaceId), [workspaceId]);

  if (loading) return <LoadingCard label="Loading workspace detail…" />;
  if (error) return <ErrorCard message={error} />;
  if (!data) return null;

  const d: WorkspaceDetailT = data.data;
  const cd = d.cost_drivers;

  return (
    <div className="flex flex-col gap-4">
      {/* Header summary */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{d.workspace}</span>
        {d.workspace_id && d.workspace_id !== d.workspace && (
          <span className="font-mono text-xs text-neutral">{d.workspace_id}</span>
        )}
        <StatusPill status={d.health} />
        <Pill className="bg-border/60 text-neutral">{d.complexity}</Pill>
        {buEnabled && d.bu && <span className="ml-auto text-xs text-neutral">{d.bu}</span>}
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatTile label="Spend / mo" value={fmtMoney(d.spend_usd_month, cur, { compact: true })} tone="accent" info="This workspace's estimated monthly spend at list price (DBUs consumed times effective list rate)." />
        <StatTile label="Savings / mo" value={fmtMoney(d.savings_usd_month, cur, { compact: true })} tone="success" info="Estimated monthly dollars recoverable in this workspace from the recommended optimisations." />
        <StatTile label="DBUs / mo" value={fmtNum(d.dbus_month, { compact: true })} tone="info" info="Databricks Units consumed per month, the unit of processing you are billed for." />
        <StatTile label="Automated" value={fmtPct(d.automated_pct)} info="Share of spend on automated or optimised compute (serverless, jobs, Predictive Optimization) versus manually managed compute." />
        <StatTile label="Idle" value={fmtPct(d.idle_pct)} tone={d.idle_pct > 0.2 ? "warning" : "neutral"} info="Share of compute time spent running but doing no useful work, a direct source of wasted spend." />
        <StatTile label="Serverless" value={fmtPct(d.serverless_share)} info="Share of this workspace's spend on serverless SKUs versus classic compute." />
        <StatTile label="Clusters" value={String(d.num_clusters)} info="Number of active all-purpose and job clusters seen in this workspace." />
        <StatTile label="Warehouses" value={String(d.num_warehouses)} info="Number of active Databricks SQL warehouses serving queries in this workspace." />
        {d.genie && d.genie.total_dbus > 0 && (
          <StatTile
            label="Genie / mo"
            value={fmtMoney(d.genie.total_list_cost_usd, cur, { compact: true })}
            tone="info"
            info={`Genie spend this month at list price across ${d.genie.num_surfaces} surface(s), ${d.genie.distinct_users} user(s). Paid portion (above the free allowance): ${fmtMoney(d.genie.paid_list_cost_usd, cur, { compact: true })}.`}
          />
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Product / SKU mix */}
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
            Product / SKU mix
            <InfoTip text="How this workspace's spend splits across products like DBSQL, Jobs, DLT or Lakeflow, Model Serving, and Apps. Shows which product to target for savings." />
          </div>
          <ProductMix mix={d.product_mix} cur={cur} />
        </div>

        {/* Monthly trend */}
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <div className="text-sm font-semibold mb-1">
            {d.savings_estimated ? "Monthly spend & estimated recoverable" : "Monthly spend and captured savings"}
          </div>
          <WorkspaceTrendChart data={d.monthly_trend} savingsEstimated={!!d.savings_estimated} />
        </div>
      </div>

      {/* Top-by-$ breakdowns */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <TopList title="Top users by $" items={d.top_users} label={(u) => u.user} cur={cur} mono tip="The principals or users driving the most monthly spend in this workspace, ranked by dollars. Useful for targeting the biggest cost owners." />
        </div>
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <TopList title="Top jobs by $" items={d.top_jobs} label={(j) => j.job} cur={cur} mono />
        </div>
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <TopList title="Top warehouses by $" items={d.top_warehouses} label={(w) => w.warehouse} cur={cur} mono tip="The SQL warehouses active in this workspace that cost the most per month, ranked by dollars. Idle or oversized warehouses are common savings targets." />
        </div>
      </div>

      {/* Cost-by-driver + SKU breakdown drill-down (SPEC §9) */}
      {cd && (
        <>
          <div className="flex items-center gap-2 pt-1">
            <span className="text-sm font-semibold flex items-center gap-1.5">
              Cost by cost-driver &amp; SKU
              <InfoTip text="Breaks this workspace's spend down by cost-driver product and by individual billing SKU. Helps pinpoint exactly what to optimize." />
            </span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <DriverBar drivers={cd.drivers} title="Cost by cost-driver (product)" />
            <DriverTrendChart trend={cd.trend} />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SkuStackedBar skus={cd.sku_breakdown} title="Cost by SKU (stacked by driver)" />
            <MoMChangeBar mom={cd.mom} />
          </div>
          <DbuBySkuBar dbu={cd.dbu_by_sku} />
          <div>
            <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
              SKU-level breakdown (total_cost · % of total)
              <InfoTip text="A SKU is a single priced product billing line. Shows each SKU's total cost and share of spend so you can see where money goes." />
            </div>
            <SkuBreakdownTable skus={cd.sku_breakdown} />
          </div>
        </>
      )}

      {/* Genie spend — Code vs Spaces for this workspace (SPEC §13; always on) */}
      <GenieSpendCard workspace={d.workspace} />
    </div>
  );
}
