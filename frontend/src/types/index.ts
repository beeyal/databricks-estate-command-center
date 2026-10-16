// Shared types for the Estate Command Center frontend.

export type PageId =
  | "overview"
  | "access"
  | "workspaces"
  | "jobs"
  | "pipelines"
  | "queries"
  | "warehouses"
  | "clusters"
  | "tables"
  | "governance"
  | "adoption"
  | "genie"
  | "ai"
  | "recommendations"
  | "dqm";

// Deploy-time feature flags (SPEC §10). Read once from /api/config; gate the
// Ask Genie banner (genie), the Data Quality tab (dqm) and LLM narration
// (ai_narration). Deterministic recommender + Genie-cost are always on.
export interface Features {
  genie: boolean;
  ai_narration: boolean;
  dqm: boolean;
  // NOT a feature toggle — writeback (acknowledge/dismiss) is built in. This is its
  // OPERATIONAL STATUS from /api/config: true when the store can persist, false when
  // it can't (live + Delta backend with no writable schema/grant). The UI shows the
  // ack controls only when true.
  writeback?: boolean;
}

// Acknowledge/ignore state overlaid onto any alert/rec row when writeback is on.
export type AckStatus = "open" | "acknowledged" | "wont_fix" | "in_progress";
export interface AckFields {
  ack_status?: AckStatus;
  reopened?: boolean;
  reopen_reason?: string;
  ack_reason?: string;
  ack_updated_by?: string;
  ack_updated_at?: string;
}

export type Health = "Good" | "Warning" | "Critical";
export type Complexity = "Simple" | "Moderate" | "Complex";
export type InsightType =
  | "cluster"
  | "resize"
  | "rewrite"
  | "vacuum"
  | "optimize"
  | "monitor"
  | "convert-to-managed"
  | "enable-PO"
  | "enable-LC";
export type Status = "Good" | "Warning" | "Critical" | "Unknown";

// Every API payload is wrapped with demo/mode metadata. Some endpoints
// (e.g. /api/tables) also attach a `summary` block of pre-aggregated KPIs.
export interface Envelope<T, S = unknown> {
  demo: boolean;
  mode: "demo" | "live";
  // In live mode, true when this panel is not yet wired to live data and is
  // showing clearly-marked sample data instead.
  live_pending?: boolean;
  data: T;
  summary?: S;
  // ISO timestamp of the last admin-API ingest (finops_admin_sync job). Present
  // on /governance, /access/groups and /access/pats envelopes; "" if unknown.
  last_refreshed?: string;
  // Admin dataset keys not yet ingested into finops_admin (e.g. ["budgets",
  // "tokens"]). Present on /governance; empty when all present or in demo mode.
  pending_admin?: string[];
  // Acknowledge/ignore counts by status ({acknowledged, wont_fix, in_progress}),
  // for the status-filter chips. Present on every alert surface when writeback is
  // on (recommendations/jobs/warehouses/clusters/tables/queries).
  ack_counts?: Record<string, number>;
  // Set on /jobs when the shipped list was capped for payload size: how many rows
  // are shown vs how many matched. The UI shows a calm note + a "Load all" action.
  list_capped?: { shown: number; total: number };
  // Soft degradation note (e.g. an ungranted live table) — distinct from a capped
  // list; rendered as a calm warning, not an error.
  message?: string;
}

export interface CurrencyOption {
  code: string; // "USD" | "AUD"
  symbol: string;
  rate: number; // multiply a USD figure by this
  label: string;
}

export interface AppConfig {
  app_name: string;
  app_short: string;
  mode: "demo" | "live";
  demo: boolean;
  // Set when USE_LIVE was requested but the app fell back to demo because the
  // warehouse / gold schema aren't configured yet — drives the "finish setup" banner.
  live_setup_note?: string;
  customer: string;
  currencies?: CurrencyOption[];
  fx_aud?: number;
  base_currency?: string;
  // Effective-cost calibration (#1/#2f): figures are list price; effective ≈
  // list × (1 − effective_discount). 0 ⇒ list only.
  effective_discount?: number;
  features?: Features;
  genie_space_id?: string;
  // Whether the business-unit dimension exists (demo always; live only when a
  // workspace_bu_table is configured). When false the UI hides BU everywhere.
  bu_enabled?: boolean;
  // Whether the user->team dimension exists (Adoption tab). False ⇒ hide team.
  team_enabled?: boolean;
  // Panels reading real data in live mode (e.g. ["genie-cost"]); the rest show
  // sample data. Empty in demo mode.
  live_wired?: string[];
}

export interface TrendPoint {
  week: string;
  spend_usd: number;
  savings_usd: number;
}

export interface TopOpportunity {
  // "workspace" is emitted by the live overview (a workspace-level opportunity);
  // the UI renders a rank, not the type, so this is just type-honesty.
  type: "query" | "table" | "access" | "workspace";
  insight: string;
  target: string;
  est_savings_usd_month: number;
  detail: string;
}

