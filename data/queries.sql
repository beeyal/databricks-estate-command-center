-- ============================================================================
-- Estate Command Center — REAL system-table SQL (live mode)
-- ============================================================================
-- These are the production queries the app runs when USE_LIVE=true against a
-- SQL warehouse via databricks-sql-connector. In demo mode (default,
-- USE_LIVE=false) the app serves the seeded dataset from data/generate_demo.py
-- and NEVER runs these — the UI clearly labels demo numbers as "demo".
--
-- Parameters are passed as bound params by the connector and shown here as
-- :param placeholders. Time windows use :time_range_days.
--
-- Required grants (minimum): SELECT on system.billing.*, system.query.history,
-- system.access.audit, system.information_schema.*, system.lakeflow.*,
-- system.storage.* (where available); plus the account SCIM/groups API and the
-- workspace-assignments API for the Access page (documented — not SQL).
-- All queries are region-scoped where a region column exists.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- OVERVIEW / WORKSPACES  §3.0 §3.2
-- Spend + DBUs per workspace × SKU × product, joined to list prices.
-- ----------------------------------------------------------------------------
-- :time_range_days  e.g. 30
WITH usage AS (
  SELECT
    u.workspace_id,
    u.sku_name,
    u.billing_origin_product,
    SUM(u.usage_quantity)                                    AS dbus,
    SUM(u.usage_quantity * lp.pricing.default)               AS list_cost_usd
  FROM system.billing.usage u
  JOIN system.billing.list_prices lp
    ON u.sku_name = lp.sku_name
   AND u.usage_end_time >= lp.price_start_time
   AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
  WHERE u.usage_date >= dateadd(DAY, -:time_range_days, current_date())
    AND (:workspace_id = 'all' OR u.workspace_id = :workspace_id)
  GROUP BY ALL
)
SELECT
  workspace_id,
  SUM(list_cost_usd)                                         AS spend_usd,
  SUM(dbus)                                                  AS dbus,
  -- product/SKU mix for the drill-down
  collect_list(struct(billing_origin_product, list_cost_usd)) AS product_mix
FROM usage
GROUP BY workspace_id
ORDER BY spend_usd DESC;


-- Automated % = scheduled-jobs compute vs interactive/all-purpose compute.
-- Drives the Health rule: Good >=60%, Warning 25-60%, Critical <25%.
SELECT
  u.workspace_id,
  SUM(CASE WHEN u.billing_origin_product IN ('JOBS','DLT','LAKEFLOW')
           THEN u.usage_quantity ELSE 0 END)                 AS automated_dbus,
  SUM(u.usage_quantity)                                      AS total_dbus,
  SUM(CASE WHEN u.billing_origin_product IN ('JOBS','DLT','LAKEFLOW')
           THEN u.usage_quantity ELSE 0 END)
    / NULLIF(SUM(u.usage_quantity), 0)                       AS automated_pct
FROM system.billing.usage u
WHERE u.usage_date >= dateadd(DAY, -:time_range_days, current_date())
GROUP BY u.workspace_id;


-- Serverless share + idle (all-purpose no query activity) inputs to savings.
SELECT
  u.workspace_id,
  SUM(CASE WHEN u.sku_name ILIKE '%SERVERLESS%' THEN u.usage_quantity ELSE 0 END)
    / NULLIF(SUM(u.usage_quantity),0)                        AS serverless_share
FROM system.billing.usage u
WHERE u.usage_date >= dateadd(DAY, -:time_range_days, current_date())
GROUP BY u.workspace_id;


-- ----------------------------------------------------------------------------
-- WORKSPACE DRILL-DOWN  §3.2
-- Top users / warehouses by $ (approx via query history joined to billing).
-- ----------------------------------------------------------------------------
SELECT
  q.executed_by                                              AS user,
  q.warehouse_id,
  COUNT(*)                                                   AS runs,
  SUM(q.total_task_duration_ms) / 3.6e6                      AS compute_hours
