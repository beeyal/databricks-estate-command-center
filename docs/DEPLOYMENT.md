# Deploying Estate Command Center into a customer workspace

> [!IMPORTANT]
> **Config is now ONE file: `app.yaml`.** The old `config.yaml`, `customise.yaml`,
> and `deploy.sh` have been removed — there is no separate config to reconcile and no
> shell wrapper. Everywhere below that mentions `deploy.sh`, `customise.yaml`, or
> `config.yaml`, the equivalent is simply an env var in **`app.yaml`** (each value is
> self-documented there), deployed with `databricks bundle deploy -t customer`. The
> **[Customer deployment guide](CUSTOMER_DEPLOYMENT.md)** is the current, authoritative
> step-by-step; prefer it over the historical detail in this file.


This guide covers deploying the app into **any** Databricks workspace — your own
or a customer's — with a Databricks Asset Bundle. The app is a **small always-on
container** (~0.5 DBU/hr at `compute_size` MEDIUM, ≈ $270/mo — stop it and it costs
nothing; a shared multi-region demo workspace can over-report this ~3×) and ships
in **demo mode**, so the first deploy needs **no warehouse and no grants**. You
turn on live system-table reads later by flipping one flag.

> **Onboarding a customer = edit `customise.yaml`, then run `./deploy.sh`.**
> No code changes, ever. (SPEC §14)

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Databricks CLI ≥ 0.229 | `databricks version`. Bundles + `databricks apps` require a recent CLI. |
| Auth to the target workspace | `databricks auth login --host https://<workspace-host>` → creates a profile. Use `--profile <name>` throughout, or set `DATABRICKS_PROFILE`. |
| Databricks Apps enabled | Available in the target workspace (app switcher → Databricks Apps). |
| Built frontend committed | The React bundle (`frontend/dist`) is committed — deploying needs **no npm and no Python build step**. |

Live mode additionally needs a **SQL warehouse** (Serverless / 2X-Small is
plenty — the app issues only read-only `SELECT`s) and the read-only grants in
§5.

---

## 2. Configure the customer (`customise.yaml`)

`customise.yaml` is the single per-customer file. `deploy.sh` reads it and writes
the matching environment into `app.yaml` (and `catalog_scope` into `config.yaml`).

