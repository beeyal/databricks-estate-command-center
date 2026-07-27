import type { Attribution } from "../../types";
import { fmtMoney, fmtPct } from "../shared/format";
import { useCurrency, useBuEnabled } from "../../store/appStore";
import { ProgressBar } from "../shared/ProgressBar";
import { GenieSpendCard } from "../genie/GenieSpendCard";
import { InfoTip } from "../shared/InfoTip";

// Cost-attribution panel (SPEC §3.7): spend by BU / workspace / driver, untagged
// %, MoM spikes, and the specific tagging + budget-policy plays to close the
// attribution gaps.
export function CostAttributionPanel({ attribution, workspace }: { attribution: Attribution; workspace?: string }) {
  const cur = useCurrency();
  const buEnabled = useBuEnabled();
  const topBu = attribution.by_bu.slice(0, 8);
  const maxBuSpend = Math.max(...topBu.map((b) => b.spend_usd_month), 1);
  const topDrivers = attribution.cost_drivers.slice(0, 10);
  const maxDriver = Math.max(...topDrivers.map((d) => d.spend_usd_month), 1);
  const spikes = attribution.driver_spikes ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          Cost attribution
          <InfoTip text="How much spend can be traced to a business unit, project or owner. Untagged spend cannot be charged back and is the gap to close." />
        </h3>
        <span className="text-xs text-neutral">
          {fmtMoney(attribution.total_untagged_usd_month, cur, { compact: true })} untagged ·{" "}
          {fmtPct(attribution.untagged_pct)} of {fmtMoney(attribution.total_spend_usd_month, cur, { compact: true })}
        </span>
      </div>

      <div className={`grid grid-cols-1 gap-4 ${buEnabled ? "lg:grid-cols-2" : ""}`}>
        {/* Spend + tag coverage by BU — hidden when no BU dimension is configured */}
        {buEnabled && (
        <div className="card flex flex-col gap-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral flex items-center gap-1.5">
            Spend & tag coverage by business unit
            <InfoTip text="Monthly spend per business unit and the share of it carrying cost-attribution tags. Low tagged % means that spend cannot be reliably charged back." />
          </div>
          <div className="flex flex-col gap-2.5">
            {topBu.map((b) => (
              <div key={b.bu} className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="truncate mr-2">{b.bu}</span>
                  <span className="tabular-nums text-neutral shrink-0">
                    {fmtMoney(b.spend_usd_month, cur, { compact: true })} ·{" "}
                    <span className={b.tagged_pct >= 0.8 ? "text-success" : b.tagged_pct >= 0.5 ? "text-warning" : "text-danger"}>
                      {fmtPct(b.tagged_pct)} tagged
                    </span>
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="h-1.5 flex-1 rounded-full bg-border/60 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${(b.spend_usd_month / maxBuSpend) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
        )}

        {/* Cost by driver (billing_origin_product) */}
        <div className="card flex flex-col gap-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral flex items-center gap-1.5">
            Cost by driver (billing_origin_product)
            <InfoTip text="Spend grouped by the product that generated it (the billing_origin_product field in system.billing.usage), for example DBSQL, jobs, DLT or model serving." />
          </div>
          <div className="flex flex-col gap-2.5">
            {topDrivers.map((d) => (
              <div key={d.driver} className="flex flex-col gap-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="truncate mr-2">
                    {d.driver}
                  </span>
                  <span className="tabular-nums text-neutral shrink-0">
                    {fmtMoney(d.spend_usd_month, cur, { compact: true })} · {fmtPct(d.pct_of_total, 1)}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-border/60 overflow-hidden">
                  <div className="h-full rounded-full bg-info" style={{ width: `${(d.spend_usd_month / maxDriver) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* MoM cost-driver spikes (SPEC §9.3) */}
      {spikes.length > 0 && (
        <div className="card border-l-4 border-l-warning flex flex-col gap-2 py-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral flex items-center gap-1.5">
            Cost-driver spikes vs prior 30 days
            <InfoTip text="Drivers whose spend jumped month over month (MoM) versus the prior 30 days. Large positive swings are the first place to investigate for cost overruns." />
          </div>
          <div className="flex flex-wrap gap-2">
            {spikes.map((s) => (
              <span key={s.driver} className="pill tabular-nums bg-warning/15 text-warning">
                {s.label} +{fmtPct(s.mom_pct)} MoM · +{fmtMoney(s.delta_usd, cur, { compact: true })}
              </span>
            ))}
          </div>
          <div className="text-xs text-neutral">
            Investigate + attribute the drivers behind these spikes; wire spend-anomaly alerts on
            <span className="font-mono"> system.billing.usage</span> (fires the cost-spike recommendation, TAG-004).
          </div>
        </div>
      )}

      {/* Attribution gap-closing plays */}
      <div className="card flex flex-col gap-2 py-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-neutral">Close the attribution gap</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div className="flex flex-col gap-1">
            <div className="font-medium text-brand-dark">Tag taxonomy (classic compute)</div>
            <ProgressBar value={1 - attribution.untagged_pct} tone={attribution.untagged_pct > 0.4 ? "danger" : "warning"} />
            <p className="text-neutral">
              Mandatory BU / Project / Environment / Owner tags enforced via cluster policies (TAG-001, GA).
            </p>
          </div>
          <div className="flex flex-col gap-1">
            <div className="font-medium text-brand-dark">Serverless budget policies</div>
            <span className="pill bg-warning/15 text-warning w-fit">AU preview · PuPr</span>
            <p className="text-neutral">
              Budget policies tag serverless notebooks / jobs / pipelines / endpoints for chargeback (TAG-002).
            </p>
          </div>
          <div className="flex flex-col gap-1">
            <div className="font-medium text-brand-dark">Account budgets + alerts</div>
            <span className="pill bg-success/15 text-success w-fit">GA</span>
            <p className="text-neutral">
              Per-BU / project budgets with up to 4 threshold email alerts before overrun (TAG-003).
            </p>
          </div>
        </div>
      </div>

      {/* Genie spend — Code vs Spaces (SPEC §13; always on) */}
      <GenieSpendCard workspace={workspace} />
    </div>
  );
}