FROM system.query.history q
WHERE q.start_time >= dateadd(DAY, -:time_range_days, current_timestamp())
  AND (:workspace_id = 'all' OR q.workspace_id = :workspace_id)
GROUP BY q.executed_by, q.warehouse_id
ORDER BY compute_hours DESC
LIMIT 20;


-- Top jobs by $ for the drill-down (job compute joined to billing tags).
SELECT
  j.name                                                     AS job,
  SUM(u.usage_quantity * lp.pricing.default)                 AS spend_usd
FROM system.lakeflow.jobs j
JOIN system.billing.usage u
  ON u.usage_metadata.job_id = j.job_id
JOIN system.billing.list_prices lp
  ON u.sku_name = lp.sku_name
 AND u.usage_end_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= dateadd(DAY, -:time_range_days, current_date())
  AND (:workspace_id = 'all' OR u.workspace_id = :workspace_id)
GROUP BY j.name
ORDER BY spend_usd DESC
LIMIT 10;


-- Monthly spend trend for the drill-down chart (12 months, per workspace).
SELECT
  date_format(u.usage_date, 'yyyy-MM')                       AS month,
  SUM(u.usage_quantity * lp.pricing.default)                 AS spend_usd
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON u.sku_name = lp.sku_name
 AND u.usage_end_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= add_months(current_date(), -12)
  AND (:workspace_id = 'all' OR u.workspace_id = :workspace_id)
GROUP BY date_format(u.usage_date, 'yyyy-MM')
ORDER BY month;


-- ----------------------------------------------------------------------------
-- QUERY ADVISOR  §3.3
-- Statement-level metrics: bytes read/pruned, spill, p50/p95, run count.
-- ----------------------------------------------------------------------------
SELECT
  q.statement_id,
  any_value(q.statement_text)                                AS query_text,
  q.warehouse_id,
  q.executed_by                                              AS user,
  q.workspace_id,
  COUNT(*)                                                   AS runs,
  percentile(q.total_duration_ms, 0.50) / 1000.0             AS p50_s,
  percentile(q.total_duration_ms, 0.95) / 1000.0             AS p95_s,
  SUM(q.read_bytes)                                          AS bytes_read,
  SUM(q.read_bytes - COALESCE(q.pruned_bytes, 0))            AS bytes_scanned,
  1 - (SUM(COALESCE(q.pruned_bytes,0)) / NULLIF(SUM(q.read_bytes),0)) AS pruning_efficiency,
  SUM(COALESCE(q.spilled_local_bytes,0)) / 1e9               AS spill_gb
FROM system.query.history q
WHERE q.start_time >= dateadd(DAY, -:time_range_days, current_timestamp())
  AND q.statement_type = 'SELECT'
  AND (:warehouse_id = 'all' OR q.warehouse_id = :warehouse_id)
  AND (:workspace_id = 'all' OR q.workspace_id = :workspace_id)
GROUP BY q.statement_id, q.warehouse_id, q.executed_by, q.workspace_id
HAVING p95_s >= :p95_threshold
ORDER BY p95_s DESC
LIMIT 200;


-- ----------------------------------------------------------------------------
-- STORAGE & TABLES  §3.4
-- Managed vs external classification from information_schema; physical detail
-- (numFiles, sizeInBytes, clustering/partition cols) comes from DESCRIBE DETAIL
-- per table (run per-fqn; cannot be a single system query today).
-- ----------------------------------------------------------------------------
SELECT
  t.table_catalog,
  t.table_schema,
  t.table_name,
  t.table_type,                                              -- MANAGED / EXTERNAL / VIEW
  t.data_source_format,
  t.storage_path
FROM system.information_schema.tables t
WHERE t.table_catalog NOT IN ('system','information_schema')
  AND (:catalog = 'all' OR t.table_catalog = :catalog)
ORDER BY t.table_catalog, t.table_schema, t.table_name;

