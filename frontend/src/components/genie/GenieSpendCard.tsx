import { fetchGenieCost } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useCurrency } from "../../store/appStore";
import { fmtMoney, fmtNum } from "../shared/format";
import { LoadingCard, ErrorCard } from "../layout/PageShell";
import { InfoTip } from "../shared/InfoTip";
import type { GenieCost } from "../../types";

// "Genie spend" card (SPEC §13; ALWAYS on). Genie cost by SURFACE
// (usage_metadata.genie.surface: Genie Code / Spaces / Agents), per workspace ×
// user, DBUs + list $. Rendered on the Genie page, the Recommendations
// cost-attribution panel (estate) and the Workspaces drill-down (per workspace).
// List-price $, dual-currency aware. Carries the undocumented-surface /
// shared-free-tier / list-price caveats. Calls NO model — deterministic
// attribution over billing data, so not gated by any feature flag.

// Stable colour per surface (Tailwind classes present in the design system).
const SURFACE_STYLE: Record<string, { bar: string; pill: string; dot: string }> = {
  GENIE_CODE: { bar: "bg-insight-rewrite", pill: "bg-insight-rewrite/15 text-insight-rewrite", dot: "bg-insight-rewrite" },
  GENIE_ONE: { bar: "bg-insight-enable", pill: "bg-insight-enable/15 text-insight-enable", dot: "bg-insight-enable" },
  GENIE_AGENTS: { bar: "bg-info", pill: "bg-info/15 text-info", dot: "bg-info" },
  UNKNOWN: { bar: "bg-neutral", pill: "bg-border text-neutral", dot: "bg-neutral" },
};
export function surfaceStyle(surface: string) {
  return SURFACE_STYLE[surface] ?? SURFACE_STYLE.UNKNOWN;
}

// Plain-language definition of each Genie surface, surfaced as tooltips so the
// "what's the difference?" question is self-served. Keyed on the raw
// usage_metadata.genie.surface value.
const SURFACE_HELP: Record<string, string> = {
  GENIE_CODE:
    "Genie Code: the natural-language-to-SQL assistant inside the SQL editor and notebooks. A developer or analyst prompts Genie inline and it drafts the SQL. Technical users, in the editor. Spend concentrates in a few power users.",
  GENIE_ONE:
    "Genie Spaces: the no-code conversational experience (Genie One / Databricks One chat). Business users ask questions in plain English against a curated, governed Genie Space and get answers and charts back. Bills today as the GENIE_ONE surface. Spend spreads across many light users.",
  GENIE_AGENTS:
    "Genie Agents: Genie called programmatically as a tool inside an agent (Agent Bricks / Genie Conversation API), not a human in a UI. Machine-driven, so it can spike with automation volume.",
  UNKNOWN:
    "Genie usage with no surface recorded (~0.5% of rows). Counted in totals but not attributed to Code, Spaces or Agents.",
};
export function surfaceHelp(surface: string): string {
  return SURFACE_HELP[surface] ?? SURFACE_HELP.UNKNOWN;
}

function SurfaceBadge({ surface, label }: { surface: string; label: string }) {
  return <span className={`pill ${surfaceStyle(surface).pill}`}>{label}</span>;
}