export interface Overview {
  total_spend_usd_month: number;
  total_savings_usd_month: number;
  savings_pct: number;
  // True in live mode: savings is a transparent estimate (not a billing fact).
  savings_estimated?: boolean;
  total_dbus_month: number;
  num_workspaces: number;
  num_critical: number;
  trend: TrendPoint[];
  top_opportunities: TopOpportunity[];
  cost_drivers: CostDrivers;
  // --- Exec-summary extras (landing view). All optional — a deployment / mode
  // that doesn't supply them degrades gracefully (the card just hides).
  spend_mtd_usd?: number;
  spend_prev_mtd_usd?: number;
  spend_mom_delta_usd?: number;
  spend_mom_pct?: number;
  mau?: number;
  mau_delta?: number;
  wau?: number;
  queries_30d?: number;
  queries_30d_delta_pct?: number;
  query_hours_30d?: number;
  avg_breadth?: number;
  num_products?: number;
  genie_adopters?: number;
  ai_adopters?: number;
  automated_pct?: number;
  serverless_share?: number;
  untagged_spend_pct?: number;
  addressable_savings_usd_month?: number;
  num_p1?: number;
  needs_attention?: NeedsAttentionItem[];
}

// A highlighted item on the landing view's "needs attention" list. `tab` is the
// PageId to deep-link into on click.
export interface NeedsAttentionItem {
  kind: "spend-spike" | "optimization" | string;
  title: string;
  detail?: string;
  est_savings_usd_month?: number;
  tab: PageId;
  // When this item is a specific recommendation (source:"rec"), entity_key is its
  // rec id — the Overview chip deep-links to that exact row in the Recommendations
  // tab (scroll + expand), not just the tab.
  source?: string;
  entity_key?: string;
}

// Optional AI narrative for the landing view (POST /api/overview/exec-summary).
export interface ExecSummary {
  status: "ok" | "unavailable" | "error";
  narrative: string;
  prompt_used?: boolean;
  error?: string;
}

export interface Workspace {
  workspace_id: string;
  // `workspace` is the display name (friendly name in both modes, falling back to
  // the id in live when the name table is ungranted); workspace_id is the raw id.
  workspace: string;
  workspace_name?: string;
  bu: string;
  spend_usd_month: number;
  savings_usd_month: number;
  savings_pct: number;
  automated_pct: number;
  health: Health;
  complexity: Complexity;
  idle_pct: number;
  serverless_share: number;
  dbus_month: number;
  num_clusters: number;
  num_warehouses: number;
  trend: number[];
  // True in live mode: savings_usd_month + the monthly-trend savings series are
  // modeled estimates (spend × headroom %), not captured $. Drives the honest
  // "Est. recoverable" labelling in the workspace drill-down.
  savings_estimated?: boolean;
  // Abnormal-cost-movement flags (customer feedback #4). Set by the backend from
  // the weekly trend: anomaly=true when the latest week breaks materially + notably
  // above the workspace's own recent average. Drives the exception surfacing.
  anomaly?: boolean;
  anomaly_pct?: number;         // signed latest-vs-baseline deviation (0.38 = +38%)
  anomaly_severity?: "warning" | "critical" | "";
  anomaly_note?: string;        // plain-English explanation for the tooltip
  // Naming-convention group (#2a) + Production/Non-production environment (#2b),
  // annotated by the backend from the workspace name via data/grouping.py.
  group?: string;
  environment?: string;
  env_tier?: string;
}

// Operational cost lenses. Each bucket is one
// slice; pct is the share of that lens's total. All lenses reconcile to the
// estate spend total.
export interface LensBucket {
  key: string;
  spend_usd_month: number;
  pct: number;
}
export interface OverviewLenses {
  by_workload: LensBucket[];
  by_compute_type: LensBucket[];
  by_environment: LensBucket[];
  by_env_tier?: LensBucket[];
  by_group: LensBucket[];
  groups_configured?: boolean;
  workload_basis?: string;
  window?: string;
}

export interface ProductMixSlice {
  product: string;
  pct: number;
  spend_usd_month: number;
}

export interface WorkspaceMonthPoint {
  month: string;
  spend_usd: number;
  savings_usd: number;
}

export interface SpendByEntity {
  spend_usd_month: number;
}

export interface TopUser extends SpendByEntity {
  user: string;
}

export interface TopJob extends SpendByEntity {
  job: string;       // human-readable job name (falls back to the id when unresolved)
  job_id?: string;   // the raw system.billing usage_metadata.job_id
}

export interface TopWarehouse extends SpendByEntity {
  warehouse: string;
}

// --- Cost by cost-driver + SKU breakdown (SPEC §9) ------------------------

export interface DriverSlice {
  driver: string; // billing_origin_product code
  label: string;
  spend_usd_month: number;
  pct_of_total: number;
}

export interface DriverTrendPoint {
  month: string;
  spend_usd: number;
}

