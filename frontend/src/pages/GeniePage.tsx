import { useMemo, useState } from "react";
import { fetchGenieCost } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { InfoTip } from "../components/shared/InfoTip";
import { surfaceStyle, surfaceHelp } from "../components/genie/GenieSpendCard";
import { fmtMoney, fmtNum, fmtPct, byNamedFirst } from "../components/shared/format";
import { useCurrency } from "../store/appStore";
import type { GenieCostRow } from "../types";

// Genie $ tab — Genie cost tracking by surface (Code / Spaces / Agents), per
// workspace and per user, from system.billing.usage (billing_origin_product =
// 'GENIE', usage_metadata.genie.surface). Deterministic billing attribution
// (calls no model), so ALWAYS on. Dollar figures are LIST price. See
// data/queries.sql (Q1–Q3) for the live-mode SQL and data/genie_cost.py for the
// demo encoding.

export function GeniePage() {
  const cur = useCurrency();
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [search, setSearch] = useState("");

  // One fetch of the full estate; the workspace/user filters are applied
  // client-side so per-user paid figures (computed against the global per-user
  // free allowance) stay consistent across scopes.
  const { data, loading, error } = useApi(() => fetchGenieCost({}), []);
  const gc = data?.data;

  // In live mode the backend sets billable_only=TRUE: the per-row breakdown is
  // the BILLABLE usage (above each user's 150 free DBUs/month). Free usage is now
  // recorded separately as the GENIE_FREE_USAGE SKU (from 20 Jul 2026), so the
  // backend supplies a REAL estate-wide free_absorbed_usd when present. In demo
  // mode billable_only is FALSE and we keep the illustrative free-vs-paid split.
  const billable = gc?.summary?.billable_only ?? false;
  // Real free-absorbed $ from the GENIE_FREE_USAGE SKU (live, estate-wide only;
  // null when scoped, pre-20-Jul, or no free usage). demo derives its own below.
  const liveFreeAbsorbed = gc?.summary?.free_absorbed_usd ?? null;
  const freeInSystemTables = gc?.summary?.free_usage_in_system_tables ?? false;
  const freeRateIsProxy = gc?.summary?.free_rate_is_proxy ?? false;

  const wsOptions = useMemo(
    () =>
      (gc?.by_workspace ?? [])
        .map((w) => ({ value: w.workspace, label: w.workspace }))
        .sort(byNamedFirst),
    [gc],
  );

  const rows = useMemo(() => {
    let r: GenieCostRow[] = gc?.breakdown ?? [];
    if (workspace.length) r = r.filter((x) => workspace.includes(x.workspace));
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      r = r.filter((x) => x.user_identity.toLowerCase().includes(q) || x.workspace.toLowerCase().includes(q));
    }
    return r;
  }, [gc, workspace.join(","), search]);

  // KPIs + per-surface + per-user roll-ups recomputed from the filtered rows.
  const roll = useMemo(() => {
    const list = rows.reduce((a, r) => a + r.total_list_cost_usd, 0);
    const paid = rows.reduce((a, r) => a + r.paid_list_cost_usd, 0);
    const dbus = rows.reduce((a, r) => a + r.total_dbus, 0);
    const users = new Set(rows.map((r) => r.user_identity));
    const workspaces = new Set(rows.map((r) => r.workspace));
    const bySurface = new Map<string, { surface: string; label: string; list: number; paid: number; dbus: number }>();
    for (const r of rows) {
      const s = bySurface.get(r.surface) ?? { surface: r.surface, label: r.label, list: 0, paid: 0, dbus: 0 };
      s.list += r.total_list_cost_usd;
      s.paid += r.paid_list_cost_usd;
      s.dbus += r.total_dbus;
      bySurface.set(r.surface, s);
    }
    const surfaces = [...bySurface.values()].sort((a, b) => b.list - a.list);
    const byUser = new Map<string, { user: string; list: number; paid: number; dbus: number; ws: Set<string>; top: string; topDbus: number }>();
    for (const r of rows) {
      const u = byUser.get(r.user_identity) ?? { user: r.user_identity, list: 0, paid: 0, dbus: 0, ws: new Set(), top: r.label, topDbus: 0 };
      u.list += r.total_list_cost_usd;
      u.paid += r.paid_list_cost_usd;
      u.dbus += r.total_dbus;
      u.ws.add(r.workspace);
      if (r.total_dbus > u.topDbus) { u.top = r.label; u.topDbus = r.total_dbus; }
      byUser.set(r.user_identity, u);
    }
    const usersRanked = [...byUser.values()].sort((a, b) => b.list - a.list);
    return { list, paid, dbus, users: users.size, workspaces: workspaces.size, surfaces, usersRanked };
  }, [rows]);

  // Cap the two big tables so a real workspace (thousands of users) stays fast.
  const USER_CAP = 50;
  const DETAIL_CAP = 150;
  const topUsers = roll.usersRanked.slice(0, USER_CAP);
  const detailRows = [...rows].sort((a, b) => b.total_list_cost_usd - a.total_list_cost_usd).slice(0, DETAIL_CAP);

  const maxSurface = Math.max(1, ...roll.surfaces.map((s) => s.list));
  // Only meaningful in demo mode. In live/billable mode list === paid so this is
  // ~0, and it must never be surfaced as a real "free absorbed" number.
  const freeAbsorbed = roll.list - roll.paid;

  const userColumns: Column<(typeof roll.usersRanked)[number]>[] = [
    {
      key: "user",
      header: "User",
      sortValue: (u) => u.user,
      render: (u) => <span className="font-mono text-xs text-brand-dark truncate max-w-[220px] inline-block">{u.user}</span>,
    },
    {
      key: "top",
      header: (
        <span className="inline-flex items-center gap-1">Top surface<InfoTip text="The Genie surface this user spends the most DBUs on: Genie Code, Genie Spaces (Genie One / Databricks One), or Genie Agents." /></span>
      ),
      sortValue: (u) => u.top,
      render: (u) => {
        const surface = rows.find((r) => r.label === u.top)?.surface ?? "UNKNOWN";
        return <span className={`pill ${surfaceStyle(surface).pill}`}>{u.top}</span>;
      },
    },
    { key: "ws", header: "Workspaces", align: "center", sortValue: (u) => u.ws.size, render: (u) => <span className="tabular-nums">{u.ws.size}</span> },
    { key: "dbus", header: "DBUs", align: "right", sortValue: (u) => u.dbus, render: (u) => <span className="tabular-nums text-neutral">{fmtNum(u.dbus)}</span> },
    // In live/billable mode list === paid, so a single "Billable $" column
    // avoids implying a meaningful list-vs-paid gap. In demo we show both.
    ...(billable
      ? [
          {
            key: "billable",
            header: (
              <span className="inline-flex items-center gap-1">Billable $<InfoTip text="Billable Genie cost for this user — spend above their 150 free DBUs/month (the billed Serverless Real-Time Inference SKU). The free tier is tracked separately as GENIE_FREE_USAGE, so this figure is the charged portion." /></span>
            ),
            align: "right" as const,
            sortValue: (u: (typeof roll.usersRanked)[number]) => u.paid,
            render: (u: (typeof roll.usersRanked)[number]) => <span className="tabular-nums text-neutral">{fmtMoney(u.paid, cur)}</span>,
          },
        ]
      : [
          {
            key: "list",
            header: (
              <span className="inline-flex items-center gap-1">List $<InfoTip text="Genie cost at published list price for this user across all surfaces (DBUs times effective list rate), before any free allowance." /></span>
            ),
            align: "right" as const,
            sortValue: (u: (typeof roll.usersRanked)[number]) => u.list,
            render: (u: (typeof roll.usersRanked)[number]) => <span className="tabular-nums">{fmtMoney(u.list, cur)}</span>,
          },
          {
            key: "paid",
            header: (
              <span className="inline-flex items-center gap-1">Paid $<InfoTip text="Estimated billable cost after this user's 150 free Genie DBUs per month (shared across all surfaces) are deducted." /></span>
            ),
            align: "right" as const,
            sortValue: (u: (typeof roll.usersRanked)[number]) => u.paid,
            render: (u: (typeof roll.usersRanked)[number]) => <span className="tabular-nums text-neutral">{fmtMoney(u.paid, cur)}</span>,
          },
        ]),
  ];

  const detailColumns: Column<GenieCostRow>[] = [
    { key: "workspace", header: "Workspace", sortValue: (r) => r.workspace, render: (r) => <span className="font-mono text-xs text-neutral truncate max-w-[170px] inline-block">{r.workspace}</span> },
    { key: "user", header: "User", sortValue: (r) => r.user_identity, render: (r) => <span className="font-mono text-xs text-brand-dark truncate max-w-[190px] inline-block">{r.user_identity}</span> },
    {
      key: "surface",
      header: (
        <span className="inline-flex items-center gap-1">Surface<InfoTip text="Which Genie surface generated the usage. Genie Code = NL-to-SQL in the editor/notebook (technical users). Genie Spaces = no-code chat on a curated Space (Genie One / Databricks One, business users). Genie Agents = Genie called programmatically inside an agent (Agent Bricks / API)." /></span>
      ),
      sortValue: (r) => r.label,
      render: (r) => <span className={`pill ${surfaceStyle(r.surface).pill}`}>{r.label}</span>,
    },
    { key: "dbus", header: "DBUs", align: "right", sortValue: (r) => r.total_dbus, render: (r) => <span className="tabular-nums text-neutral">{fmtNum(r.total_dbus)}</span> },
    // In live/billable mode list === paid, so collapse to a single "Billable $".
    ...(billable
      ? [
          {
            key: "billable",
            header: (
              <span className="inline-flex items-center gap-1">Billable $<InfoTip text="Billable Genie cost for this row — spend above the 150 free DBUs/month (the billed SKU). The free tier is tracked separately as GENIE_FREE_USAGE, so this is the charged portion." /></span>
            ),
            align: "right" as const,
            sortValue: (r: GenieCostRow) => r.paid_list_cost_usd,
            render: (r: GenieCostRow) => <span className="tabular-nums text-neutral">{fmtMoney(r.paid_list_cost_usd, cur)}</span>,
          },
        ]
      : [
          { key: "list", header: "List $", align: "right" as const, sortValue: (r: GenieCostRow) => r.total_list_cost_usd, render: (r: GenieCostRow) => <span className="tabular-nums">{fmtMoney(r.total_list_cost_usd, cur)}</span> },
          { key: "paid", header: "Paid $", align: "right" as const, sortValue: (r: GenieCostRow) => r.paid_list_cost_usd, render: (r: GenieCostRow) => <span className="tabular-nums text-neutral">{fmtMoney(r.paid_list_cost_usd, cur)}</span> },
        ]),
  ];

  return (
    <PageShell
      title="Genie spend"
      subtitle="Genie cost by surface (Code / Spaces / Agents), per workspace and per user — from system.billing.usage. Figures are list price."
    >
      {loading && !gc && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {gc && (
        <>
          <FilterBar>
            <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={wsOptions} allLabel="All workspaces" />
            <SearchBox value={search} onChange={setSearch} placeholder="Filter by user or workspace…" />
          </FilterBar>

          <KpiRow cols={6}>
            <KpiCard label="Genie $ / mo" value={fmtMoney(roll.list, cur, { compact: true })} tone="accent"
              hint={gc.total_platform_spend_usd_month ? `${fmtPct(roll.list / gc.total_platform_spend_usd_month, 1)} of total spend` : "list price"}
              info="Total Genie cost this month at published list price. The hint shows it as a share of TOTAL platform spend this month (that denominator includes all products — Genie and AI included), so it's directional context (Genie is typically a small slice), not a Genie-excluded ratio." />
            <KpiCard label={billable ? "Billable $ / mo" : "Paid $ / mo"} value={fmtMoney(roll.paid, cur, { compact: true })} tone="danger" info={billable ? "Billable Genie cost this month — spend above each user's 150 free DBUs/month. This figure is already net of the free allowance." : "Estimated billable Genie cost after each user's 150 free DBUs per month are deducted. The free tier is shared across all Genie surfaces."} />
            {billable && liveFreeAbsorbed != null ? (
              /* Live + GENIE_FREE_USAGE present (20 Jul 2026+): a REAL free-absorbed
                 figure valued at the effective billed Genie rate. Estate-wide, so
                 only shown when unscoped (backend nulls it when a workspace is
                 selected). */
              <KpiCard label="Free absorbed" value={fmtMoney(liveFreeAbsorbed, cur, { compact: true })} tone="success" info={`Real free Genie usage this month from the GENIE_FREE_USAGE SKU (the 150 free DBUs/user/month allowance, recorded in system tables from 20 Jul 2026). It has no list price, so it's valued at ${freeRateIsProxy ? "the ~$0.55/DBU-hour list-price proxy (no billed Genie usage this month to derive an effective rate)" : "the estate's effective billed Genie rate"} — what the free usage would have cost. Estate-wide; not shown when filtered to one workspace.`} />
            ) : billable ? (
              <KpiCard label="Avg $ / user" value={fmtMoney(roll.users ? roll.list / roll.users : 0, cur, { compact: true })} tone="info"
                info="Average billable Genie spend per identified user this month (Genie $ ÷ Genie users). A useful intensity signal — a high average points to a few heavy Genie users worth reviewing (see the per-user table below)." />
            ) : (
              <KpiCard label="Free absorbed" value={fmtMoney(freeAbsorbed, cur, { compact: true })} tone="success" info="Genie cost covered by the 150 free DBUs per user per month allowance, so not billed." />
            )}
            <KpiCard label="Genie users" value={String(roll.users)} tone="info" info="Distinct identified users (identity_metadata.run_as) with Genie usage in the selected scope this month." />
            <KpiCard label="Workspaces" value={String(roll.workspaces)} info="Number of workspaces with Genie usage in the selected scope." />
            <KpiCard label="Surfaces" value={String(roll.surfaces.length)} info="Distinct Genie surfaces in use (Genie Code, Genie Spaces / One, Genie Agents), from usage_metadata.genie.surface." />
          </KpiRow>

          {billable ? (
            /* Live mode: the breakdown is billable usage (above the free
               allowance). Free usage is now recorded separately as the
               GENIE_FREE_USAGE SKU (20 Jul 2026+), so we show the real
               billable-vs-free picture when that figure is available. */
            <div className="card flex flex-col gap-2">
              <div className="text-sm font-semibold flex items-center gap-1.5">
                Billable Genie spend
                <InfoTip text="Genie spend that actually hits the invoice — usage above each user's 150 free DBUs/month. The free tier is recorded separately under the GENIE_FREE_USAGE SKU (from 20 Jul 2026); these billable figures are the charged portion." />
              </div>
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-2xl font-semibold tabular-nums text-brand-dark">{fmtMoney(roll.paid, cur)}</span>
                <span className="text-xs text-neutral">billable this month</span>
                {liveFreeAbsorbed != null && (
                  <span className="text-xs text-success tabular-nums">+ {fmtMoney(liveFreeAbsorbed, cur)} free absorbed</span>
                )}
              </div>
              <p className="text-[11px] leading-relaxed text-neutral">
                This is billable Genie usage — spend above each user's 150 free DBUs/month.
                {freeInSystemTables
                  ? " Free usage is now recorded in system tables (GENIE_FREE_USAGE SKU, from 20 Jul 2026); it carries no list price, so the 'free absorbed' figure values those DBUs at the effective billed Genie rate — what they would have cost."
                  : " Free usage before 20 Jul 2026 isn't recorded in system tables, so only the billable portion is shown here."}
              </p>
            </div>
          ) : (
            /* Demo mode: keep the illustrative free-vs-paid split. */
            <div className="card flex flex-col gap-3">
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <div className="text-sm font-semibold flex items-center gap-1.5">
                  Free vs paid
                  <InfoTip text="How much Genie spend the 150 free DBUs per user per month absorb versus what is actually billable. Only the paid portion hits the invoice; the free portion is covered by the monthly per-user allowance. This demo split is illustrative over synthetic totals; a live deployment reads the real free portion from the GENIE_FREE_USAGE SKU (20 Jul 2026+)." />
                </div>
                <span className="text-xs text-neutral tabular-nums">
                  {fmtPct(roll.list ? freeAbsorbed / roll.list : 0, 0)} of Genie spend covered by the free allowance
                </span>
              </div>
              <div className="flex h-5 w-full overflow-hidden rounded-full border border-border">
                <div className="bg-success flex items-center justify-center" style={{ width: `${(freeAbsorbed / (roll.list || 1)) * 100}%` }} title={`Free ${fmtMoney(freeAbsorbed, cur)}`} />
                <div className="bg-danger flex items-center justify-center" style={{ width: `${(roll.paid / (roll.list || 1)) * 100}%` }} title={`Paid ${fmtMoney(roll.paid, cur)}`} />
              </div>
              <div className="flex flex-wrap gap-x-8 gap-y-1 text-xs">
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-success" /> Free absorbed
                  <span className="tabular-nums text-brand-dark font-medium">{fmtMoney(freeAbsorbed, cur)}</span>
                  <span className="tabular-nums text-neutral">· {fmtPct(roll.list ? freeAbsorbed / roll.list : 0, 1)}</span>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-danger" /> Paid (billable)
                  <span className="tabular-nums text-brand-dark font-medium">{fmtMoney(roll.paid, cur)}</span>
                  <span className="tabular-nums text-neutral">· {fmtPct(roll.list ? roll.paid / roll.list : 0, 1)}</span>
                </span>
              </div>
              <p className="text-[11px] text-neutral">
                Illustrative free-vs-paid split — in a live deployment, system tables show only billable usage above the free allowance.
              </p>
            </div>
          )}

          {/* Spend by surface */}
          <div className="card flex flex-col gap-3">
            <div className="text-sm font-semibold flex items-center gap-1.5">
              Spend by surface
              <InfoTip text={billable
                ? "Billable Genie spend split by the surface that generated it (usage_metadata.genie.surface). These figures are already net of the free allowance, so the solid bar is the full billable spend for that surface. Genie Code = SQL editor / notebook, Genie Spaces = Genie One / Databricks One chat, Genie Agents = Agent Bricks. The surface enum is open, so the app labels whatever values appear."
                : "Genie spend split by the surface that generated it (usage_metadata.genie.surface), and within each surface the paid (solid) vs free-absorbed (faded) portion. Genie Code = SQL editor / notebook, Genie Spaces = Genie One / Databricks One chat, Genie Agents = Agent Bricks. The surface enum is open, so the app labels whatever values appear."} />
            </div>
            <div className="flex flex-col gap-2.5">
              {roll.surfaces.map((s) => {
                // Bar sized to TOTAL list value (real usage on the surface), split
                // solid = billable ($ above the free allowance), faded = free
                // absorbed. This is the fix for the "Agents/Spaces = $0" bug: a
                // free-tier-heavy surface (e.g. Agents run inside the 150 free
                // DBUs) now shows its real magnitude with the free portion faded,
                // instead of a zero-width bar. Works in both live and demo (live
                // free = list − billable, from the GENIE_FREE_USAGE SKU).
                const free = Math.max(0, s.list - s.paid);
                return (
                  <div key={s.surface} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="flex items-center gap-1.5">
                        <span className={`h-2.5 w-2.5 rounded-sm ${surfaceStyle(s.surface).dot}`} />
                        {s.label}
                        <InfoTip text={surfaceHelp(s.surface)} label={`What is ${s.label}?`} />
                      </span>
                      <span className="tabular-nums text-neutral">
                        {billable
                          ? <>{fmtMoney(s.paid, cur, { compact: true })} billable{free > 0 ? <> · {fmtMoney(free, cur, { compact: true })} free</> : null} · {fmtPct(s.list / (roll.list || 1), 1)}</>
                          : <>{fmtMoney(s.list, cur, { compact: true })} · paid {fmtMoney(s.paid, cur, { compact: true })} · free {fmtMoney(free, cur, { compact: true })} · {fmtPct(s.list / (roll.list || 1), 1)}</>}
                      </span>
                    </div>
                    {/* Bar scaled to the biggest surface (by total list value). Solid = billable, faded = free absorbed. */}
                    <div className="h-1.5 rounded-full bg-border/60 overflow-hidden flex" style={{ width: `${(s.list / maxSurface) * 100}%` }}>
                      <div className={`h-full ${surfaceStyle(s.surface).bar}`} style={{ width: `${s.list ? (s.paid / s.list) * 100 : 0}%` }} title={`Billable ${fmtMoney(s.paid, cur)}`} />
                      <div className={`h-full ${surfaceStyle(s.surface).bar} opacity-30`} style={{ width: `${s.list ? (free / s.list) * 100 : 0}%` }} title={`Free ${fmtMoney(free, cur)}`} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Top Genie users */}
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold flex items-center gap-1.5">
              Top Genie users
              <InfoTip text="Users ranked by Genie list cost, with their dominant surface and how many workspaces they use Genie in. Useful for spotting who to coach toward a curated Genie Space." />
              {roll.usersRanked.length > USER_CAP && (
                <span className="text-xs font-normal text-neutral">top {USER_CAP} of {roll.usersRanked.length}</span>
              )}
            </h3>
            <DataTable columns={userColumns} rows={topUsers} rowKey={(u) => u.user} initialSort={{ key: billable ? "billable" : "list", dir: "desc" }} emptyMessage="No Genie usage for this scope." />
          </div>

          {/* Per user x workspace x surface detail */}
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold flex items-center gap-1.5">
              Per user · workspace · surface
              <InfoTip text="The ground-truth Genie usage rows: one line per user, workspace and surface, with DBUs, list $, and estimated paid $ after the free allowance." />
              {rows.length > DETAIL_CAP && (
                <span className="text-xs font-normal text-neutral">top {DETAIL_CAP} of {rows.length} by $</span>
              )}
            </h3>
            <DataTable columns={detailColumns} rows={detailRows} rowKey={(r) => `${r.workspace}|${r.user_identity}|${r.surface}`} initialSort={{ key: billable ? "billable" : "list", dir: "desc" }} emptyMessage="No Genie usage for this scope." />
          </div>

          {/* Honesty caveats */}
          <div className="card flex flex-col gap-2 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-neutral">How to read this</div>
            <ul className="flex flex-col gap-1 text-[11px] text-neutral">
              {gc.caveats.map((c, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-warning shrink-0" aria-hidden>·</span>
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </PageShell>
  );
}
