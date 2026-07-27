import { useState } from "react";
import { postExecSummary } from "../../api/client";
import { useCurrency, useFeatures, useAppStore } from "../../store/appStore";
import { fmtMoney, fmtNum, fmtPct } from "../shared/format";
import { InfoTip } from "../shared/InfoTip";
import type { ExecSummary, Overview, PageId } from "../../types";

// Compact stat shown in the summary's right rail (GridSense exec-briefing style:
// small label + bold value + sub).
function MiniStat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-[10px] uppercase tracking-wide text-neutral">{label}</p>
      <p className="text-lg font-bold tabular-nums leading-none" style={accent ? { color: accent } : undefined}>{value}</p>
      {sub && <p className="text-[10px] text-neutral leading-tight">{sub}</p>}
    </div>
  );
}

// Slim segmented cost-by-driver bar — the small chart that rides alongside the
// narrative (mirrors GridSense's RiskBar).
function DriverSplitBar({ ov }: { ov: Overview }) {
  const cur = useCurrency();
  const drivers = (ov.cost_drivers?.drivers ?? []).slice(0, 5);
  const total = drivers.reduce((s, d) => s + (d.spend_usd_month || 0), 0) || 1;
  const palette = ["#FF3621", "#0EA5E9", "#8B5CF6", "#1B8A4A", "#F59E0B"];
  if (drivers.length === 0) return null;
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-neutral mb-1.5">Where the spend goes</p>
      <div className="flex h-2.5 rounded-full overflow-hidden gap-px">
        {drivers.map((d, i) => {
          const pct = ((d.spend_usd_month || 0) / total) * 100;
          return pct > 0 ? (
            <div key={i} style={{ width: `${pct}%`, background: palette[i % palette.length] }}
              title={`${d.label ?? d.driver}: ${fmtMoney(d.spend_usd_month || 0, cur, { compact: true })}`} />
          ) : null;
        })}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1.5">
        {drivers.map((d, i) => (
          <span key={i} className="flex items-center gap-1 text-[10px] text-neutral">
            <span className="h-1.5 w-1.5 rounded-full inline-block" style={{ background: palette[i % palette.length] }} />
            {d.label ?? d.driver} · {fmtPct(d.pct_of_total ?? 0)}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * Executive summary block for the Overview landing view — sits directly under
 * the title + filters. GridSense exec-briefing style: a short narrative on the
 * left, compact headline stats + a cost-driver split bar on the right.
 *
 * ALWAYS shows a pure-SQL deterministic synthesis (no AI, no Genie needed —
 * this is the baseline and works in every deployment). When ai_narration is
 * enabled, a "Generate" button expands a richer AI-written narrative in place;
 * hidden entirely when AI is off. Deep-links via onGoto.
 */
export function ExecutiveSummary({ ov, onGoto }: { ov: Overview; onGoto: (tab: PageId) => void }) {
  const cur = useCurrency();
  const features = useFeatures();
  const gotoRecommendation = useAppStore((s) => s.gotoRecommendation);
  const [open, setOpen] = useState(false);       // whole block hidden until generated
  const [ai, setAi] = useState<ExecSummary | null>(null);
  const [loading, setLoading] = useState(false);

  // Generate: reveal the block. When AI narration is on, also fetch the richer
  // AI briefing; otherwise the deterministic (pure-SQL) summary is shown.
  async function generate() {
    setOpen(true);
    if (!features.ai_narration) return;   // baseline only — no model call
    setLoading(true);
    try {
      const env = await postExecSummary();
      setAi(env.data);
    } catch {
      setAi({ status: "error", narrative: "" });
    } finally {
      setLoading(false);
    }
  }

  // Collapsed state: nothing but a single button. The banner + metrics only
  // appear once the user clicks Generate (their explicit ask).
  if (!open) {
    return (
      <div className="card flex items-center justify-between gap-4 py-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold text-brand-dark">Executive summary</span>
          <InfoTip text="Generates an at-a-glance briefing over the whole Overview — spend & what's moving, adoption & usage, health & governance, and where to focus. Baseline is computed from your system-table figures; with AI narration enabled it produces a fuller written analysis." />
          <span className="text-xs text-neutral truncate">— an at-a-glance read of the estate: cost, adoption, health & where to focus.</span>
        </div>
        <button
          type="button"
          onClick={generate}
          className="shrink-0 rounded-md bg-accent px-4 py-1.5 text-xs font-semibold text-white hover:bg-accent/90 transition"
        >
          {features.ai_narration ? "✦ Generate executive summary" : "Generate executive summary"}
        </button>
      </div>
    );
  }

  // Deterministic, pure-SQL synthesis — the baseline narrative, built as a
  // multi-paragraph estate briefing (cost · adoption · governance · attention).
  // Every figure here is MEASURED from system tables; the one estimate (savings)
  // is explicitly flagged as an opportunity signal, not a hero number.
  const momPct = ov.spend_mom_pct ?? 0;
  const dir = momPct > 0.005 ? "up" : momPct < -0.005 ? "down" : "flat";
  const topDriver = ov.cost_drivers?.drivers?.[0];
  const savings = ov.addressable_savings_usd_month ?? ov.total_savings_usd_month;
  const attn = ov.needs_attention ?? [];
  const pctTxt = (n?: number) => (n == null ? "—" : fmtPct(n));

  // 1) Cost shape (measured) + savings as a clearly-caveated opportunity.
  const costPara =
    `Spend is ${fmtMoney(ov.total_spend_usd_month, cur, { compact: true })} this month` +
    (dir === "flat" ? ", flat versus last month" : `, ${dir} ${fmtPct(Math.abs(momPct))} versus the same point last month`) +
    (topDriver ? `, led by ${topDriver.label ?? topDriver.driver} at ${pctTxt(topDriver.pct_of_total)} of spend` : "") +
    `. Compute is ${pctTxt(ov.serverless_share)} serverless and ${pctTxt(ov.automated_pct)} automated (scheduled vs interactive).`;

  // 2) Adoption & usage (all measured).
  const adoptPara =
    `${fmtNum(ov.mau ?? 0)} people used the platform in the last 30 days` +
    (ov.wau != null ? ` (${fmtNum(ov.wau)} in the last 7)` : "") +
    `, running ${fmtNum(ov.queries_30d ?? 0)} queries` +
    (ov.query_hours_30d ? ` over ${fmtNum(ov.query_hours_30d)} compute-hours` : "") +
    (ov.queries_30d_delta_pct != null && Math.abs(ov.queries_30d_delta_pct) >= 0.005
      ? ` — query volume ${ov.queries_30d_delta_pct > 0 ? "up" : "down"} ${fmtPct(Math.abs(ov.queries_30d_delta_pct))} versus the prior 30 days`
      : "") +
    (ov.num_products ? `. Teams are using ${ov.num_products} of the platform's products on average` : "") +
    ((ov.genie_adopters || ov.ai_adopters)
      ? `, with ${ov.genie_adopters ?? 0} workspace${(ov.genie_adopters ?? 0) === 1 ? "" : "s"} on Genie and ${ov.ai_adopters ?? 0} on other AI products.`
      : ".");

  // 3) Health, governance & opportunity — savings caveated.
  const govPara =
    (ov.num_critical > 0
      ? `${ov.num_critical} workspace${ov.num_critical === 1 ? "" : "s"} need attention on health`
      : `No workspaces are in critical health`) +
    (ov.untagged_spend_pct != null ? `, and ${fmtPct(ov.untagged_spend_pct)} of spend is untagged (a cost-attribution gap)` : "") +
    `. There's an estimated ${fmtMoney(savings, cur, { compact: true })}/mo of optimisation headroom` +
    (savings && ov.total_spend_usd_month ? ` (~${fmtPct(savings / ov.total_spend_usd_month)} of spend)` : "") +
    ` — a directional signal from automation & serverless gaps, not a committed number; the Recommendations tab has the specific, evidence-backed plays.`;

  const baseline = [costPara, adoptPara, govPara].join("\n\n");

  const useAi = ai?.status === "ok" && ai.narrative;
  // While the AI call is in flight, don't flash the baseline then swap — show a
  // loading placeholder. If AI failed/unavailable, fall back to the baseline.
  const paragraphs = (useAi ? ai!.narrative : baseline).split("\n\n").filter(Boolean);

  return (
    <div className="card flex flex-col lg:flex-row gap-5">
      {/* Left — narrative */}
      <div className="flex-1 min-w-0 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-brand-dark">Executive summary</span>
          <InfoTip text="A read of the whole Overview — cost & what's moving, adoption & usage, health & governance, and where to focus. Baseline is computed from your system-table figures; the AI version adds interpretation. The savings figure is an estimate — validate on the Recommendations tab." />
          {loading && <span className="inline-flex items-center gap-1 text-[11px] text-neutral"><span className="live-dot" />Generating AI analysis…</span>}
          {useAi && <span className="pill bg-success/15 text-success text-[10px]">✦ AI</span>}
          {!loading && !useAi && features.ai_narration && ai?.status && ai.status !== "ok" && (
            <span className="text-[10px] text-neutral">(AI unavailable — showing the computed summary)</span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {/* Regenerate only meaningful when AI is on. */}
            {features.ai_narration && !loading && (
              <button type="button" onClick={generate} className="pill text-[11px] px-2.5 py-1 border border-border text-neutral hover:text-brand-dark hover:bg-surface">
                ↺ Regenerate
              </button>
            )}
            <button type="button" onClick={() => setOpen(false)} className="pill text-[11px] px-2.5 py-1 border border-border text-neutral hover:text-brand-dark hover:bg-surface">
              Hide
            </button>
          </div>
        </div>
        {loading ? (
          <div className="space-y-2 animate-pulse">
            <div className="h-3 w-full rounded bg-border/60" />
            <div className="h-3 w-11/12 rounded bg-border/60" />
            <div className="h-3 w-4/5 rounded bg-border/60" />
            <div className="h-3 w-full rounded bg-border/60 mt-3" />
            <div className="h-3 w-3/4 rounded bg-border/60" />
          </div>
        ) : (
        <div className="space-y-2">
          {paragraphs.map((p, i) => (
            <p key={i} className="text-sm text-brand-dark leading-relaxed">{p}</p>
          ))}
        </div>
        )}
        {/* Needs-attention chips — deep-link into the relevant tab. */}
        {attn.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-1">
            {attn.slice(0, 4).map((a, i) => (
              <button
                key={i}
                type="button"
                onClick={() => (a.source === "rec" && a.entity_key
                  ? gotoRecommendation(a.entity_key)
                  : onGoto(a.tab as PageId))}
                className={`pill text-[11px] px-2.5 py-1 border transition hover:bg-surface inline-flex items-center gap-1.5 ${
                  a.kind === "spend-spike" ? "border-danger/40 text-danger" : "border-accent/40 text-accent"
                }`}
                title={a.detail}
              >
                <span>{a.kind === "spend-spike" ? "▲" : "◆"}</span>
                <span className="max-w-[220px] truncate">{a.title}</span>
                {a.est_savings_usd_month ? <span className="tabular-nums opacity-80">{fmtMoney(a.est_savings_usd_month, cur, { compact: true })}/mo</span> : null}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Right — headline stats + driver split bar */}
      <div className="lg:w-80 shrink-0 flex flex-col gap-3 lg:border-l lg:border-border lg:pl-5">
        <div className="grid grid-cols-2 gap-3">
          <MiniStat
            label="Spend / mo"
            value={fmtMoney(ov.total_spend_usd_month, cur, { compact: true })}
            sub={dir === "flat" ? "flat MoM" : `${momPct > 0 ? "+" : "−"}${fmtPct(Math.abs(momPct))} MoM`}
            accent="#FF3621"
          />
          <MiniStat
            label="Active users (30d)"
            value={fmtNum(ov.mau ?? 0)}
            sub={ov.mau_delta != null && ov.mau_delta !== 0 ? `${ov.mau_delta > 0 ? "+" : ""}${ov.mau_delta} vs prior 30d` : `${fmtNum(ov.queries_30d ?? 0)} queries`}
          />
          <MiniStat
            label="Serverless / automated"
            value={`${pctTxt(ov.serverless_share)} / ${pctTxt(ov.automated_pct)}`}
            sub="of spend"
          />
          <MiniStat
            label="Need attention"
            value={String(ov.num_critical)}
            sub="critical workspaces"
            accent={ov.num_critical > 0 ? "#F59E0B" : undefined}
          />
          {/* Savings shown LAST + labelled 'est. opportunity' so it reads as a
              directional signal, not a measured headline. */}
          <MiniStat
            label="Est. opportunity"
            value={`~${fmtMoney(savings, cur, { compact: true })}`}
            sub={ov.num_p1 != null ? `${ov.num_p1} plays · estimate` : "estimate — see Recs"}
            accent="#1B8A4A"
          />
          <MiniStat
            label="Untagged spend"
            value={pctTxt(ov.untagged_spend_pct)}
            sub="cost-attribution gap"
            accent={ov.untagged_spend_pct != null && ov.untagged_spend_pct > 0.25 ? "#F59E0B" : undefined}
          />
        </div>
        <DriverSplitBar ov={ov} />
      </div>
    </div>
  );
}