export interface DriverSeries {
  driver: string;
  label: string;
  points: DriverTrendPoint[];
  spend_usd_month: number;
}

export interface DriverMoM {
  driver: string;
  label: string;
  prev_usd_month: number;
  spend_usd_month: number;
  /** Fraction change vs prior month. null for a NEW driver (prev $0 = infinite growth). */
  mom_pct: number | null;
  /** True when this driver had $0 last month and spend this month (new cost). */
  is_new?: boolean;
  delta_usd: number;
}

export interface SkuRow {
  sku: string;
  driver: string;
  driver_label: string;
  total_cost: number;
  dbus_month: number;
  pct_of_total: number;
}

export interface DbuBySku {
  sku: string;
  driver: string;
  driver_label: string;
  dbus_month: number;
}

export interface CostDrivers {
  workspace?: string;
  total_spend_usd_month: number;
  drivers: DriverSlice[];
  sku_breakdown: SkuRow[];
  dbu_by_sku: DbuBySku[];
  trend: { months: string[]; series: DriverSeries[] };
  mom: DriverMoM[];
}

export interface WorkspaceGenieSpend {
  total_list_cost_usd: number;
  paid_list_cost_usd: number;
  total_dbus: number;
  distinct_users: number;
  num_surfaces: number;
}

export interface WorkspaceDetail extends Workspace {
  product_mix: ProductMixSlice[];
  monthly_trend: WorkspaceMonthPoint[];
  top_users: TopUser[];
  top_jobs: TopJob[];
  top_warehouses: TopWarehouse[];
  cost_drivers: CostDrivers | null;
  genie?: WorkspaceGenieSpend | null;
}

export type QueryFlag =
  | "slow"
  | "high-spill"
  | "capacity-bound"
  | "full-scan";

export interface QueryRow extends AckFields {
  id: number;
  statement_id: string;
  query_text: string;
  target_table?: string;
  insight_type: InsightType;
  insight_rationale: string;
  rationale: string;
  impact: number;
  severity: number;
  src: string;
  src_id?: string;   // origin id (job/dashboard/notebook) when src isn't ad-hoc SQL
  warehouse: string;
  // Live mode resolves friendly names; these carry the raw ids for the
  // name-primary + id-secondary display (undefined/equal in demo → id hidden).
  warehouse_id?: string;
  workspace: string;
  workspace_id?: string;
  bu?: string;
  user: string;
  runs: number;
  p50_s: number;
  p95_s: number;
  bytes_read: number;
  pruning_efficiency: number;
  spill_gb: number;
  small_file_ratio: number;
  queued_ratio: number;
  stale_ratio: number;
  cost_usd: number;
  est_savings_usd_month: number;
  flags: QueryFlag[];
  confidence: number;
  applied: boolean;
  next_steps: string[];
  /** Exact, copy-paste-runnable statement for this recommendation (null when the
   *  action isn't a single SQL statement, e.g. warehouse resize). */
  apply_sql?: string | null;
  /** One-line plain-English basis for est_savings_usd_month ("show the math"). */
  savings_basis?: string | null;
}

/** AI-proposed, EXPLAIN-validated rewrite for a single query (Increment 3).
 *  status:
 *   validated      — EXPLAIN succeeded: parses + binds to the real schema
 *   unvalidated    — candidate produced but no warehouse to validate against
 *   rejected       — candidate failed EXPLAIN (bad schema binding) → not runnable
 *   unavailable    — AI rewrite is turned off (no in-region endpoint)
 *   not_applicable — the statement isn't a read-only SELECT we can rewrite */
export type RewriteStatus =
  | "validated"
  | "unvalidated"
  | "rejected"
  | "unavailable"
  | "not_applicable";

export interface QueryRewrite {
  status: RewriteStatus;
  candidate_sql: string | null;
  explanation: string;
  validation_detail: string;
  plan_before: string | null;
  plan_after: string | null;
  next_steps: string[];
  caveat: string;
}

// --- Warehouse monitor (Increment 4) ---------------------------------------
export type WarehouseReason = "capacity-bound" | "slow" | "spilling" | "idle";
export interface WarehouseRow extends AckFields {
  warehouse: string;
  warehouse_id: string;
  workspace: string;
  bu?: string;
  workspace_id?: string;
  type: string;
  size: string;
  auto_stop_mins: number;
  runs: number;
  p50_s: number;
  p95_s: number;
  queued_ratio: number;
  spill_gb: number;
  bytes_read: number;
  distinct_users: number;
  cost_usd_month: number;
  peak_concurrency: number;
  timeline: number[];
  top_users: { user: string; runs: number }[];
  top_sources: { source: string; pct: number; runs: number }[];
  reasons: WarehouseReason[];
  /** Actionable, metric-grounded advice for the drill-down: the single
   *  recommended lever, why, and the concrete next steps. */
  advice?: WarehouseAdvice;
}
export interface WarehouseAdvice {
  recommendation: string;
  rationale: string;
  next_steps: string[];
}
export interface WarehouseSummary {
  num_warehouses: number;
  num_capacity_bound: number;
  num_idle: number;
  num_spilling: number;
  total_cost_usd_month: number;
  total_runs: number;
  est_queue_ratio: number;
  biggest_queue_ratio: number;
}

