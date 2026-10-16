import { fetchOverviewLenses } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useCurrency } from "../../store/appStore";
import { InfoTip } from "../shared/InfoTip";
import { fmtMoney, fmtPct } from "../shared/format";
import type { LensBucket } from "../../types";

// Operational cost lenses (Chris feedback #2a/#2b/#2c/#2e): the same estate spend
// re-expressed by workload, Classic vs Serverless, environment, and naming-
// convention group — the way AEMO actually reasons about cost.

// Ordered categorical palette (brand-adjacent, colour-blind-safe enough for ≤5
// slices). Keyed by slot, not by label, so it's stable across lenses.
const BAR_COLORS = ["#2272B4", "#00A972", "#FF3621", "#8A63D2", "#F2A93B", "#5A6B7B"];

function LensCard({
  title,
  info,
  buckets,
}: {
  title: string;
  info: string;
  buckets: LensBucket[];
}) {
  const cur = useCurrency();
  const total = buckets.reduce((s, b) => s + b.spend_usd_month, 0);
  return (
    <div className="card">
      <h3 className="text-sm font-semibold flex items-center gap-1.5 mb-3">
        {title}
        <InfoTip text={info} />
      </h3>
      {/* Single stacked proportion bar */}
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-border/40 mb-3">
        {buckets.map((b, i) => (
          <div
            key={b.key}
            style={{ width: `${Math.max(0, b.pct * 100)}%`, background: BAR_COLORS[i % BAR_COLORS.length] }}
            title={`${b.key} — ${fmtPct(b.pct)}`}
          />
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        {buckets.map((b, i) => (
          <div key={b.key} className="flex items-center gap-2 text-xs">
            <span className="h-2.5 w-2.5 rounded-sm shrink-0" style={{ background: BAR_COLORS[i % BAR_COLORS.length] }} />
            <span className="flex-1 truncate text-brand-dark">{b.key}</span>
            <span className="tabular-nums text-neutral">{fmtPct(b.pct)}</span>
            <span className="tabular-nums font-medium w-16 text-right">{fmtMoney(b.spend_usd_month, cur, { compact: true })}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 pt-2 border-t border-border/50 flex justify-between text-[11px] text-neutral">
        <span>Total</span>
        <span className="tabular-nums">{fmtMoney(total, cur, { compact: true })}/mo</span>
      </div>
    </div>
  );
}

export function CostLenses() {
  const { data, loading } = useApi(() => fetchOverviewLenses(), []);
  const lenses = data?.data;
  if (loading || !lenses) return null;
  const pending = data?.live_pending;
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2">
        <h3 className="text-sm font-semibold">Cost by operational lens</h3>
        {pending && <span className="pill bg-warning/15 text-warning uppercase tracking-wide text-[10px]">workload/compute: sample</span>}
        <InfoTip text="The same monthly estate spend, sliced the way you run the platform rather than by Databricks product: by workload type, Classic vs Serverless compute, Production vs Non-production, and by workspace naming-convention group. Every slice reconciles to the same total." />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        <LensCard
          title="By workload"
          info="Cost grouped into batch / streaming / interactive-ad-hoc / ML-AI, mapped from the billing product + job & cluster metadata. 'Other' is platform services (Apps, Lakebase, Predictive Optimization, monitoring) that aren't a single workload."
          buckets={lenses.by_workload}
        />
        <LensCard
          title="Classic vs Serverless"
          info="Compute split from the SKU names: SERVERLESS_* SKUs vs classic/provisioned compute. (Azure VM/infrastructure cost sits in Azure Cost Management, not Databricks system tables — this is the DBU-side split.)"
          buckets={lenses.by_compute_type}
        />
        <LensCard
          title="By environment"
          info="Production vs Non-production, derived from your workspace naming convention (e.g. -NONPROD / -DEV / SANDBOX ⇒ Non-production). Configurable per your conventions."
          buckets={lenses.by_environment}
        />
        <LensCard
          title="By group"
          info="Cost by naming-convention group — e.g. DATA-EDP- (EDP Core), DATA-ANA- (EDP Edge), FORE- (Forecasting). Configurable to match your exact prefixes."
          buckets={lenses.by_group}
        />
      </div>
    </div>
  );
}