// 100%-stacked split bar across all surfaces + a legend row per surface.
function SurfaceSplitBar({ data }: { data: GenieCost }) {
  const cur = useCurrency();
  const totals = data.surface_totals.filter((s) => s.list_usd > 0);
  const total = Math.max(1, totals.reduce((a, s) => a + s.list_usd, 0));
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full border border-border">
        {totals.map((s) => (
          <div
            key={s.surface}
            className={surfaceStyle(s.surface).bar}
            style={{ width: `${(s.list_usd / total) * 100}%` }}
            title={`${s.label} · ${fmtMoney(s.list_usd, cur, { compact: true })}`}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {totals.map((s) => (
          <span key={s.surface} className="flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 rounded-sm ${surfaceStyle(s.surface).dot}`} /> {s.label}
            <InfoTip text={surfaceHelp(s.surface)} label={`What is ${s.label}?`} />
            <span className="tabular-nums text-neutral">
              {fmtMoney(s.list_usd, cur, { compact: true })} · {fmtNum(s.dbus, { compact: true })} DBUs
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function GenieSpendCard({
  workspace,
  title = "Genie spend by surface",
  maxRows = 12,
}: {
  workspace?: string;
  title?: string;
  maxRows?: number;
}) {
  const cur = useCurrency();
  const { data, loading, error } = useApi(
    () => fetchGenieCost(workspace ? { workspace } : {}),
    [workspace],
  );

  if (loading && !data) return <LoadingCard label="Loading Genie spend…" />;
  if (error) {
    // This card is an OPTIONAL sub-panel (embedded in the Workspaces drill-down
    // and the Recommendations attribution panel). A fail-closed live read (503 —
    // e.g. the billing grant is missing for this scope) shouldn't drop a red
    // error into an otherwise-fine drill-down — degrade to the same calm note the
    // zero-billable case uses. A genuinely unexpected error still surfaces.
    const softDegrade = /live read failed|503|billing/i.test(error);
    return softDegrade ? (
      <div className="rounded-lg border border-border bg-surface/40 px-3 py-3 text-xs text-neutral leading-relaxed">
        <span className="font-medium text-brand-dark">Genie spend unavailable for this scope.</span>{" "}
        The billing read for Genie cost couldn't complete here (often a missing grant on{" "}
        <span className="font-mono">system.billing</span>). Nothing else on the page is affected.
      </div>
    ) : (
      <ErrorCard message={error} />
    );
  }
  if (!data) return null;

  const gc = data.data;
  const rows = gc.breakdown.slice(0, maxRows);
  // No billable Genie rows for this scope. Usually CORRECT, not a bug: a
  // workspace whose Genie usage stays under the 150 free DBU/user/month allowance
  // has no BILLED spend — that usage sits under the free GENIE_FREE_USAGE SKU
  // (Databricks: most accounts "continue to pay nothing"). Explain it rather than
  // showing a bare $0 that reads like a broken tab.
  const noSpend = rows.length === 0 || (gc.summary.total_list_cost_usd || 0) === 0;

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          {title}
          <InfoTip text="Genie DBU spend split by surface (usage_metadata.genie.surface): Genie Code = SQL editor / notebook, Genie One = Databricks One chat, Genie Agents = Agent Bricks. Attributed per workspace and user from billing data (billing_origin_product='GENIE'). No model is called." />
        </h3>
        <span className="text-xs text-neutral tabular-nums">
          {fmtMoney(gc.summary.total_list_cost_usd, cur, { compact: true })} list ·{" "}
          {fmtMoney(gc.summary.paid_list_cost_usd, cur, { compact: true })} paid ·{" "}
          {gc.summary.distinct_users} users · {gc.month}
        </span>
      </div>

      {noSpend ? (
        <div className="rounded-lg border border-border bg-surface/40 px-3 py-3 text-xs text-neutral leading-relaxed">
          <span className="font-medium text-brand-dark">No billable Genie spend for this scope.</span>{" "}
          This is expected — it doesn't mean Genie is unused. Genie bills pay-as-you-go from
          6 Jul 2026, and only usage <span className="font-medium">above the free 150 DBUs / user / month</span>
          {" "}is charged; usage within the allowance is recorded under the free{" "}
          <span className="font-mono">GENIE_FREE_USAGE</span> SKU (no list price), so a workspace under the
          allowance correctly shows $0 billable. Recent usage can also take a few hours to land in billing.
        </div>
      ) : (
      <>
      <SurfaceSplitBar data={gc} />

      {/* Per workspace × user × surface rows */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-neutral border-b border-border">
              <th className="py-1.5 pr-3 font-medium">Workspace</th>
              <th className="py-1.5 pr-3 font-medium">User</th>
              <th className="py-1.5 pr-3 font-medium">
                <span className="inline-flex items-center gap-1">Surface<InfoTip text="Which Genie surface generated the usage: Genie Code (editor/notebook), Genie Spaces (Genie One / Databricks One), or Genie Agents (Agent Bricks)." /></span>
              </th>
              <th className="py-1.5 pr-3 font-medium text-right">DBUs</th>
              <th className="py-1.5 pr-3 font-medium text-right">
                <span className="inline-flex items-center gap-1">List $<InfoTip text="Cost at published list price (DBUs times effective list rate), before any discount or free allowance." /></span>
              </th>
              <th className="py-1.5 font-medium text-right">
                <span className="inline-flex items-center gap-1">Paid $<InfoTip text="Estimated billable cost after the 150 free Genie DBUs per user per month (shared across surfaces, from 6 Jul 2026) are deducted." /></span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-border/50">
                <td className="py-1.5 pr-3 font-mono text-neutral truncate max-w-[160px]">{r.workspace}</td>
                <td className="py-1.5 pr-3 font-mono text-brand-dark truncate max-w-[180px]">{r.user_identity}</td>
                <td className="py-1.5 pr-3"><SurfaceBadge surface={r.surface} label={r.label} /></td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-neutral">{fmtNum(r.total_dbus)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{fmtMoney(r.total_list_cost_usd, cur)}</td>
                <td className="py-1.5 text-right tabular-nums text-neutral">{fmtMoney(r.paid_list_cost_usd, cur)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </>
      )}

      {/* Caveats (SPEC §13) */}
      <ul className="flex flex-col gap-1 text-[11px] text-neutral border-t border-border pt-2">
        {gc.caveats.map((c, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="text-warning shrink-0" aria-hidden>·</span>
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