// --- Cluster efficiency (Increment 5) --------------------------------------
export interface ClusterFinding {
  code: string;
  label: string;
  detail: string;
}
export interface ClusterRow extends AckFields {
  cluster: string;
  cluster_id: string;
  purpose: string;
  node_type: string;
  workspace: string;
  bu?: string;
  workspace_id?: string;
  workers_min: number;
  workers_max: number;
  autoscale: boolean;
  auto_stop_mins: number;
  creator: string;
  cpu_mean: number;
  mem_mean: number;
  idle_frac: number;
  uptime_hours_month: number;
  dbus_month: number;
  cost_usd_month: number;
  cpu_timeline: number[];
  dbr_version: string;
  findings: ClusterFinding[];
  potential_savings_usd_month: number;
}
export interface ClusterSummary {
  num_clusters: number;
  num_with_findings: number;
  num_idle: number;
  num_oversized: number;
  total_cost_usd_month: number;
  total_potential_savings_usd_month: number;
}

export interface ConversionPath {
  recommended: string;
  steps: string[];
}


export interface TableRow extends AckFields {
  fqn: string;
  catalog: string;
  schema: string;
  table: string;
  // Composite table-health indicator (freshness + file hygiene + staleness).
  health?: "Good" | "Warning" | "Critical";
  health_reason?: string;
  // Authoritative from information_schema; usually MANAGED/EXTERNAL, but VIEW and
  // other UC types can appear in live mode too.
  table_type: "MANAGED" | "EXTERNAL" | string;
  // Real Delta metadata (DESCRIBE DETAIL, via the nightly gold census).
  size_bytes: number;
  size_gb: number;
  num_files: number;
  avg_file_mb: number;
  small_file_ratio: number;
  partitioned: boolean;
  clustered: boolean;
  po_enabled: boolean;
  // Honest non-$ hygiene flags derived from the real file stats above.
  needs_optimize: boolean;
  needs_clustering: boolean;
  needs_action: boolean;
  // Usage recency + cold-storage (#15): large + rarely-read = retention/archive
  // candidate. reads_30d/days_since_read from lineage; size from gold.
  reads_30d?: number;
  days_since_read?: number;
  cold?: boolean;
  size_snapshot?: string;
  // Usage profile + lifecycle: frequency tier, 6-week read
  // trend, business-critical flag, and the data watermark / last-update.
  usage_tier?: "Hot" | "Warm" | "Cold" | string;
  usage_trend?: number[];
  business_critical?: boolean;
  last_updated?: string;
  recommendation: InsightType;
  rationale: string;
  next_steps: string[];
  benefits: string[];
  conversion_path?: ConversionPath | null;
  caveat?: string | null;
}

export interface TablesSummary {
  num_tables: number;
  num_managed: number;
  num_external: number;
  pct_managed: number;
  num_need_optimize: number;
  num_need_clustering: number;
}

// --- Jobs: job cost & health (observable facts only) ----------------------

export type JobComputeType = "All-Purpose" | "Jobs (classic)" | "Serverless" | "DLT";
// "why" chips surfaced per row — each an observed fact about the job.
export type JobReason = "high-setup-queue" | "failing" | "spiking" | "growing" | "cost-anomaly";

export interface JobWarmupBreakdown {
  setup_s: number;
  queue_s: number;
  exec_s: number;
  cleanup_s: number;
}
export interface JobDurationPoint {
  week: string;
  avg_s: number;
  runs: number;
  fails: number;
}
export interface JobCostPoint {
  month: string;
  cost_usd: number;
}
export interface JobRunSample {
  run_id: string;
  date: string;
  result_state: string;
  duration_s: number;
  warmup_s: number;
  wait_ratio: number;
  // Run-to-run delta vs the next-older run (populated in the drill-in).
  duration_delta_pct?: number;
  result_changed?: boolean;
  regressed?: boolean;
}
export interface JobTerminationCode {
  code: string;
  count: number;
}
export interface JobDetail {
  warmup_breakdown: JobWarmupBreakdown;
  duration_trend: JobDurationPoint[];
  cost_trend: JobCostPoint[];
  recent_runs: JobRunSample[];
  termination_codes: JobTerminationCode[];
  archetype?: string;
  // Live list drops the trend ARRAYS for payload size; this flags whether the
  // job has trend history so the drill-down knows to lazy-fetch it.
  has_trends?: boolean;
}

