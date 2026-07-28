/**
 * Typed fetch wrappers for the Estate Command Center backend.
 * In dev, requests are proxied to http://localhost:8000 by Vite.
 * In production, the SPA is served by FastAPI on the same origin.
 */
import type {
  AccessTree,
  AppConfig,
  Attribution,
  CostDrivers,
  DqmMonitor,
  DqmPruneCandidate,
  DqmSummary,
  DqmWorkspaceCost,
  Envelope,
  GenieCost,
  AiCost,
  Adoption,
  Classification,
  Grant,
  GovernanceReport,
  GroupNode,
  HubRec,
  HubSummary,
  JobRow,
  JobsSummary,
  JobRunSample,
  JobTerminationCode,
  JobDurationPoint,
  JobCostPoint,
  Overview,
  ExecSummary,
  Pat,
  Principal,
  PrincipalAccess,
  QueryRow,
  QueryRewrite,
  WarehouseRow,
  WarehouseSummary,
  ClusterRow,
  ClusterSummary,
  Risk,
  SecurableAccess,
  TableRow,
  TablesSummary,
  Workspace,
  WorkspaceDetail,
} from "../types";

const API_BASE = "/api";

async function getEnvelope<T, S = unknown>(path: string): Promise<Envelope<T, S>> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    // Surface the server's `detail` (fail-closed live reads put the exact
    // reason + fix here, e.g. a missing SELECT grant) rather than a bare code.
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = ` — ${body.detail}`;
    } catch {
      /* non-JSON body; fall back to status text */
    }
    throw new Error(`API ${path} failed: ${res.status}${detail || " " + res.statusText}`);
  }
  return (await res.json()) as Envelope<T, S>;
}

async function postEnvelope<T, S = unknown>(path: string, body: unknown): Promise<Envelope<T, S>> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const b = (await res.json()) as { detail?: string };
      if (b?.detail) detail = ` — ${b.detail}`;
    } catch {
      /* non-JSON body */
    }
    throw new Error(`API ${path} failed: ${res.status}${detail || " " + res.statusText}`);
  }
  return (await res.json()) as Envelope<T, S>;
}

function qs(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "" && v !== "all")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export async function fetchConfig(): Promise<AppConfig> {
  const res = await fetch(`${API_BASE}/config`);
  // Bootstrap call — guard res.ok like the envelope helpers so a 5xx (HTML
  // error page) surfaces a clear message instead of a cryptic JSON SyntaxError
  // that would silently break app mount.
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = ` — ${body.detail}`;
    } catch {
      /* non-JSON body */
    }
    throw new Error(`API /config failed: ${res.status}${detail || " " + res.statusText}`);
  }
  return (await res.json()) as AppConfig;
}

export function fetchOverview(
  params: { workspace?: string; time_range?: string } = {},
): Promise<Envelope<Overview>> {
  return getEnvelope<Overview>(`/overview${qs(params)}`);
}

// Optional AI narrative for the landing view. Only called when features.ai_narration
// is on (the button that triggers it is hidden otherwise). Returns status
// 'unavailable' if the backend has AI off — the caller simply shows nothing.
export function postExecSummary(): Promise<Envelope<ExecSummary>> {
  return postEnvelope<ExecSummary>(`/overview/exec-summary`, {});
}