-- Per-table physical detail (issued per fully-qualified name):
-- DESCRIBE DETAIL `:catalog`.`:schema`.`:table`;
--   -> numFiles, sizeInBytes, clusteringColumns, partitionColumns, format

-- Predictive Optimization adoption (where the PO system tables exist):
-- SELECT table_full_name, optimization_type, last_run_time
-- FROM system.storage.predictive_optimization_operations_history
-- WHERE run_date >= dateadd(DAY, -:time_range_days, current_date());


-- ----------------------------------------------------------------------------
-- ACCESS — grants  §3.1
-- Effective UC privileges per principal × securable. Combine catalog / schema /
-- table / routine privilege views; resolve inherited vs direct in the app layer.
-- ----------------------------------------------------------------------------
SELECT grantee AS principal, table_catalog AS catalog, table_schema, table_name,
       privilege_type AS privilege, inherited_from, 'table' AS securable_type
FROM system.information_schema.table_privileges
WHERE (:catalog = 'all' OR table_catalog = :catalog)
UNION ALL
SELECT grantee, catalog_name, schema_name, NULL, privilege_type, inherited_from, 'schema'
FROM system.information_schema.schema_privileges
UNION ALL
SELECT grantee, catalog_name, NULL, NULL, privilege_type, inherited_from, 'catalog'
FROM system.information_schema.catalog_privileges
UNION ALL
SELECT grantee, specific_catalog, specific_schema, specific_name, privilege_type, inherited_from, 'routine'
FROM system.information_schema.routine_privileges;

-- Effective-privilege resolution (documented):
--   direct   = inherited_from IS NULL
--   inherited= inherited_from references a parent securable
--   A principal's effective set on a securable = UNION of direct grants and all
--   grants inherited from ancestor securables and from every group it belongs to.

-- Last-access for stale-grant detection:
SELECT request_params.full_name_arg AS securable, user_identity.email AS principal,
       MAX(event_time) AS last_access
FROM system.access.audit
WHERE action_name IN ('getTable','commandSubmit','generateTemporaryTableCredential')
  AND event_date >= dateadd(DAY, -90, current_date())
GROUP BY 1, 2;

-- Principal directory, group membership, workspace assignments, entitlements,
-- and PAT/token inventory come from the account SCIM / Groups API and the
-- workspace-assignments API (REST) — not SQL. See docs/ARCHITECTURE.md.


-- ----------------------------------------------------------------------------
-- GENIE COST ATTRIBUTION — by surface (Code / Spaces / Agents)  §13  (ALWAYS on)
-- ----------------------------------------------------------------------------
-- Verified against live system.billing.usage (an internal test workspace, 2026):
--   * Genie is billed under billing_origin_product = 'GENIE'. NEVER filter by
--     sku_name — Genie shares the region-suffixed real-time-inference SKU
--     (ENTERPRISE_SERVERLESS_REAL_TIME_INFERENCE_<region>).
--   * usage_metadata.genie = struct<surface, channel, agent_id>, populated on
--     ~99.5% of GENIE rows. The SURFACE is the real Code-vs-Spaces signal:
--       GENIE_CODE   ⇒ Genie Code (SQL editor / notebook Genie)
--       GENIE_ONE    ⇒ Genie Spaces family (Genie One / Databricks One chat)
--       GENIE_AGENTS ⇒ Genie in Agent Bricks / agent surface
--       NULL         ⇒ unattributed (~0.5%) — bucket as 'UNKNOWN'
--     There is NO 'GENIE_SPACES' literal — group by the raw surface, don't
--     hardcode it. This column is real but NOT in the published billing schema
--     doc, so treat as "may change" and degrade to per-user/workspace totals.
--   * product_features.ai_bi_genie.* is NULL on 100% of rows — DO NOT use it.
--   * Attribution: identity_metadata.run_as = end user (100% populated),
--     workspace_id = origin workspace (100% populated).
--   * Pricing: consumption/PAYGO from 6 Jul 2026 with 150 free DBUs of LLM
--     usage per identified user per month (SHARED across all surfaces), then
--     billed DBUs. $ = usage_quantity × pricing.effective_list.default (LIST
--     price, not contractual drawdown — Account Console is source of truth).
--   * Budgets can target Genie (Unity AI Gateway + tag databricks-product:genie)
--     but CANNOT be split by surface and cannot remove the free tier.