export interface JobRow extends AckFields {
  job_id: string;
  workspace_id: string;
  workspace: string;
  bu: string;
  job_name: string;
  compute_type: JobComputeType;
  is_serverless: boolean;
  streaming: boolean;
  dlt: boolean;
  runs: number;
  runs_7d: number;
  fail_rate: number;
  fail_rate_7d: number;
  fail_rate_rising: boolean;
  avg_duration_s: number;
  // setup + queue as a fraction of wall-clock — an observed fact, not a
  // recommendation. High values are flagged via `high_setup_queue`.
  setup_queue_ratio: number;
  high_setup_queue: boolean;
  median_duration_s: number;
  recent_duration_s: number;
  spike_ratio: number;
  growth_pct: number;
  cost_usd_month: number;
  cost_per_run: number;
  // Estimated $ of failed runs this month (fails × avg cost/run) — an estimate.
  failed_run_cost_usd_month: number;
  // Month-over-month cost change (exact, from the cost trend).
  mom_cost_delta_usd: number;
  mom_cost_pct: number;
  cost_anomaly?: boolean;
  cost_anomaly_ratio?: number;
  reasons: JobReason[];
  trend: number[];
  detail: JobDetail;
  // Operational workload type, classified from the job name
  // + compute: Batch / Streaming / Interactive / Ad-hoc / ML / AI / Other.
  workload?: string;
  env_tier?: string;
  environment?: string;
  group?: string;
  schedule?: string;
  paused?: boolean | null;
  schedule_active?: boolean;
  cron?: string;
  owner?: string;
  creator?: string;
  deleted?: boolean;
  last_run?: string;
  started_by?: string;
}

export interface JobsByCompute {
  compute_type: JobComputeType;
  num_jobs: number;
  cost_usd_month: number;
}
export interface JobsSummary {
  num_jobs: number;
  num_failing: number;
  num_growing: number;
  num_streaming: number;
  num_dlt: number;
  num_spiking: number;
  num_cost_anomalies: number;
  failed_run_cost_usd_month: number;
  total_cost_usd_month: number;
  by_compute: JobsByCompute[];
  by_env_tier?: { env_tier: string; environment: string; num_jobs: number; num_scheduled: number; cost_usd_month: number; runs: number }[];
  nonprod?: { num_jobs: number; num_scheduled: number; cost_usd_month: number; scheduled_cost_usd_month: number };
}

// --- Pipelines: Lakeflow + DLT + MV/ST (billed with pipeline id) ----------

export type PipelineReason = "failing" | "cost-spike" | "full-refresh-heavy" | "continuous" | "development-mode" | "deleted-still-billing";

export interface PipelineRow extends AckFields {
  pipeline_id: string;
  workspace_id: string;
  workspace: string;
  bu: string;
  name: string;
  pipeline_type: string;
  type_label: string;
  owner: string;
  cost_usd_month: number;
  prev_cost_usd_month: number;
  mom_pct: number | null;
  dbus_month: number;
  serverless_share: number;
  serverless: boolean;
  products: string[];
  job_attributed: boolean;
  continuous: boolean;
  development: boolean;
  deleted: boolean;
  updates_30d: number;
  failed_30d: number;
  fail_rate: number;
  full_refreshes_30d: number;
  last_update: string; // "YYYY-MM-DD HH:MM" or ""
  last_result: string;
  triggered_by: string;
  avg_update_s: number;
  reasons: PipelineReason[];
  env_tier: string;
  environment: string;
  group: string;
}

export interface PipelineTypeBreakdown {
  type: string;
  num_pipelines: number;
  cost_usd_month: number;
}

export interface PipelinesSummary {
  num_pipelines: number;
  total_cost_usd_month: number;
  prev_total_cost_usd_month: number;
  mom_pct: number | null;
  serverless_pct: number;
  num_failing: number;
  num_continuous: number;
  num_spiking: number;
  num_mv_st: number;
  mv_st_cost_usd_month: number;
  by_type: PipelineTypeBreakdown[];
}

export type PrincipalType = "user" | "service_principal" | "group";

export interface Principal {
  principal: string;
  type: PrincipalType;
  source: string;
  status: string;
  last_active: string | null;      // null in live: not derivable from the grants query (needs audit)
  num_entitlements: number | null;  // null in live: comes from group membership (admin-sync), not grants
  num_grants: number;
  member_count: number | null;
  num_direct_members?: number | null;
}

// --- Access: bidirectional grant graph ------------------------------------

// Cascading workspace → catalog → schema → table structure.
export interface AccessTree {
  workspaces: string[];
  // catalog -> schema -> [table fqns]
  catalogs: Record<string, Record<string, string[]>>;
}

export interface GroupMember {
  principal: string;
  type: PrincipalType;
}

export interface GroupNode {
  group: string;
  source: string;
  member_count: number;
  direct_members: GroupMember[];
  entitlements: string[];
  workspaces: { workspace: string; permission: string }[];
}

// Principal-centric ("what can this principal access?")
export interface EffectiveEntitlement {
  entitlement: string;
  via: "direct" | "group";
  source_group: string | null;
}

export interface EffectiveWorkspace {
  workspace: string;
  permission: string;
  source_group: string | null;
}

