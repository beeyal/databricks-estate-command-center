import { useMemo, useState } from "react";
import { useCurrency } from "../../store/appStore";
import { InfoTip } from "./InfoTip";
import { fmtMoney, fmtNum } from "./format";
import type { PlatformEnvMatrix as Matrix } from "../../types";

// Platform × environment grid: "pick a platform, see its Dev / Test / Pre-prod /
// Prod". The platform is the workspace name with its environment word removed
// (dev-forecasting / test-forecasting / prod-forecasting → forecasting), so a
// team can look at one product across its environments. Click a platform to
// filter to it (all environments), a cell for that platform in one environment,
// or a column header for one environment across every platform. The grid is
// computed before those filters on the server, so it stays a navigator.

const NONPROD = new Set(["Dev", "Test", "Pre-prod", "Sandbox", "Non-prod"]);
const DEFAULT_ROWS = 10;

type SortKey = "nonprod" | "total" | "name";

export function PlatformEnvMatrix({
  matrix: platformMatrix,
  buMatrix,
  itemLabel,
  showScheduled = false,
  platform,
  bu = [],
  tier,
  onSelect,
  onSelectBu,
}: {
  matrix: Matrix | undefined;
  buMatrix?: Matrix;               // same grid with business units as rows (BU mapping configured)
  itemLabel: string;               // "jobs" | "workspaces" | "pipelines"
  showScheduled?: boolean;
  platform: string[];
  bu?: string[];
  tier: string[];
  onSelect: (platform: string[], tier: string[]) => void;
  onSelectBu?: (bu: string[], tier: string[]) => void;
}) {
  const cur = useCurrency();
  // Rows by platform (default) or business unit. Platform is the better default
  // when names carry a convention; if no platform spans two environments but a
  // BU mapping exists, start on BU.
  const [rowsByChoice, setRowsBy] = useState<"platform" | "bu" | null>(null);
  const canBu = !!buMatrix && !!onSelectBu;
  const rowsBy = rowsByChoice ?? (canBu && (platformMatrix?.num_multi_env ?? 0) === 0 ? "bu" : "platform");
  const matrix = rowsBy === "bu" && canBu ? buMatrix : platformMatrix;
  const rowNoun = rowsBy === "bu" ? "business unit" : "platform";
  const [sort, setSort] = useState<SortKey>("nonprod");
  const [multiOnly, setMultiOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);

  const rows = useMemo(() => {
    let r = matrix?.rows ?? [];
    if (multiOnly) r = r.filter((p) => p.multi_env);
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      r = r.filter((p) => p.platform.toLowerCase().includes(q) || p.workspaces.some((w) => w.toLowerCase().includes(q)));
    }
    const sorted = [...r];
    if (sort === "total") sorted.sort((a, b) => b.total_cost - a.total_cost);
    else if (sort === "name") sorted.sort((a, b) => a.platform.localeCompare(b.platform, undefined, { sensitivity: "base", numeric: true }));
    // "nonprod" = server order (non-prod cost, then total)
    return sorted;
  }, [matrix, multiOnly, query, sort]);

  if (!matrix || !matrix.rows.length) return null;
  const cov = matrix.coverage;
  const noEnv = !matrix.tiers.some((t) => t !== "Unclassified");
  // Without environment words (or mapping-table environments) the grid is one
  // "Unclassified" column — say so plainly instead of drawing it.
  if (noEnv || (rowsBy === "platform" && matrix.num_multi_env === 0 && !canBu)) {
    return (
      <div className="card border-l-4 border-l-info text-xs text-neutral leading-relaxed py-2.5">
        <span className="font-medium text-brand-dark">Environment breakdown isn't available yet. </span>
        {noEnv
          ? "None of these workspace names contain an environment word (DEV, TST, UAT, PPD, STG, PRD…), so the app can't tell Dev from Prod."
          : "No platform spans more than one environment, so the workspace names don't share a pattern the app can group on."}
        {" "}Add <span className="font-mono">environment</span> (and optionally <span className="font-mono">platform</span>) columns to the workspace mapping table, or set <span className="font-mono">FINOPS_WORKSPACE_GROUPS</span> / <span className="font-mono">FINOPS_ENV_TIERS</span> — see the deployment guide.
      </div>
    );
  }

  const selValues = rowsBy === "bu" ? bu : platform;
  const select = (rowsVal: string[], t: string[]) => (rowsBy === "bu" && onSelectBu ? onSelectBu(rowsVal, t) : onSelect(rowsVal, t));
  const selP = selValues.length === 1 ? selValues[0].toLowerCase() : null;
  const selT = tier.length === 1 ? tier[0] : null;
  const visible = showAll ? rows : rows.slice(0, DEFAULT_ROWS);
  const hasSelection = selValues.length > 0 || tier.length > 0;

  const cellBtn = "w-full text-right rounded px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none";

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          By {rowNoun} and environment
          <InfoTip text={`${rowsBy === "bu" ? "Each row is a business unit from your workspace mapping table." : "Each row is a platform. In order of precedence it comes from the platform column of your workspace mapping table, your naming rules (FINOPS_WORKSPACE_GROUPS), or the workspace name with its environment word removed — so dev-forecasting, test-forecasting and prod-forecasting are one row."} Columns are the environment, from the mapping table's environment column or the workspace name (DEV, TST/UAT, PPD/STG, PRD…; configurable with FINOPS_ENV_TIERS). Click a row for all its environments, a cell for one environment, or a column header for that environment everywhere.${showScheduled ? " 'sched' = jobs on a time or event trigger that isn't paused — they keep running until someone pauses them." : ""}`} />
        </h3>
        {canBu && (
          <div className="inline-flex rounded border border-border overflow-hidden text-xs" role="group" aria-label="Rows">
            {(["platform", "bu"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={rowsBy === k}
                onClick={() => { setRowsBy(k); setShowAll(false); setQuery(""); }}
                className={`px-2 py-0.5 ${rowsBy === k ? "bg-accent/15 text-accent" : "text-neutral hover:bg-surface"}`}>
                {k === "bu" ? "Business unit" : "Platform"}
              </button>
            ))}
          </div>
        )}
        <span className="text-xs text-neutral">
          {fmtNum(matrix.num_platforms)} {rowNoun}{matrix.num_platforms === 1 ? "" : "s"}
          {matrix.num_multi_env > 0 && <> · {fmtNum(matrix.num_multi_env)} in more than one environment</>}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Find a ${rowNoun}…`}
            aria-label={`Find a ${rowNoun}`}
            className="bg-surface border border-border rounded px-2 py-1 w-40 focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none"
          />
          {matrix.num_multi_env > 0 && (
            <label className="inline-flex items-center gap-1 text-neutral cursor-pointer">
              <input type="checkbox" checked={multiOnly} onChange={(e) => setMultiOnly(e.target.checked)} />
              Several environments only
            </label>
          )}
          <label className="inline-flex items-center gap-1 text-neutral">
            Sort
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="bg-surface border border-border rounded px-1.5 py-1"
              aria-label="Sort platforms"
            >
              <option value="nonprod">Non-prod cost</option>
              <option value="total">Total cost</option>
              <option value="name">Name</option>
            </select>
          </label>
          {hasSelection && (
            <button type="button" onClick={() => select([], [])} className="pill px-2 py-0.5 border border-border text-neutral hover:text-brand-dark hover:bg-surface">
              Clear selection
            </button>
          )}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead>
            <tr>
              <th scope="col" className="text-left font-medium text-neutral uppercase tracking-wide text-[10px] px-2 pb-1.5">{rowsBy === "bu" ? "Business unit" : "Platform"}</th>
              {matrix.tiers.map((t) => (
                <th key={t} scope="col" className="text-right px-1 pb-1.5">
                  <button
                    type="button"
                    onClick={() => select([], selT === t && !selP ? [] : [t])}
                    aria-pressed={selT === t && !selP}
                    className={`uppercase tracking-wide text-[10px] font-medium px-2 py-0.5 rounded hover:bg-surface focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none ${selT === t && !selP ? "text-accent" : NONPROD.has(t) ? "text-warning" : "text-neutral"}`}
                  >
                    {t}
                  </button>
                </th>
              ))}
              <th scope="col" className="text-right font-medium text-neutral uppercase tracking-wide text-[10px] px-2 pb-1.5">Total</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((p) => {
              const rowSel = selP === p.platform.toLowerCase();
              return (
                <tr key={p.platform.toLowerCase()} className={rowSel ? "bg-accent/5" : ""}>
                  <th scope="row" className="text-left font-normal border-t border-border/50 px-2 py-1 max-w-[260px]">
                    <button
                      type="button"
                      onClick={() => select(rowSel && !selT ? [] : [p.platform], [])}
                      aria-pressed={rowSel && !selT}
                      title={p.workspaces.join(", ")}
                      className="text-left w-full rounded px-1 py-1 hover:bg-surface focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none"
                    >
                      <div className="font-medium text-brand-dark truncate">{p.platform}</div>
                      <div className="text-[10px] text-neutral">
                        {fmtNum(p.num_workspaces)} workspace{p.num_workspaces === 1 ? "" : "s"}
                        {showScheduled && p.nonprod_scheduled > 0 && (
                          <span className="text-warning"> · {fmtNum(p.nonprod_scheduled)} non-prod scheduled</span>
                        )}
                      </div>
                    </button>
                  </th>
                  {matrix.tiers.map((t) => {
                    const c = p.cells[t];
                    const sel = rowSel && selT === t;
                    if (!c) return <td key={t} className="border-t border-border/50 text-right px-2 text-neutral/40">—</td>;
                    const warn = NONPROD.has(t) && (showScheduled ? c.scheduled > 0 : c.cost > 0);
                    return (
                      <td key={t} className="border-t border-border/50 px-1 py-1">
                        <button
                          type="button"
                          onClick={() => select(sel ? [] : [p.platform], sel ? [] : [t])}
                          aria-pressed={sel}
                          aria-label={`${p.platform} ${t}: ${fmtMoney(c.cost, cur, { compact: true })}`}
                          className={`${cellBtn} ${sel ? "bg-accent/15 ring-1 ring-accent/50" : warn ? "bg-warning/10 hover:bg-warning/20" : "hover:bg-surface"}`}
                        >
                          <div className="tabular-nums font-medium text-brand-dark">{fmtMoney(c.cost, cur, { compact: true })}</div>
                          <div className="text-[10px] text-neutral tabular-nums">
                            {fmtNum(c.count)} {c.count === 1 ? itemLabel.replace(/s$/, "") : itemLabel}
                            {showScheduled && c.scheduled > 0 && <> · {fmtNum(c.scheduled)} sched</>}
                          </div>
                        </button>
                      </td>
                    );
                  })}
                  <td className="border-t border-border/50 text-right px-2 tabular-nums font-medium">{fmtMoney(p.total_cost, cur, { compact: true })}</td>
                </tr>
              );
            })}
            {!visible.length && (
              <tr><td colSpan={matrix.tiers.length + 2} className="text-center text-neutral py-3">Nothing matches.</td></tr>
            )}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" className="text-left text-[10px] uppercase tracking-wide text-neutral border-t border-border px-3 pt-1.5">All</th>
              {matrix.tiers.map((t) => (
                <td key={t} className="text-right tabular-nums text-neutral border-t border-border px-3 pt-1.5">{fmtMoney(matrix.tier_totals[t] ?? 0, cur, { compact: true })}</td>
              ))}
              <td className="text-right tabular-nums font-medium border-t border-border px-2 pt-1.5">
                {fmtMoney(Object.values(matrix.tier_totals).reduce((s, v) => s + v, 0), cur, { compact: true })}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {cov && (cov.env_unclassified > 0 || cov.env_from_mapping > 0) && (
        <div className="text-[11px] text-neutral">
          Environment comes from{" "}
          {cov.env_from_mapping > 0 && <>your mapping table for {Math.round(cov.env_from_mapping * 100)}% of cost, </>}
          workspace names for {Math.round(cov.env_from_name * 100)}%
          {cov.env_unclassified > 0 && (
            <>; <span className="text-warning">{Math.round(cov.env_unclassified * 100)}% isn't recognised</span> (the Unclassified column) — add those workspaces' <span className="font-mono">environment</span> to the mapping table</>
          )}.
        </div>
      )}
      {rows.length > DEFAULT_ROWS && (
        <button type="button" onClick={() => setShowAll((v) => !v)} className="self-start text-xs text-neutral hover:text-brand-dark underline-offset-2 hover:underline">
          {showAll ? "Show fewer" : `Show all ${fmtNum(rows.length)}`}
        </button>
      )}
    </div>
  );
}