-- Q1. Ground-truth Genie usage by usage_date × workspace × user × surface.
--     LEFT JOIN to list_prices so a missing price row never drops usage.
SELECT
  u.usage_date,
  u.workspace_id,
  u.identity_metadata.run_as                                 AS user_identity,
  COALESCE(u.usage_metadata.genie.surface, 'UNKNOWN')        AS genie_surface,
  SUM(u.usage_quantity)                                      AS total_dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)  AS total_list_cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud
 AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.billing_origin_product = 'GENIE'
GROUP BY 1, 2, 3, 4;


-- Q2. Approximate PAID split by month × workspace × surface, pro-rating each
--     user's paid DBUs across surfaces AFTER the shared 150-free-DBU/user/month
--     allowance:  GREATEST(total_user_dbus - 150, 0) × usage_share_of_month.
--     (Per-surface "paid" is an estimate — the free tier is not allocated to a
--     surface by billing; only the per-user total paid figure is exact.)
WITH genie_usage AS (
  SELECT
    date_trunc('MONTH', u.usage_date)                        AS usage_month,
    u.workspace_id,
    u.identity_metadata.run_as                               AS user_identity,
    COALESCE(u.usage_metadata.genie.surface, 'UNKNOWN')      AS genie_surface,
    SUM(u.usage_quantity)                                    AS surface_dbus
  FROM system.billing.usage u
  WHERE u.billing_origin_product = 'GENIE'
  GROUP BY 1, 2, 3, 4
),
user_month AS (   -- free allowance is per identified user per month
  SELECT usage_month, user_identity, SUM(surface_dbus) AS total_user_dbus
  FROM genie_usage GROUP BY 1, 2
)
SELECT
  g.usage_month,
  g.workspace_id,
  g.genie_surface,
  SUM(
    GREATEST(um.total_user_dbus - 150, 0)                    -- paid after 150 free
    * (g.surface_dbus / NULLIF(um.total_user_dbus, 0))       -- usage_share_of_month
  )                                                          AS paid_dbus
FROM genie_usage g
JOIN user_month um
  ON g.usage_month = um.usage_month AND g.user_identity = um.user_identity
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;


-- Q3. Total Genie spend per workspace, THIS MONTH (distinct users, DBUs, list $).
SELECT
  u.workspace_id,
  COUNT(DISTINCT u.identity_metadata.run_as)                 AS distinct_users,
  SUM(u.usage_quantity)                                      AS total_dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)  AS total_list_cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud
 AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.billing_origin_product = 'GENIE'
  AND u.usage_date >= date_trunc('MONTH', current_date())
GROUP BY u.workspace_id
ORDER BY total_list_cost DESC;


-- ----------------------------------------------------------------------------
-- DATA QUALITY MONITORING — Lakehouse Monitoring  §12  (gated by features.dqm)
-- ----------------------------------------------------------------------------
-- Existing monitors + metric/drift tables come from the monitor metadata API
-- (information_schema does not fully expose Lakehouse Monitoring today):
--   GET /api/2.1/unity-catalog/tables/{full_name}/monitor  (per table)
-- The FinOps tie-in — the DATA_QUALITY_MONITORING cost + which workspaces/tables
-- drive it — comes from billing:
SELECT
  u.workspace_id,
  SUM(u.usage_quantity)                                      AS dqm_dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)  AS dqm_list_cost
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud
 AND u.sku_name = lp.sku_name
 AND u.usage_end_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.billing_origin_product = 'DATA_QUALITY_MONITORING'
  AND u.usage_date >= date_trunc('MONTH', current_date())
GROUP BY u.workspace_id
ORDER BY dqm_list_cost DESC;