export interface EffectiveGrant {
  securable: string;
  securable_type: string;
  catalog: string;
  privilege: string;
  granted_via: "direct" | "group";
  source_group: string | null;
  inherited: boolean;
}

export interface Pat {
  id: number;
  principal: string;
  comment: string;
  age_days: number;
  last_used_days: number | null;  // null in live: token ingest doesn't capture last-use (UI hides the line)
  stale: boolean;
}

export interface PrincipalAccess {
  principal: string;
  type: PrincipalType;
  source: string;
  status: string;
  member_of: string[];
  entitlements: EffectiveEntitlement[];
  workspaces: EffectiveWorkspace[];
  grants: EffectiveGrant[];
  num_grants: number;
  pats: Pat[];
}

// Data-centric ("who can access this securable?")
export interface SecurableGrant {
  id: number;
  principal: string;
  principal_type: PrincipalType;
  privilege: string;
  granted_via: "direct" | "group";
  inherited: boolean;
  inherited_from: string | null;
}

export interface AccessPolicy {
  kind: "row_filter" | "column_mask" | "abac";
  name: string;
  detail: string;
}

export interface SecurableAccess {
  securable: string;
  securable_type: string;
  catalog: string;
  grants: SecurableGrant[];
  policies: AccessPolicy[];
  num_grants: number;
}

export interface Grant {
  id: number;
  principal: string;
  principal_type: string;
  securable_type: string;
  securable: string;
  catalog: string;
  privilege: string;
  inherited: boolean;
  granted_via: string;
}

export interface Risk {
  id: number;
  principal: string;
  risk: string;
  severity: "critical" | "warning";
  detail: string;
  recommended_action: string;
}

export type GovernancePageId = Exclude<PageId, "governance">;

export interface GovernanceTile {
  metric: string;
  category?: string;
  weight?: number;
  value_pct?: number;
  value_usd?: number;
  value_count?: number;
  status: Status;
  gap: string;
  action: string;
  ties_to?: GovernancePageId;
  score_points?: number;
}

export interface TaggingWorkspaceRow {
  workspace: string;
  bu: string;
  spend_usd_month: number;
  tagging_pct: number;
  untagged_usd_month: number;
  status: Status;
}

export interface GovernanceReport {
  score: number;
  grade: string;
  counts: Record<Status, number>;
  num_tiles: number;
  tiles: GovernanceTile[];
  tagging_by_workspace: TaggingWorkspaceRow[];
}

// --- Recommendations hub (SPEC §3.7) --------------------------------------

export type Priority = "P1" | "P2" | "P3";
export type RecCategory =
  | "behavioural"
  | "compute"
  | "storage"
  | "tagging"
  | "genai"
  | "access"
  | "governance";
export type RecScope = "global" | "workspace" | "bu" | "team" | "user" | "account";
export type RecSource = "strategic" | "query" | "table" | "governance" | "access";

export interface HubRec extends AckFields {
  id: string;
  catalog_id: string | null;
  category: RecCategory;
  priority: Priority;
  priority_score: number;
  title: string;
  scope: RecScope;
  scope_label: string;
  est_savings_usd_month: number;
  // False ⇒ a genuine cost play whose $ can't be estimated without an A/B run
  // (e.g. classic→serverless). UI shows "A/B required", not a number or "risk".
  savings_estimatable?: boolean;
  savings_basis?: string | null;
  effort: "Low" | "Med" | "High";
  evidence: string[];
  what_to_do: string[];
  owner: string;
  source: RecSource;
  expected_impact?: string;
  notes?: string | null;
}

export interface HubSummary {
  total_addressable_savings_usd_month: number;
  // The total sums overlapping levers (same dollars at estate + per-item
  // granularity), so it's a non-additive upper bound — label it, don't present
  // it as a bankable sum.
  savings_is_upper_bound?: boolean;
  num_p1: number;
  num_recs: number;
  untagged_spend_usd_month: number;
  untagged_pct: number;
  top_category: RecCategory | null;
  category_counts: Record<string, number>;
  priority_counts: Record<Priority, number>;
  category_savings: Record<string, number>;
}

export interface AttributionWorkspace {
  workspace: string;
  bu: string;
  spend_usd_month: number;
  tagging_pct: number | null;
  untagged_usd_month: number;
}

export interface AttributionBu {
  bu: string;
  spend_usd_month: number;
  untagged_usd_month: number;
  tagged_pct: number;
}

export interface CostDriver {
  driver: string;
  driver_code?: string;
  spend_usd_month: number;
  pct_of_total: number;
}

export interface Spike {
  workspace: string;
  bu: string;
  mom_pct: number;
  spend_usd_month: number;
}

export interface DriverSpike {
  driver: string;
  label: string;
  /** null for a NEW driver (prev $0 = infinite growth). */
  mom_pct: number | null;
  /** True when this driver had $0 last month and spend this month. */
  is_new?: boolean;
  prev_usd_month: number;
  spend_usd_month: number;
  delta_usd: number;
}