| `customise.yaml` field | → env var | Purpose |
|---|---|---|
| `customer_name` | `FINOPS_CUSTOMER_NAME` | Display label in the header. |
| `use_live` | `USE_LIVE` | Demo (`false`) ⇄ live (`true`) switch. |
| `warehouse_id` | `DATABRICKS_WAREHOUSE_ID` | Warehouse for live system-table reads. |
| `genie_space_id` | `GENIE_SPACE_ID` | Genie Space for the Ask Genie banner. |
| `llm_endpoint` | `FINOPS_LLM_ENDPOINT` | In-region Claude endpoint. Powers **both** `ai_narration` (prose) **and** the Query Advisor **EXPLAIN-validated rewrite** (`POST /api/queries/rewrite`), which additionally needs a warehouse for EXPLAIN in live mode. Keep it an in-region endpoint for data residency. |
| `fx_aud` | `FINOPS_FX_AUD` | AUD = USD list price × this rate. |
| `features.genie` | `FINOPS_FEATURE_GENIE` | Ask Genie banner + `/api/genie/ask`. |
| `features.ai_narration` | `FINOPS_FEATURE_AI_NARRATION` | LLM rewrite of recommendation rationale. |
| `features.dqm` | `FINOPS_FEATURE_DQM` | Data Quality Monitoring tab + `/api/dqm`. |
| `gold_schema` | `FINOPS_GOLD_SCHEMA` | **Required in live mode.** Schema (e.g. `main.finops_gold`) the nightly `finops_gold_sync` job writes estate payloads + a table-size census + warehouse/cluster daily history into; the app reads them in ~1s. The app **refuses to boot** in live mode without it (account-wide system-table scans per request aren't production-viable). Read-only-eval escape hatch: `FINOPS_ALLOW_LIVE_NO_GOLD=true` (slow). |
| `workspace_bu_table` | `FINOPS_WORKSPACE_BU_TABLE` | Customer table mapping `workspace_id → bu` (+ optional `owner`). Blank ⇒ BU dimension hidden. |
| `user_team_table` | `FINOPS_USER_TEAM_TABLE` | Customer table mapping `user → team` (Adoption tab). Blank ⇒ team dimension hidden. |
| `ingest_schema` | `FINOPS_INGEST_SCHEMA` | UC schema the daily `finops_admin_sync` job writes the `<ingest_schema>.finops_admin` Delta table into (budgets, policies, tokens, groups, monitors). Blank ⇒ admin-API panels show a "run finops_admin_sync" prompt. |
| `writeback_schema` | `FINOPS_WRITEBACK_SCHEMA` | UC schema for the writeback `finops_ack_status` table (Delta backend, live mode). Blank ⇒ falls back to `gold_schema`; if that's blank too the app reports writeback off. Not needed on `customer-lakebase`. |
| `catalog_scope` | → `config.yaml` (list) | Catalog allowlist for Access/Tables. Empty = all non-system. |

**Precedence** (highest first, enforced by `data/config.py`):
`app.yaml` env → `config.yaml` → built-in default. Because `deploy.sh` writes
env, `customise.yaml` always wins.

---

## 3. Deploy

Deploy is a **Databricks Asset Bundle**. The bundle (`databricks.yml`) defines
the App resource, the two scheduled jobs (`finops_gold_sync` + `finops_admin_sync`), and three targets:

- **`dev`** — your own workspace, development mode (default).
- **`customer`** — the customer's workspace (production mode). This target is
  **portable**: no host is hardcoded, so it deploys into whatever workspace your
  CLI profile points at. Authenticate with a profile against the customer
  workspace and pass `--profile` / `-p`. Writeback (acknowledge/ignore) runs on
  the **Delta** backend here — no extra infrastructure, but in live mode it needs a
  **writeback schema** set (`FINOPS_WRITEBACK_SCHEMA` or `gold_schema`) + `CREATE`/`MODIFY`
  on it for the app SP (see §5); if neither schema is set the app reports writeback off and
  hides the ack controls.
- **`customer-lakebase`** — identical to `customer` but the bundle also **creates
  a CU_1 Lakebase (managed Postgres) instance and binds it to the app**, so
  writeback uses managed Postgres for lower latency at scale. The binding
  auto-provisions the app service principal's Postgres role and injects the
  connection vars — nothing to set. Opt in only if you want it; the instance is
  always-on (~tens of $/mo). Switching an *existing* app onto this target needs a
  one-time `databricks bundle deployment bind estate estate-command-center -t customer-lakebase`
  first (so the bundle adopts the running app instead of erroring that it exists).

There is **no npm and no Python build** — the built `frontend/dist` is committed.

### Bundle deploy (canonical)

```bash
databricks bundle validate                              # sanity-check the bundle
databricks bundle deploy -t customer --profile CUSTOMER # sync + create the App
databricks bundle run estate -t customer --profile CUSTOMER  # start the App
```

### One command (convenience wrapper)

```bash
# Edit customise.yaml first, then:
./deploy.sh --profile CUSTOMER --target customer
```

`deploy.sh` is a convenience wrapper only. It reads `customise.yaml` → writes
`app.yaml`/`config.yaml` → runs `databricks bundle deploy` → starts the App →
prints the app URL. Everything it does you can do by hand with the bundle
commands above.

---

## 4. Feature flags (SPEC §10)

The deterministic **recommender, cost-attribution, and Genie-cost view are
ALWAYS on** — they call no model and are never gated. Four feature flags gate the
*optional* surfaces. When a flag is off the surface is hidden in the UI **and
inert server-side** (its endpoint 404s; no Genie/model call is ever made).

- **`genie`** *(default on)* — the Ask Genie banner + `/api/genie/ask`.
- **`ai_narration`** *(built-in default **off**; this repo's `config.yaml` turns
  it on to showcase it)* — the **LLM features on Query Advisor / recommendations**
  (calls the configured in-region Claude endpoint): (1) an LLM rewrite of the
  recommendation *rationale* prose, and (2) the on-demand **EXPLAIN-validated
  query rewrite** in the Query Advisor drill-down (proposes a runnable `SELECT`,
  then validates it with `EXPLAIN` against your warehouse and rejects any that
  don't bind). This is *distinct from Genie*: it does not power the Ask Genie
  banner. With it off, both fall back to deterministic rule text / guidance.
- **`dqm`** *(default on)* — the Data Quality Monitoring tab + `/api/dqm`.
- **`writeback`** *(default on)* — the acknowledge/ignore controls on every alert
  surface + `/api/acks`. Independent of AI/Genie; state lives in a small
  `finops_ack_status` Delta table in a **writeback schema** (or Lakebase, on the
  `customer-lakebase` deploy — see §3). On the Delta backend in live mode this needs a
  writeback schema (`FINOPS_WRITEBACK_SCHEMA` or `gold_schema`) + `CREATE`/`MODIFY` for the
  app SP (§5); if neither schema is set the app reports writeback **off** and hides the
  controls (no silent failure). Off ⇒ controls hidden + `/api/acks` 404, a strictly
  read-only app.

Each is switchable **off independently**. Any combination is valid — turn off
just narration, just Genie, just DQM, just writeback, or any mix.

Three equivalent ways to switch a feature off:

1. **CLI switch (one-off):** `./deploy.sh --no-genie` / `--no-narration` /
   `--no-ai` (both Genie + narration) / `--no-dqm`. These **override**
   `customise.yaml` for that run and write the matching `FINOPS_FEATURE_*` env
   into `app.yaml`.
2. **Declarative (permanent):** set the `features:` block in `customise.yaml`.
   Self-documenting; survives redeploys.
3. **Bundle / UI path:** set the relevant `FINOPS_FEATURE_*` env to the string
   `"false"` in `app.yaml`. You can also flip any flag after deploy in the
   **App UI → Settings → Environment** without redeploying code.

> `ai_narration` requires an FMAPI Claude endpoint (`llm_endpoint`). Turn it off
> (`--no-narration` / `ai_narration: false`) for a model-free deployment; the
> recommender still shows deterministic rule text.

---

## 5. Read-only grants for live mode (`USE_LIVE=true`)

Demo mode needs **no grants**. For live mode, grant the **app's service
principal** read-only access on the system schemas below. Reading a UC table
needs **`USE SCHEMA` + `SELECT`** (plus `USE CATALOG` on `system`) — `SELECT`
alone throws `INSUFFICIENT_PERMISSIONS: … does not have USE SCHEMA`.

> **Run these as a metastore admin, from a Unity Catalog context — not
> `hive_metastore`.** Only a metastore admin can grant on `system.*`. If your SQL
> editor's catalog selector is `hive_metastore`, `system.billing` resolves as
> `hive_metastore.system.billing` and fails — switch the catalog to `system` (or
> backtick-qualify `` `system`.`billing` `` as below).

> **Warehouse access is a UI permission, not SQL.** A SQL warehouse is not a Unity
> Catalog securable, so `GRANT ... ON WAREHOUSE` does not parse. Set it in **SQL
> Warehouses → your warehouse → Permissions → add the app SP → Can use** (and tick the
> **Databricks SQL access** entitlement for the SP if it still can't query).

```sql
GRANT USE CATALOG ON CATALOG `system`                              TO `<app-service-principal>`;  -- see the system catalog
GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`billing`            TO `<app-service-principal>`;  -- Overview, Workspaces, Genie $, AI $
GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`query`              TO `<app-service-principal>`;  -- Query Advisor, Warehouse Monitor, idle %
GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`compute`            TO `<app-service-principal>`;  -- Cluster Efficiency (clusters + node_timeline)
GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`access`             TO `<app-service-principal>`;  -- Access/audit, Adoption
GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`information_schema` TO `<app-service-principal>`;  -- Access grant graph, Governance
GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`lakeflow`           TO `<app-service-principal>`;  -- Automated %, jobs
```

Conditional grants:

- **If `features.dqm = true`** — grant the app SP read on
  **`system.data_quality_monitoring`** (`USE SCHEMA` + `SELECT`) so the Data Quality
  tab can read the quality/freshness results from `table_results`:
  ```sql
  GRANT USE SCHEMA, SELECT ON SCHEMA `system`.`data_quality_monitoring` TO `<app-service-principal>`;  -- Data Quality
  ```
  That schema only exists once anomaly detection has been enabled on a schema; if it
  doesn't exist the grant errors with `SCHEMA_DOES_NOT_EXIST` — in that case just leave
  the tab off (`FINOPS_FEATURE_DQM: "false"`) and skip the grant.
- **If `features.genie = true`** — grant the app's service principal
  **`CAN RUN` on the Genie Space** (`genie_space_id`) so the Ask Genie banner
  can start conversations. The Genie Space must be curated over the cost/usage/
  access tables.
- **If `gold_schema` is set** — the `finops_gold_sync` job's service principal
  needs read on the system tables the app reads (the grants above) plus
  **`CREATE`/`MODIFY` on `<gold_schema>`** so it can write
  `<gold_schema>.finops_gold` and `<gold_schema>.finops_gold_table_sizes`. See
  §6, "Live-mode serving layer — gold tables".
- **If `writeback` stays on in live mode with the Delta backend** — set a
  **writeback schema** (`FINOPS_WRITEBACK_SCHEMA`, or fall back to `gold_schema`) and grant
  the **app's own** service principal `CREATE`/`MODIFY` on it, for the single
  `finops_ack_status` table:
  ```sql
  GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO `<app-service-principal>`;
  ```
  This is the one non-read grant the app SP needs (and only for its own bookkeeping table).
  If neither schema is set the app reports writeback off and hides the ack controls; the
  `customer-lakebase` target needs none of this (state lives in the bound Postgres).

> Prefer `GRANT SELECT ON SCHEMA system.information_schema` (and `system.lakeflow`)
> over table-by-table grants — it is fewer statements and
> automatically covers the individual views the app reads
> (`system.information_schema.{catalog,schema,table,routine}_privileges`,
> `system.lakeflow.jobs`).

Enabling system tables themselves (one-time, per metastore) may require a
metastore admin to enable the `system.billing`, `system.access`,
`system.query`, `system.compute` and `system.lakeflow` schemas.

### The two scheduled jobs (`finops_gold_sync` + `finops_admin_sync`)

The bundle defines **two scheduled jobs**, both shipped **PAUSED**:

- **`finops_gold_sync`** (`data/gold.py`, nightly) — precomputes every estate
  payload + a per-table size census into `<gold_schema>.finops_gold` and
  `<gold_schema>.finops_gold_table_sizes`, so the app serves each page in
  sub-second time (see §6, "Live-mode serving layer — gold tables"). Its service
  principal needs read on the system tables the app reads + `CREATE`/`MODIFY` on
  `<gold_schema>`.
- **`finops_admin_sync`** (`data/admin_sync.py`, daily) — pulls the
  account/workspace admin-API data that is **not** in system tables — budgets,
  budget/cluster policies, tokens, SCIM groups, workspace assignments, and DQM
  monitors — into a Delta `<ingest_schema>.finops_admin` table. That table backs
  the governance budget/policy/PAT/alert tiles and the Access groups + PAT panels
  with a "last refreshed" time. Its service principal needs **account admin**
  (budgets, budget policies, SCIM groups, workspace assignments) **+ workspace
  admin** (tokens, alerts, quality monitors) — heavier than the app's read-only
  access — plus `CREATE`/`MODIFY` on `<ingest_schema>`. Keep it paused until those
  admin grants are in place.

---

## 6. The `USE_LIVE` demo → live switch

| | Demo (`use_live: false`) | Live (`use_live: true`) |
|---|---|---|
| Data source | Seeded demo data in `data/` | Customer's system tables via `warehouse_id` |
| Warehouse | none | required |
| Grants | none | §5 |
| Genie / narration | canned streamed answers | real Genie Space / FMAPI Claude |
| Good for | first deploy, demos, POC | production cost governance |

To go live:

1. Set `use_live: true` and `warehouse_id: <id>` in `customise.yaml`.
2. Apply the §5 grants to the app service principal.
3. (If using Genie/narration) set `genie_space_id` / `llm_endpoint`.
4. `./deploy.sh --profile CUSTOMER --target customer` (or flip `USE_LIVE=true`
   in App UI → Settings → Environment for a no-redeploy switch).

In live mode the app is **fail-closed, never fail-to-demo**: a missing grant or
failed read returns a clear 503 naming the exact system table + grant to fix
(and Ask Genie returns an honest "not configured / couldn't answer" message) —
it never falls back to fabricated or demo data. Optional enrichments (BU, team,
workspace-name resolution, top-lists) degrade to blank rather than failing the
whole tab. Demo answers appear only when `USE_LIVE=false`.

### Live-mode serving layer — gold tables (required for live mode)

The fast/cheap serving layer is a **nightly precompute into Delta "gold" tables**
that the app reads instantly. Set **`gold_schema`** (e.g. `main.finops_gold`) and
unpause the **`finops_gold_sync`** job: it computes every estate payload AND a
per-table **size census** (there is no GA system table for bulk table size, so it
iterates `information_schema.tables` → `DESCRIBE DETAIL`, which is metadata-only —
no data scan) into `<gold_schema>.finops_gold` + `<gold_schema>.finops_gold_table_sizes`.
The app then serves each estate page from the gold table in **~1s on a small
serverless SQL warehouse that scales to zero between reads**. Day-old data is fine
for FinOps trending, and the size census also powers the Tables tab sizes + the
**cold-storage** (large + rarely-read) view. `gold_schema` is **required in live
mode** — the app's reads are over account-wide system tables (tens of millions of
rows), so live-per-request isn't production-viable and the app refuses to boot in
live mode without it (escape hatch for read-only evals: `FINOPS_ALLOW_LIVE_NO_GOLD`).

> The **Warehouse Monitor** and **Cluster Efficiency** tabs are the two heaviest
> live scans (`system.query.history` aggregation and per-node/per-minute
> `system.compute.node_timeline`, respectively), so they benefit most from the
> roll-up. The Warehouse Monitor caches its **default 30-day** window; the
> narrower time chips (7d/24h/…) compute live on demand — those are small,
> fast scans, and gold only holds the 30-day snapshot, so it is never relabelled
> as a shorter window. Cluster Efficiency has no window selector and always
> serves from gold when it is configured.

The gold job SP needs read on the system tables the app reads + `CREATE`/`MODIFY`
on the gold schema. No extra Python deps (just `databricks-sdk`).

---

## 7. Minimal footprint

- **Small always-on App container.** A Databricks App runs a continuously
  provisioned container — it does **not** scale to zero while deployed. It's a
  lean single container (~0.5 DBU/hr at `compute_size` MEDIUM, ≈ $270/mo if left
  up 24×7; a shared multi-region demo workspace can over-report this ~3×). To pay
  nothing when it isn't needed, **stop the app** (Compute →
  Apps → Stop) and start it again on demand.
- **Read-only.** Live mode issues only `SELECT`s against system tables. It
  writes nothing to the customer's data.
- **No standing warehouse in demo mode.** The default deploy needs no warehouse
  at all. Live mode uses a small/Serverless warehouse that *does* scale to zero
  between reads — so the warehouse, unlike the app container, costs nothing at rest.
- **Tiny sync surface.** The bundle syncs only `frontend/dist/**` + the Python
  backend; `.databricksignore` excludes `node_modules`, `.venv`, `frontend/src`,
  etc. — so the workspace footprint is a few MB.
- **Model-free is one switch away.** AI is on by default, but
  `--no-ai` (or `ai_narration: false`) plus `--no-genie` makes the app issue
  zero model calls — the deterministic recommender still shows rule text.

---

## 8. Quick reference

```bash
# First deploy (demo, no grants) into your own workspace — canonical bundle path
databricks bundle deploy -t customer
databricks bundle run estate -t customer

# Or via the convenience wrapper
./deploy.sh

# Deploy into a customer workspace with narration + Genie off (DQM stays on)
./deploy.sh --profile CUSTOMER --target customer --no-ai

# Validate the bundle without deploying
databricks bundle validate

# Flip to live later (edit customise.yaml: use_live/warehouse_id) then redeploy
databricks bundle deploy -t customer --profile CUSTOMER && \
  databricks bundle run estate -t customer --profile CUSTOMER
```