// --- Acknowledge / ignore (writeback tier; only called when features.writeback) ---
export interface AckRecord {
  source: string;
  entity_key: string;
  status: string;
  reason?: string;
  metric_name?: string;
  metric_at_ack?: number;
  snooze_until?: string;
  updated_by?: string;
  updated_at?: string;
}
export function postAck(body: {
  source: string; entity_key: string; status: string;
  reason?: string; metric_name?: string; metric_at_ack?: number; snooze_days?: number;
}): Promise<Envelope<{ ok: boolean }>> {
  return postEnvelope<{ ok: boolean }>(`/acks`, body);
}
export async function deleteAck(source: string, entityKey: string): Promise<void> {
  // Must check res.ok: fetch does NOT reject on a non-2xx status, so without this
  // a failed reopen (e.g. 503 when the writeback store is unavailable) would
  // resolve as success — the UI would close the popover and refetch, showing the
  // item STILL suppressed, i.e. the "reopen button does nothing" symptom. Throw
  // like postEnvelope so callers can surface the failure instead of faking success.
  const res = await fetch(`${API_BASE}/acks/${encodeURIComponent(source)}/${encodeURIComponent(entityKey)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.detail ? `: ${body.detail}` : "";
    } catch {
      /* non-JSON body */
    }
    throw new Error(`Could not reopen (${res.status})${detail}`);
  }
}

export function fetchCostDrivers(
  params: { workspace?: string } = {},
): Promise<Envelope<CostDrivers>> {
  return getEnvelope<CostDrivers>(`/cost-drivers${qs(params)}`);
}

export function fetchWorkspaces(
  params: Record<string, string> = {},
): Promise<Envelope<Workspace[]>> {
  return getEnvelope<Workspace[]>(`/workspaces${qs(params)}`);
}

// Lightweight {workspace, workspace_id} list for filter dropdowns — avoids
// pulling the full ~1.8MB /workspaces payload (4k+ rows w/ detail) just for names.
export function fetchWorkspaceOptions(): Promise<Envelope<{ workspace: string; workspace_id: string }[]>> {
  return getEnvelope<{ workspace: string; workspace_id: string }[]>(`/workspaces/options`);
}

export function fetchWorkspaceDetail(
  workspaceId: string,
): Promise<Envelope<WorkspaceDetail>> {
  return getEnvelope<WorkspaceDetail>(`/workspaces/${encodeURIComponent(workspaceId)}`);
}

// --- Jobs (job cost & health, observable facts only; always on) ---
// The envelope carries the row list in `data`, the KPI rollup in `summary`, and
// (live mode only, on a degraded read) an optional `message` explaining an empty
// list — surfaced by the JobsPage instead of an error.
export interface JobsEnvelope extends Envelope<JobRow[], JobsSummary> {
  message?: string;
}

export function fetchJobs(
  params: Record<string, string> = {},
): Promise<JobsEnvelope> {
  return getEnvelope<JobRow[], JobsSummary>(`/jobs${qs(params)}`) as Promise<JobsEnvelope>;
}

// Lazy per-job drill-down (recent runs + termination codes + trends). Fetched
// on row-expand: the list endpoint leaves recent_runs/termination_codes empty
// in live (not bulk-scannable) and drops the trend arrays for payload size.
export interface JobDrilldown {
  recent_runs: JobRunSample[];
  termination_codes: JobTerminationCode[];
  duration_trend?: JobDurationPoint[];
  cost_trend?: JobCostPoint[];
}
export function fetchJobDetail(workspaceId: string, jobId: string): Promise<Envelope<JobDrilldown>> {
  return getEnvelope<JobDrilldown>(`/jobs/${encodeURIComponent(workspaceId)}/${encodeURIComponent(jobId)}`);
}

export function fetchQueries(
  params: Record<string, string | number> = {},
): Promise<Envelope<QueryRow[]>> {
  return getEnvelope<QueryRow[]>(`/queries${qs(params)}`);
}


export interface WarehousesEnvelope extends Envelope<WarehouseRow[], WarehouseSummary> {
  message?: string;
  /** Distinct warehouses active in the window (universe behind the top-N list). */
  total_active?: number;
  /** How many rows are actually returned (the top-N cap). */
  shown?: number;
  /** True when total_active > shown, so the UI can say "top N of M". */
  truncated?: boolean;
}
export function fetchWarehouses(
  params: Record<string, string> = {},
): Promise<WarehousesEnvelope> {
  return getEnvelope<WarehouseRow[], WarehouseSummary>(`/warehouses${qs(params)}`) as Promise<WarehousesEnvelope>;
}

export interface ClustersEnvelope extends Envelope<ClusterRow[], ClusterSummary> {
  message?: string;
}
export function fetchClusters(
  params: Record<string, string> = {},
): Promise<ClustersEnvelope> {
  return getEnvelope<ClusterRow[], ClusterSummary>(`/clusters${qs(params)}`) as Promise<ClustersEnvelope>;
}

export function fetchTables(
  params: Record<string, string> = {},
): Promise<Envelope<TableRow[], TablesSummary>> {
  return getEnvelope<TableRow[], TablesSummary>(`/tables${qs(params)}`);
}

export function fetchPrincipals(
  params: Record<string, string> = {},
): Promise<Envelope<Principal[]>> {
  return getEnvelope<Principal[]>(`/access/principals${qs(params)}`);
}

export function fetchGrants(
  params: Record<string, string> = {},
): Promise<Envelope<Grant[]>> {
  return getEnvelope<Grant[]>(`/access/grants${qs(params)}`);
}

export function fetchRisks(
  params: Record<string, string> = {},
): Promise<Envelope<Risk[]>> {
  return getEnvelope<Risk[]>(`/access/risks${qs(params)}`);
}

export function fetchAccessTree(): Promise<Envelope<AccessTree>> {
  return getEnvelope<AccessTree>(`/access/tree`);
}

export function fetchGroupTree(): Promise<Envelope<GroupNode[]>> {
  return getEnvelope<GroupNode[]>(`/access/groups`);
}

export function fetchPats(): Promise<Envelope<Pat[]>> {
  return getEnvelope<Pat[]>(`/access/pats`);
}

export function fetchPrincipalAccess(principal: string): Promise<Envelope<PrincipalAccess>> {
  return getEnvelope<PrincipalAccess>(`/access/principal/${encodeURIComponent(principal)}`);
}

export function fetchSecurableAccess(fqn: string): Promise<Envelope<SecurableAccess>> {
  return getEnvelope<SecurableAccess>(`/access/securable?fqn=${encodeURIComponent(fqn)}`);
}

export function fetchGovernance(
  params: Record<string, string> = {},
): Promise<Envelope<GovernanceReport>> {
  return getEnvelope<GovernanceReport>(`/governance${qs(params)}`);
}

// The hub envelope carries the rec list in `data`, plus a `summary` (KPIs) and
// an `attribution` rollup alongside it. `attribution` is optional at the type
// level: `getEnvelope` only guarantees the base shape, and a degraded backend
// could omit it — so consumers must guard (they do: `data?.attribution`).
export interface HubEnvelope extends Envelope<HubRec[], HubSummary> {
  attribution?: Attribution;
}

export function fetchRecommendationsHub(
  params: Record<string, string> = {},
): Promise<HubEnvelope> {
  return getEnvelope<HubRec[], HubSummary>(`/recommendations/hub${qs(params)}`) as Promise<HubEnvelope>;
}

// --- Genie cost attribution (SPEC §13; always on) --------------------------

export function fetchGenieCost(
  params: { workspace?: string } = {},
): Promise<Envelope<GenieCost>> {
  return getEnvelope<GenieCost>(`/genie-cost${qs(params)}`);
}

// --- AI cost attribution (all AI billing products; always on) --------------

export function fetchAiCost(
  params: { workspace?: string } = {},
): Promise<Envelope<AiCost>> {
  return getEnvelope<AiCost>(`/ai-cost${qs(params)}`);
}

// --- Adoption & Value (always on) ------------------------------------------

export function fetchAdoption(
  params: Record<string, string> = {},
): Promise<Envelope<Adoption>> {
  return getEnvelope<Adoption>(`/adoption${qs(params)}`);
}

export function fetchClassification(): Promise<Envelope<Classification>> {
  return getEnvelope<Classification>(`/classification`);
}

// --- Data Quality Monitoring (SPEC §12; gated by features.dqm) -------------

// Optional at the type level (the base fetch only guarantees {data, summary});
// consumers default them (`data?.by_workspace ?? []`) so a degraded backend
// that omits them can't crash the page.
export interface DqmEnvelope extends Envelope<DqmMonitor[], DqmSummary> {
  by_workspace?: DqmWorkspaceCost[];
  prune_candidates?: DqmPruneCandidate[];
  billing_line?: string;
  caveat?: string;
}

export function fetchDqm(
  params: Record<string, string> = {},
): Promise<DqmEnvelope> {
  return getEnvelope<DqmMonitor[], DqmSummary>(`/dqm${qs(params)}`) as Promise<DqmEnvelope>;
}

// --- EXPLAIN-validated query rewrite (Increment 3) -------------------------

export function postQueryRewrite(row: QueryRow): Promise<Envelope<QueryRewrite>> {
  // Send only the fields the backend needs; live mode uses statement_id to
  // recover the full (untruncated) SQL before generating + EXPLAIN-validating.
  const body = {
    id: row.id,
    statement_id: row.statement_id,
    query_text: row.query_text,
    target_table: row.target_table ?? null,
    pruning_efficiency: row.pruning_efficiency,
    bytes_read: row.bytes_read,
    spill_gb: row.spill_gb,
    p95_s: row.p95_s,
    runs: row.runs,
  };
  return postEnvelope<QueryRewrite>("/queries/rewrite", body);
}

// --- Ask Genie SSE (SPEC §11; gated by features.genie) ---------------------

export async function postGenieAsk(question: string): Promise<Response> {
  return fetch(`${API_BASE}/genie/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}