export interface Attribution {
  total_spend_usd_month: number;
  total_untagged_usd_month: number;
  untagged_pct: number;
  by_workspace: AttributionWorkspace[];
  by_bu: AttributionBu[];
  cost_drivers: CostDriver[];
  spikes: Spike[];
  driver_spikes: DriverSpike[];
}

// --- Genie cost attribution — Code vs Spaces (SPEC §13; always on) ----------

// Genie surface = usage_metadata.genie.surface. Real values: GENIE_CODE,
// GENIE_ONE, GENIE_AGENTS (+ UNKNOWN for the ~0.5% null rows). Not an enum —
// the column is undocumented and may add values, so treat surface as a string
// and render the server-supplied label.
export interface GenieCostRow {
  workspace: string;
  user_identity: string;
  surface: string;
  label: string;
  total_dbus: number;
  total_list_cost_usd: number;
  paid_dbus: number;
  paid_list_cost_usd: number;
}

export interface GenieSurfaceTotal {
  surface: string;
  label: string;
  dbus: number;
  list_usd: number;
  paid_usd: number;
  pct: number;
}

export interface GenieWorkspaceTotal {
  workspace: string;
  distinct_users: number;
  total_dbus: number;
  paid_dbus: number;
  surface_dbus: Record<string, number>;
  total_list_cost_usd: number;
  paid_list_cost_usd: number;
}

export interface GenieUserTotal {
  user_identity: string;
  total_dbus: number;
  total_list_cost_usd: number;
  paid_list_cost_usd: number;
  num_workspaces: number;
  top_surface: string | null;
}

export interface GenieCost {
  month: string;
  workspace: string;
  dbu_list_price_usd: number;
  free_dbus_per_user_month: number;
  breakdown: GenieCostRow[];
  surface_totals: GenieSurfaceTotal[];
  by_workspace: GenieWorkspaceTotal[];
  by_user: GenieUserTotal[];
  // Total platform spend this month, for the "% of total spend" context.
  total_platform_spend_usd_month?: number;
  summary: {
    total_dbus: number;
    total_list_cost_usd: number;
    paid_list_cost_usd: number;
    // Live (estate-wide): the REAL free-absorbed $ from the GENIE_FREE_USAGE SKU
    // (present from 20 Jul 2026), valued at the effective billed Genie rate.
    // null when scoped to a workspace, pre-20-Jul, or no free usage. In demo mode
    // it holds the illustrative free-vs-paid split.
    free_absorbed_usd: number | null;
    // Live (estate-wide): free DBUs from the GENIE_FREE_USAGE SKU; null otherwise.
    free_dbus_total?: number | null;
    distinct_users: number;
    num_workspaces: number;
    num_surfaces: number;
    // TRUE in live mode: the per-row breakdown is the billable usage above each
    // user's 150 free DBUs/month. FALSE in demo mode.
    billable_only?: boolean;
    // TRUE when free usage was found in system.billing.usage (GENIE_FREE_USAGE
    // SKU, from 20 Jul 2026). FALSE in demo, or live pre-20-Jul / no free usage.
    free_usage_in_system_tables?: boolean;
    // TRUE when free_absorbed_usd was valued at the list-price proxy (no billed
    // Genie rows this month to derive an effective rate) rather than a real rate.
    free_rate_is_proxy?: boolean;
  };
  caveats: string[];
}

// --- AI cost attribution (all AI billing products; always on) --------------

export interface AiProduct {
  code: string;
  label: string;
  list_usd: number;
  dbus: number;
  endpoints: number;
  pct: number;
}
export interface AiEndpoint {
  name: string;
  product: string;
  product_label: string;
  workspace: string;
  owner: string;
  list_usd_month: number;
  dbus_month: number;
  mode: string | null;        // null in live when the offering struct isn't present (UI shows "—")
  gpu: boolean | null;        // null in live when the gpu struct isn't present
  scale_to_zero: boolean | null;
}
export interface AiUser {
  user: string;
  list_usd: number;
  endpoints: number;
  top_product: string | null;
}
export interface AiWorkspace {
  workspace: string;
  list_usd: number;
  endpoints: number;
}
export interface AiFlag {
  endpoint: string;
  workspace: string;
  issue: string;
  saving_usd_month: number;
}
export interface AiCaller {
  requester: string;
  requests: number;
  tokens: number;
  est_usd_month: number;
  endpoints: string[];
  num_endpoints: number;
  workspaces: string[];
  is_service_principal: boolean;
}
export interface AiCost {
  month: string;
  workspace: string;
  by_product: AiProduct[];
  by_workspace: AiWorkspace[];
  by_user: AiUser[];
  endpoints: AiEndpoint[];
  flags: AiFlag[];
  callers?: AiCaller[];
  callers_note?: string;
  total_platform_spend_usd_month?: number;
  trend: { months: string[]; points: number[] };
  summary: {
    total_list_usd_month: number;
    total_dbus_month: number;
    num_endpoints: number;
    num_products: number;
    distinct_users: number;
    num_workspaces: number;
    top_product: string | null;
    gpu_usd_month: number;
    flagged_savings_usd_month: number;
  };
  caveats: string[];
}

