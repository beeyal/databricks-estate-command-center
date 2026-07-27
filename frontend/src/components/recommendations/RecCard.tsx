import type { HubRec } from "../../types";
import { useCurrency } from "../../store/appStore";
import { InfoTip } from "../shared/InfoTip";
import { fmtMoney } from "../shared/format";

const SCOPE_LABEL: Record<string, string> = {
  global: "Estate-wide",
  workspace: "Workspace",
  bu: "Business unit",
  team: "Team",
  user: "User",
  account: "Account",
};

// Expanded rec detail: what-to-do steps, suggested owner, expected impact, and
// any caveat.
export function RecCard({ rec }: { rec: HubRec }) {
  // The hub's recommendation text is deterministic (rule-templated), not model-
  // generated — the recommender never calls the LLM in this path. So there's no
  // "AI narration" badge here (it would falsely imply the text was AI-authored);
  // expected_impact is shown once, honestly, under "Expected impact" below.
  const cur = useCurrency();
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 flex flex-col gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-1.5">What to do</div>
            <ol className="list-decimal list-inside flex flex-col gap-1 text-brand-dark/90">
              {rec.what_to_do.map((step, i) => (
                <li key={i} className="leading-snug">
                  {step}
                </li>
              ))}
            </ol>
          </div>

          {/* Show the math — how the savings figure is derived (a customer trust ask).
              Non-estimatable recs (serverless / VM-dependent) explain why there's
              no number and what's needed to get one, instead of a formula. */}
          {rec.savings_basis && (
            <div className={`rounded-lg border p-3 ${rec.savings_estimatable === false ? "border-warning/40 bg-warning/5" : "border-border bg-surface/40"}`}>
              <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-1.5 flex items-center gap-1.5">
                {rec.savings_estimatable === false ? "Why no savings figure" : "How this is calculated"}
                <InfoTip text={rec.savings_estimatable === false
                  ? "This is a genuine optimisation, but its dollar saving can't be computed accurately from your usage data alone — it needs an A/B run or Azure Billing (VM + reservation) cost. We show the finding, not a number we can't stand behind."
                  : "The formula and the actual figures from your usage data behind this estimate. All $ are list price."} />
              </div>
              {rec.savings_estimatable !== false && rec.est_savings_usd_month > 0 && (
                <div className="text-sm font-semibold text-success mb-1 tabular-nums">
                  {fmtMoney(rec.est_savings_usd_month, cur)} / mo
                </div>
              )}
              <p className="text-xs text-neutral leading-snug">{rec.savings_basis}</p>
              {rec.evidence.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {rec.evidence.map((e, i) => (
                    <span key={i} className="pill bg-border/60 text-neutral text-[11px]">{e}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {rec.expected_impact && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-1">Expected impact</div>
              <p className="text-xs text-neutral leading-relaxed">{rec.expected_impact}</p>
            </div>
          )}

          {rec.notes && (
            <div className="card bg-surface/60 py-2 text-xs text-neutral">
              <span className="font-medium text-warning">Note:</span> {rec.notes}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-neutral">Scope</div>
              <div className="text-brand-dark">
                {SCOPE_LABEL[rec.scope] ?? rec.scope}
                <span className="text-neutral"> · </span>
                <span className="font-mono text-xs">{rec.scope_label}</span>
              </div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-neutral">Suggested owner</div>
              <div className="text-brand-dark">{rec.owner || "—"}</div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-neutral">Source</div>
              <div className="text-brand-dark capitalize">{rec.source}</div>
            </div>
            {rec.catalog_id && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-neutral">Play</div>
                <div className="text-brand-dark font-mono text-xs">{rec.catalog_id}</div>
              </div>
            )}
          </div>
          {rec.savings_estimatable !== false && rec.est_savings_usd_month === 0 && (
            <div className="text-xs text-neutral">
              Risk-avoidance / enablement play — no direct $ but reduces compliance / security / overrun risk.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
