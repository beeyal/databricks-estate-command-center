-- ===========================================================================
-- FinOps Command Center — optional mapping tables (copy-paste setup)
-- ===========================================================================
-- Databricks system tables don't know your org structure (which workspace is
-- which business unit, which user is on which team). These two small tables let
-- the app show that. They're OPTIONAL — leave the config blank and the app just
-- hides the BU / team features. You own and maintain these tables.
--
-- HOW TO USE:
--   1. Edit the catalog.schema below (default: main.finops_ref) if you want a
--      different location, and create the schema.
--   2. Run this whole file once in a SQL editor / notebook.
--   3. Replace the sample rows with your real mappings.
--   4. Point the app at them, then redeploy. For a bundle deploy, set these env
--      values in app.yaml (the file the running app reads):
--          FINOPS_WORKSPACE_BU_TABLE = main.finops_ref.workspace_bu
--          FINOPS_USER_TEAM_TABLE    = main.finops_ref.user_team
--      (Or, on the ./deploy.sh path, set workspace_bu_table / user_team_table in
--      customise.yaml and deploy.sh writes them into app.yaml for you.) The BU /
--      team features light up immediately.
--
-- GRANT: the app's service principal needs USE CATALOG/SCHEMA + SELECT on these
-- (a single GRANT at the bottom does it — edit the principal).
-- ===========================================================================

CREATE SCHEMA IF NOT EXISTS main.finops_ref;

-- ---------------------------------------------------------------------------
-- 1) workspace -> business unit (+ optional owner)
--    Powers: Workspaces BU facet, per-BU cost attribution.
--    workspace_id matches system.access.workspaces_latest.workspace_id.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS main.finops_ref.workspace_bu (
  workspace_id STRING,   -- e.g. '6821148851943430'
  bu           STRING,   -- e.g. 'Energy Markets & Trading'
  owner        STRING    -- optional; name or email (leave NULL if unused)
) USING DELTA;

-- Replace these with your real workspaces. (Find IDs in:
--   SELECT workspace_id, workspace_name FROM system.access.workspaces_latest)
INSERT INTO main.finops_ref.workspace_bu VALUES
  ('1111111111111111', 'Energy Markets & Trading', 'jane.doe@yourco.com'),
  ('2222222222222222', 'Metering & NEM12',         'sam.lee@yourco.com'),
  ('3333333333333333', 'Shared BI / AI-BI',        NULL);

-- ---------------------------------------------------------------------------
-- 2) user -> team
--    Powers: Adoption tab Team column + "Activity by team" rollup
--    (the "top users by team" view for leadership reporting).
--    user matches system.query.history.executed_by (usually an email).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS main.finops_ref.user_team (
  user STRING,   -- e.g. 'mario@yourco.com'
  team STRING    -- e.g. 'Finance'
) USING DELTA;

INSERT INTO main.finops_ref.user_team VALUES
  ('mario@yourco.com', 'Finance'),
  ('priya@yourco.com', 'Data Science'),
  ('wei@yourco.com',   'Platform Engineering');

-- ---------------------------------------------------------------------------
-- Grant the app's service principal read access (edit the principal).
-- ---------------------------------------------------------------------------
GRANT USE CATALOG ON CATALOG main TO `<app-service-principal>`;
GRANT USE SCHEMA  ON SCHEMA  main.finops_ref TO `<app-service-principal>`;
GRANT SELECT      ON TABLE   main.finops_ref.workspace_bu TO `<app-service-principal>`;
GRANT SELECT      ON TABLE   main.finops_ref.user_team    TO `<app-service-principal>`;