// --- Governed tags + Data Classification (always on) -----------------------

export interface GovernedTag {
  tag_key: string;
  allowed_values: string[];
  coverage_pct: number;
  certified?: number;
  deprecated?: number;
  draft?: number;
  note?: string;
}
export interface SensitiveTable {
  fqn: string;
  pii_columns: number;
  categories: string[];
  sensitivity: string;
}
export interface Classification {
  governed_tags: GovernedTag[];
  classification: {
    enabled: boolean;
    catalogs_enabled: number;
    tables_scanned: number;
    tables_total: number;
    pii_columns: number;
    sensitive_columns: number;
    coverage_pct: number;
    scan_cost_usd_month: number;
    categories: { type: string; count: number }[];
    top_sensitive_tables: SensitiveTable[];
    unclassified_high_risk: { fqn: string; reason: string }[];
  };
  summary: {
    num_governed_tags: number;
    avg_tag_coverage: number;
    pii_columns: number;
    classification_coverage_pct: number;
    scan_cost_usd_month: number;
  };
}

// --- Adoption & Value (always on) ------------------------------------------

export type AdoptionQuadrant = "Gold asset" | "Standard" | "Archive candidate";
export interface AdoptionAsset {
  table: string;
  workspace: string;
  total_queries: number;
  unique_users: number;
  days_since_update: number;
  quadrant: AdoptionQuadrant;
}
export interface AdoptionUser {
  user: string;
  workspace: string;
  queries: number;
  // Cumulative query execution time (statement duration summed) in the period.
  query_hours?: number;
  last_active_days: number;
  // From the customer's user_team_table (empty when team dimension is disabled).
  team?: string;
}
export interface AdoptionTeam {
  team: string;
  users: number;
  queries: number;
  query_hours?: number;
}
export interface AdoptionFeature {
  workspace: string;
  products: string[];
  breadth: number;
  genie: boolean;
  ai: boolean;
}
export interface Adoption {
  month: string;
  assets: AdoptionAsset[];
  users: AdoptionUser[];
  team_rollup?: AdoptionTeam[];
  features: AdoptionFeature[];
  mau_trend: { month: string; active_users: number }[];
  summary: {
    mau: number;
    wau: number;
    total_queries_month: number;
    query_hours_month?: number;
    avg_breadth: number;
    num_products: number;
    genie_adopters: number;
    ai_adopters: number;
    num_workspaces: number;
    gold_assets: number;
    archive_candidates: number;
  };
}

// --- Data Quality Monitoring (SPEC §12; gated by features.dqm) --------------

export type DqmMonitorType = "Snapshot" | "TimeSeries" | "Inference";
export type DqmQuality = "Good" | "Warning" | "Critical";
export type DqmFreshness = "Fresh" | "OK" | "Stale";

export interface DqmMonitor {
  fqn: string;
  catalog: string;
  schema: string;
  table: string;
  monitor_type: DqmMonitorType;
  workspace: string;
  profile_metrics_table: string;
  drift_metrics_table: string;
  dashboard_url: string;
  schedule: string;
  schedule_label: string;
  last_refresh_hours: number;
  freshness: DqmFreshness;
  num_metrics: number;
  null_pct: number;
  drift_psi: number;
  drift_ks: number;
  anomalies: number;
  alerts: number;
  alert_status: "OK" | "TRIGGERED";
  quality_status: DqmQuality;
  dbus_month: number;
  cost_usd_month: number;
  value: "high" | "medium" | "low";
  prune_candidate: boolean;
}

export interface DqmSummary {
  num_monitors: number;
  num_visible: number;
  num_critical: number;
  num_warning: number;
  num_stale: number;
  num_alerts_triggered: number;
  dqm_cost_usd_month: number;
  dqm_dbus_month: number;
  num_prune_candidates: number;
  prune_savings_usd_month: number;
}

export interface DqmWorkspaceCost {
  workspace: string;
  num_monitors: number;
  cost_usd_month: number;
  dbus_month: number;
}

export interface DqmPruneCandidate {
  fqn: string;
  workspace: string;
  cost_usd_month: number;
  freshness: DqmFreshness;
  value: string;
  anomalies: number;
}

// Genie Ask SSE meta (mirrors GridSense's GenieMeta shape).
export interface GenieAskChart {
  type: "bar" | "line";
  labels: string[];
  values: number[];
  unit: string;
}

export interface GenieAskMeta {
  matched_question: string;
  asked_question: string;
  chart: GenieAskChart;
  follow_ups: string[];
  // "genie" = real Genie Space answer (live); "synthetic" = canned demo answer;
  // "unavailable" = Genie not configured in live; "error" = Genie call failed.
  source?: "genie" | "synthetic" | "unavailable" | "error";
  caveat?: string;
}
