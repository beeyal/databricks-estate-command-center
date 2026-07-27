import { StatusPill } from "../shared/Pill";
import { InfoTip } from "../shared/InfoTip";
import { ProgressBar } from "../shared/ProgressBar";
import { fmtMoney, fmtPct } from "../shared/format";
import type { MoneyCurrency } from "../shared/format";
import { useAppStore, useCurrency } from "../../store/appStore";
import type { GovernancePageId, GovernanceTile, Status } from "../../types";

const BORDER: Record<Status, string> = {
  Good: "border-l-success",
  Warning: "border-l-warning",
  Critical: "border-l-danger",
  Unknown: "border-l-neutral",
};

// map a tile status to a ProgressBar tone
const BAR_TONE: Record<Status, "success" | "warning" | "danger" | "info"> = {
  Good: "success",
  Warning: "warning",
  Critical: "danger",
  Unknown: "info",
};

// Plain-language definition per tile, keyed by the tile's metric name. Explains
// what the control evaluates and the unit shown, matching data/generate_demo.py.
const TILE_INFO: Record<string, string> = {
  "Tagging coverage":
    "Share of monthly spend on resources that carry cost-attribution tags. Higher means more spend can be traced to a business unit or cost centre.",
  "Untagged spend":
    "Dollars per month on resources with no cost-attribution tags, so the cost cannot be charged back to any business unit or cost centre.",
  "Budget-policy coverage":
    "Share of workspaces with a serverless budget policy that caps and tags serverless compute spend. Higher means more spend is guard-railed.",
  "Account-level budgets":
    "Count of Account Console budgets configured with spend thresholds and alerts, versus the number planned. More means better early warning on overspend.",
  "Alert / anomaly setup":
    "How well spend-anomaly alerts on billing usage are wired up. Low coverage means cost spikes can go unnoticed until the invoice arrives.",
  "Predictive Optimization adoption":
    "Share of managed tables with Predictive Optimization, which auto-runs OPTIMIZE, VACUUM and ANALYZE. Higher means lower storage and query cost with no manual tuning.",
  "Liquid Clustering adoption":
    "Share of managed tables using CLUSTER BY liquid clustering instead of rigid partitioning. Higher means better data layout and cheaper queries.",
  "External / unmanaged data":
    "Share of tables that are external rather than Unity Catalog managed. Lower is better, since external tables miss auto-maintenance and catalog-managed commits.",
  "Certification coverage":
    "Share of key gold tables carrying a certified badge so consumers know they are trusted. Higher means clearer trust signals on curated data.",
  "Lineage coverage":
    "Share of tables with captured data lineage. Gaps come from external or notebook writes and weaken impact analysis and audit.",
  "Cluster-policy coverage":
    "Share of clusters bound to a cost-control policy that caps DBU, sets auto-terminate and limits nodes. Higher means fewer runaway interactive clusters.",
  "Over-privileged principals":
    "Count of identities holding MODIFY or ALL PRIVILEGES on production data. Lower is better, since broad write access is a cost and security risk.",
  "PAT / token risk":
    "Count of personal access tokens, long-lived credentials, older than 90 days. Lower is better; stale tokens should be rotated or replaced with short-lived OAuth.",
};

const PAGE_LABEL: Record<GovernancePageId, string> = {
  overview: "Overview",
  access: "Access",
  workspaces: "Workspaces",
  jobs: "Jobs",
  queries: "Query Advisor",
  warehouses: "Warehouses",
  clusters: "Clusters",
  tables: "Tables",
  adoption: "Adoption & Value",
  genie: "Genie $",
  ai: "AI $",
  recommendations: "Recommendations",
  dqm: "Data Quality",
};

function tileValue(t: GovernanceTile, cur: MoneyCurrency): string {
  if (t.value_pct !== undefined) return fmtPct(t.value_pct);
  if (t.value_usd !== undefined) return fmtMoney(t.value_usd, cur, { compact: true });
  if (t.value_count !== undefined) return String(t.value_count);
  return "—";
}

export function GovernanceTileCard({ tile }: { tile: GovernanceTile }) {
  const setActivePage = useAppStore((s) => s.setActivePage);
  const cur = useCurrency();
  const showBar = tile.value_pct !== undefined;

  return (
    <div className={`card border-l-4 ${BORDER[tile.status]} flex flex-col gap-2`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="text-sm font-medium flex items-center gap-1">
            <span className="truncate">{tile.metric}</span>
            {TILE_INFO[tile.metric] && <InfoTip text={TILE_INFO[tile.metric]} label={`What is ${tile.metric}?`} />}
          </span>
          {tile.category && (
            <span className="text-[10px] uppercase tracking-wide text-neutral">{tile.category}</span>
          )}
        </div>
        <StatusPill status={tile.status} />
      </div>

      <div className="text-2xl font-semibold tabular-nums leading-none">{tileValue(tile, cur)}</div>

      {showBar && (
        <ProgressBar value={tile.value_pct ?? 0} tone={BAR_TONE[tile.status]} showPct={false} />
      )}

      <div className="text-xs text-neutral">{tile.gap}</div>

      <div className="text-xs mt-1 pt-2 border-t border-border/60">
        <span className="text-neutral">Next: </span>
        {tile.action}
      </div>

      {tile.ties_to && (
        <div className="flex items-center flex-wrap gap-2 mt-1">
          <button
            type="button"
            onClick={() => setActivePage(tile.ties_to as GovernancePageId)}
            className="pill bg-border/40 text-neutral hover:text-brand-dark transition"
            title={`Open the ${PAGE_LABEL[tile.ties_to]} page`}
          >
            → {PAGE_LABEL[tile.ties_to]}
          </button>
        </div>
      )}
    </div>
  );
}
