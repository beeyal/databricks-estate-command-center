<div align="center">

# 🚀 Estate Command Center — Deployment Guide

**One screen for cost, usage, access & governance across your Databricks estate.**

*Read-only by construction · deploys from your own workspace · nothing to build*

</div>

---

The Estate Command Center reads your own Databricks **system tables** and turns them
into a single screen — with a priority-ranked recommendation for every finding. It
only ever runs `SELECT` and read-only APIs, so it can **never change or touch your
data**.

You deploy it as a **Databricks Asset Bundle (DAB)** from inside your own workspace. The
compiled front-end ships with the code, so **nothing builds** — no Python, no npm, no
tools on your laptop. Everything below happens in the Databricks UI.

> [!TIP]
> **The short version:** download the repo ZIP from GitHub on your laptop, import it into your
> workspace (Step ①), paste the deploy cell from Step ③ into a new notebook on Serverless, put your
> warehouse and gold schema at the top, and run it. The cell deploys the app and both jobs, grants
> the app its warehouse access, runs the gold job once and turns its nightly schedule on. Then run
> the system-table grants (Step ④) in a `%sql` cell. Databricks never needs to reach GitHub, and
> nothing runs on your laptop.

> [!NOTE]
> **The whole thing is six steps**, and **①–⑤ are the required path; ⑥ is optional.**
> ① import the code → ② choose your settings → ③ run the deploy cell → ④ grant read access
> (SQL) → ⑤ check the daily jobs → ⑥ *(optional)* light up BU/team. Everything happens in the
> workspace or in a notebook. Anything in a collapsed *FYI* / *conditional* box is skippable.

---

## What gets created in your workspace

| | Object | What it is |
|---|---|---|
| 📊 | **estate-command-center** | The app itself — a small always-on container (~0.5 DBU/hr at `compute_size` MEDIUM, ≈ $270/mo; stop it to pay nothing). A shared multi-region demo workspace can over-report this ~3×. |
| ⚡ | **1 SQL warehouse** *(you provide — not created by the deploy)* | A serverless SQL warehouse you already have powers the reads; supply its id as `DATABRICKS_WAREHOUSE_ID`. Scales to zero when idle. |
| 🗄️ | **Gold Delta tables** under `<gold_schema>` (`finops_gold`, `finops_gold_table_sizes`, `finops_gold_warehouse_daily`, `finops_gold_cluster_daily`) | Nightly-precomputed estate payloads (every tab) + a per-table size census + daily-grain warehouse/cluster history, so estate pages load in ~1s instead of re-scanning account-wide system tables per request. **Required in live mode** — populated by the gold-sync job below. |
| 💾 | **`finops_payload_cache`** (created automatically) | A small cache of each tab's last good result, so a restarted app serves pages straight away instead of recomputing them. On `-t customer` it lives in the bundle's Lakebase instance; on `-t customer-delta` it's a Delta table in your writeback schema (the gold schema unless you set `FINOPS_WRITEBACK_SCHEMA`). Nothing to configure. |
| 🔁 | **Two scheduled jobs** (`estate-command-center-gold-sync` nightly + `estate-command-center-admin-sync` daily) | Gold-sync precomputes the estate payloads (**required for live mode**); admin-sync fills the governance & access panels that don't live in system tables (budgets, policies, tokens, groups). **Both ship paused** — unpause gold-sync for live (admin-sync is optional). |

> [!IMPORTANT]
> The app runs as its own **service principal** — a machine identity Databricks creates
> when you deploy it. You grant *that* identity read access to your system tables in
> step ④. Nothing else needs credentials.

---

## Step ① — Add the code to your workspace

1. On your laptop, open https://github.com/beeyal/databricks-estate-command-center, click the
   green **Code** button and choose **Download ZIP**. You get
   `databricks-estate-command-center-main.zip`. You don't need to unzip it.
2. In Databricks, open **Workspace**, go to your home folder, click the **⋮** menu (or right-click
   the folder), choose **Import**, drop in the ZIP and click **Import**. If Databricks unpacks it
   into a folder instead of keeping the ZIP, that works too.
3. Click **⋮** next to the uploaded file (or folder) and choose **Copy URL/path → Full path**.
   You'll paste this into the deploy cell as `REPO_PATH`.

The ZIP includes the official Databricks CLI release (checksum-verified), which the deploy cell
runs for you, so your workspace never needs to reach GitHub. You can also upload the ZIP to a
Unity Catalog volume and use its `/Volumes/...` path. If you already have the repo as a Git
folder, point `REPO_PATH` at that folder instead.

---

## Step ② — Set your configuration

**You don't edit any files.** Every setting lives in `app.yaml` (a list of `- name: … /
value: …` pairs, each with a comment explaining what it does and what happens if you leave it
blank), but you set them in the deploy cell in Step ③, which writes them into `app.yaml` at
deploy time. Keep that notebook: it holds your settings for every future upgrade.

The app **ships in live mode** (`USE_LIVE: "true"`). Until you set the two values below it
boots showing **demo data with a "finish setup to go live" banner** — so you can deploy
first and configure after, and it never fails to start.

### Set these **2** values to read your real estate (everything else can stay as-is):

```python
WAREHOUSE_ID = ""                  # blank = the cell picks a serverless SQL warehouse you can use
GOLD_SCHEMA  = "main.finops_gold"  # catalog.schema the nightly job precomputes into
```

That's the whole required configuration. Deploy (Step ③), grant the app read access
(Step ④), and run the gold job once (Step ⑤).

**What each value does:**

| Value | What it is | If left blank |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | The serverless SQL warehouse the app queries with. Grant its service principal **CAN USE** (Step ④). | App stays on demo data + shows the setup banner. |
| `FINOPS_GOLD_SCHEMA` | Where the nightly `estate-command-center-gold-sync` job stores its pre-aggregated tables. The app reads these in ~1 s instead of scanning account-wide system tables on every page. **The job creates the schema + tables for you** — you just need `CREATE` on the catalog. | App stays on demo data + shows the setup banner. |
| `USE_LIVE` | Already `"true"` (ships live). Set to `"false"` only to **force demo on purpose** (no banner). | — |

> [!IMPORTANT]
> **Why a gold schema is needed for live.** The app reads `system.billing.usage`,
> `system.query.history`, `system.lakeflow.*` — account-wide tables with tens of millions
> of rows. Scanning those on every page load isn't viable, so a nightly job pre-aggregates
> them once into the gold schema and the app reads that. Until `FINOPS_GOLD_SCHEMA` (and a
> warehouse) are set, the app **doesn't fail** — it falls back to demo with the setup
> banner. (Read-only eval with no writable schema? Set `FINOPS_ALLOW_LIVE_NO_GOLD: "true"`
> to read straight off system tables — slow, not for production.) After configuring, run
> `estate-command-center-gold-sync` **once** (Step ⑤) or the estate pages stay empty until the first
> nightly run.

### Everything else is optional — leave it blank unless you want it

Every other value in `app.yaml` has a safe default and its comment says what blank does. To
set one, add it to `APP_SETTINGS` in the deploy cell by its name, for example
`APP_SETTINGS = {"FINOPS_CUSTOMER_NAME": "Acme", "FINOPS_FEATURE_GENIE": "false"}`.
The common ones:

- **`FINOPS_CUSTOMER_NAME`** — the label in the app header. Change to your company (cosmetic).
- **Feature toggles** (`FINOPS_FEATURE_GENIE` / `_AI_NARRATION` / `_DQM`) — all ship **on**.
  Set any to `"false"` to switch that surface off — it's hidden in the UI **and** its API
  goes inert (no half-rendered tabs, and no model call is ever made). See the table just
  below for exactly what each one removes.
- **Acknowledge/dismiss is built in** (not a toggle — it always ships). You don't configure
  it here; **where its state is stored is set by the deploy target** (see Step ③): the
  recommended `-t customer` uses a managed Lakebase (Postgres) instance the bundle wires up
  for you (nothing to set); `-t customer-delta` stores it in a tiny Delta table in your gold
  schema instead (leave `FINOPS_WRITEBACK_SCHEMA` blank to reuse the gold schema, and grant
  the app SP write on it — Step ④). If it can't persist, the app hides the controls with an
  honest note — it never silently drops a dismissal.
- **Business-unit / team reporting** (`FINOPS_WORKSPACE_BU_TABLE`, `FINOPS_USER_TEAM_TABLE`)
  — blank hides those columns (Step ⑥ if you want them).

#### What each `"false"` turns off (and what stays)

| Set to `"false"` | You lose | You keep |
|---|---|---|
| **`FINOPS_FEATURE_GENIE`** | The **Ask Genie** banner (natural-language Q&A). `/api/genie/ask` goes inert. | Everything else — including the **Genie $** tab (that's Genie *cost attribution* from billing, not the Q&A feature, so it stays). |
| **`FINOPS_FEATURE_AI_NARRATION`** | The **LLM-written wording** on recommendations, and the Query Advisor's **AI SQL-rewrite** suggestion. | **All recommendations still appear**, with clear rule-based explanations + next steps — just not LLM-phrased. Query Advisor still finds & ranks the queries. |
| **Both of the above** *(a "no-AI" deploy)* | Only the two rows above. **No LLM endpoint is needed at all** — nothing ever calls a model, so no data-residency/endpoint setup. | A **fully-featured cost & governance console**: every tab, all cost/usage/access/governance data, and the full priority-ranked recommendation engine. |
| **`FINOPS_FEATURE_DQM`** | The **Data Quality** tab (Lakehouse Monitoring). `/api/dqm` goes inert. | All 14 other tabs. *(Turn this off if you don't use Lakehouse Monitoring — then you can skip its grant in Step ④.)* |

*(Acknowledge / dismiss is **built in** — there's no flag to turn it off. The only related choice is where its state is stored, set by the deploy target in Step ③.)*

> [!NOTE]
> **FYI** — the deterministic recommender, cost attribution, and Genie-cost view are **always
> on** regardless of these flags; they call no model. Turning flags off never fabricates or
> hides *data*, it only removes the optional surface itself.

> [!NOTE]
> The cell writes your values into a scratch copy of `app.yaml` on the notebook's compute, so
> the uploaded repo is never changed and a typo can't break the file. Values must be text, so
> quote them (`"false"`, not `False`).

---

## Step ③ — Deploy it from a notebook

Create a new notebook, click **Connect** (top right) and choose **Serverless**. Paste the cell
below, fill in the values at the top (`REPO_PATH` is the path from Step ①), and run it:

```python
# ---- Estate Command Center: fill in these values, then run this cell -------------------
REPO_PATH     = ""          # the uploaded databricks-estate-command-center-main.zip, or the folder it unpacked to
WAREHOUSE_ID  = ""          # blank = use a serverless SQL warehouse you can access
GOLD_SCHEMA   = ""          # catalog.schema the nightly job precomputes into, e.g. "main.finops_gold"
TARGET        = "customer"  # "customer" (Lakebase for ack state) or "customer-delta" (Delta table)
RUN_GOLD_SYNC = True        # run the gold job once now and turn its nightly schedule on
APP_SETTINGS  = {}          # any other app.yaml setting by name, e.g. {"FINOPS_CUSTOMER_NAME": "Acme"}
BUNDLE_VARS   = {}          # optional, e.g. {"app_name": "estate-command-center-2"}
# -----------------------------------------------------------------------------------------
import glob, os, subprocess, sys, tempfile, zipfile
src = REPO_PATH.strip().rstrip("/")
if src.endswith(".zip"):
    unpacked = tempfile.mkdtemp(prefix="ecc_zip_")
    zipfile.ZipFile(src).extractall(unpacked)
    src = unpacked
found = glob.glob(src + "/databricks.yml") + glob.glob(src + "/*/databricks.yml")
if not found:
    raise FileNotFoundError(f"Couldn't find the Estate Command Center repo at '{REPO_PATH}'. Check REPO_PATH.")
repo = os.path.dirname(found[0])
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
env = dict(os.environ, ECC_HOST=ctx.apiUrl().get(), ECC_TOKEN=ctx.apiToken().get())
settings = {"DATABRICKS_WAREHOUSE_ID": WAREHOUSE_ID, "FINOPS_GOLD_SCHEMA": GOLD_SCHEMA, **APP_SETTINGS}
args = [sys.executable, os.path.join(repo, "scripts", "notebook_deploy.py"), "--source", repo, "--target", TARGET]
args += [x for k, v in settings.items() for x in ("--set", f"{k}={v}")]
args += [x for k, v in BUNDLE_VARS.items() for x in ("--var", f"{k}={v}")] + (["--gold-sync"] if RUN_GOLD_SYNC else [])
proc = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for line in proc.stdout:
    print(line, end="")
if proc.wait() != 0:
    raise RuntimeError("The deploy stopped with an error. The lines above say what went wrong.")
```


The output streams into the notebook. The cell deploys the app and the two scheduled jobs,
starts the app, grants the app's service principal **CAN USE** on the warehouse, runs
`estate-command-center-gold-sync` once and turns its nightly schedule on (`RUN_GOLD_SYNC`). The
last lines print the app URL and the app's **service principal** ID, which you need for Step ④.
You don't sign in or paste a token: the notebook runs **as you**.

**`TARGET = "customer"` is the one to use.** It stores the acknowledge/dismiss state in a
small managed Lakebase (Postgres) instance the bundle creates and wires for you,
**nothing to configure.** (This adds one small always-on instance, ~tens of $/mo.) If that
name is already taken in your workspace, set `BUNDLE_VARS = {"lakebase_instance": "<another name>"}`.

<details>
<summary><b>FYI — don't want a managed-Postgres instance?</b> (optional; skip if using the default)</summary>

<br>

Set **`TARGET = "customer-delta"`** instead of `"customer"`. Identical app, but the
acknowledge/dismiss state goes in a tiny **Delta** table via your warehouse — no extra
infrastructure, no extra cost. On that target, in live mode, do the optional writeback
grant listed under Step ④'s conditional grants (leave `FINOPS_WRITEBACK_SCHEMA` blank to
reuse `FINOPS_GOLD_SCHEMA`; the app auto-creates the table). If it can't write, the app
just hides the ack controls — it never loses a dismissal silently.

Pick the target once. Switching an app that is already deployed to the other target isn't
supported from the deploy cell.

</details>

---

## Step ④ — Grant the app read access to your data

**The app's service principal** is printed in the last lines of the deploy cell's output
(a client ID like `a1b2c3d4-…`). Copy it.

There are two required grants: **(a)** let the app use the warehouse, and **(b)** read
access to the system tables.

**a. Let the app use the warehouse.** The deploy cell does this for you and prints
`Granted the app CAN USE on warehouse …`. If it printed `Couldn't grant CAN USE` instead
(you don't have CAN MANAGE on the warehouse), someone who does runs this in a notebook:

```python
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql
WorkspaceClient().warehouses.update_permissions(
    warehouse_id="<warehouse id>",
    access_control_list=[sql.WarehouseAccessControlRequest(
        service_principal_name="<app-sp>", permission_level=sql.WarehousePermissionLevel.CAN_USE)])
```

**b. Grant read access to the system tables (SQL).** In a notebook, add a cell that starts
with `%sql`, paste the block below, replace `<app-sp>` with the service principal client ID,
and run it (the SQL editor works too):

### Grant system-table access to the app service principal

**Why this matters:** Each tab in the app reads specific system tables. If a grant is missing,
that tab displays *"needs read access"* with the exact `GRANT` statement to run — it never
crashes or fabricates data. The Jobs tab is particularly common to miss: it needs
`system.lakeflow.job_run_timeline`. The Data Quality tab needs `SELECT` on the
`system.data_quality_monitoring.table_results` **table** (a schema-level grant fails — see below). These grants are **required before the tabs show live data**.

**Run this SQL block as an account/metastore admin** (the app's service principal is read-only
and cannot grant itself access). Replace `<app-sp>` with the service principal client ID you
copied above:

```sql
-- ============================================================================
-- Estate Command Center — Complete system-table grants
-- ============================================================================
-- Replace <app-sp> with the app's service principal (e.g. a1b2c3d4-…)
-- Run this as account/metastore + workspace admin
-- ============================================================================

-- Catalog visibility
GRANT USE CATALOG ON CATALOG system TO `<app-sp>`;

-- Billing & cost (Overview, Workspaces, Genie $, AI $, Cost drivers)
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing TO `<app-sp>`;
GRANT SELECT ON TABLE system.billing.usage TO `<app-sp>`;
GRANT SELECT ON TABLE system.billing.list_prices TO `<app-sp>`;

-- Query observability (Query Advisor, Warehouse Monitor, idle % detection)
GRANT USE SCHEMA, SELECT ON SCHEMA system.query TO `<app-sp>`;
GRANT SELECT ON TABLE system.query.history TO `<app-sp>`;

-- Compute resources (Cluster Efficiency, cluster sizing & idle analysis)
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute TO `<app-sp>`;
GRANT SELECT ON TABLE system.compute.clusters TO `<app-sp>`;
GRANT SELECT ON TABLE system.compute.node_timeline TO `<app-sp>`;
GRANT SELECT ON TABLE system.compute.warehouses TO `<app-sp>`;

-- Access & lineage (Tables page, Adoption & Value, Access page)
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `<app-sp>`;
GRANT SELECT ON TABLE system.access.audit TO `<app-sp>`;
GRANT SELECT ON TABLE system.access.table_lineage TO `<app-sp>`;
GRANT SELECT ON TABLE system.access.workspaces_latest TO `<app-sp>`;

-- Information schema (Access page grants graph, Governance, Data Classification)
GRANT USE SCHEMA, SELECT ON SCHEMA system.information_schema TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.tables TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.table_privileges TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.schema_privileges TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.catalog_privileges TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.routine_privileges TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.table_tags TO `<app-sp>`;
GRANT SELECT ON TABLE system.information_schema.column_tags TO `<app-sp>`;

-- Lakeflow (Jobs tab cost & reliability + job names / owners / schedules,
-- Pipelines tab names / types / update history, automated % calculation)
-- ⚠️ Jobs tab will fail with "needs read access" if this grant is missing
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `<app-sp>`;
GRANT SELECT ON TABLE system.lakeflow.job_run_timeline TO `<app-sp>`;

-- Model serving (OPTIONAL — AI $ "Who is calling": per-person requests/tokens,
-- including shared pay-per-token endpoints that billing attributes to the owner)
GRANT USE SCHEMA, SELECT ON SCHEMA system.serving TO `<app-sp>`;

-- + USE CATALOG / USE SCHEMA on any user catalog you want the Tables page to inspect in detail.
```

**What each system schema provides:**

| Schema | Tables | Powers |
|---|---|---|
| `system.billing` | `usage`, `list_prices` | Overview KPIs, spend/DBU trends, cost drivers, per-SKU breakdown, Genie $ attribution, AI $ inventory |
| `system.query` | `history` | Query Advisor, Warehouse Monitor, idle warehouse detection, top users/warehouses, query flags |
| `system.compute` | `clusters`, `node_timeline`, `warehouses` | Cluster Efficiency (sizing/idle/CPU analysis), warehouse queue % & spill detection |
| `system.access` | `audit`, `table_lineage`, `workspaces_latest` | Tables page (usage/freshness), Adoption & Value map, Access page last-accessed tracking |
| `system.information_schema` | `tables`, `*_privileges`, `*_tags` | Access page grants graph (who-can-access-what), Governance (certification coverage, data classification), governed tags |
| `system.lakeflow` | `job_run_timeline`, `jobs`, `pipelines`, `pipeline_update_timeline` | **Jobs tab** (cost/run, failure rate, runtime spikes, cost growth, owner, schedule, paused) — **fails with "needs read access" if missing**; **Pipelines tab** names, types and update history |
| `system.serving` *(optional)* | `endpoint_usage`, `served_entities` | AI $ **Who is calling** — who sends requests to each endpoint |

> Reading a Unity Catalog table needs **both** `USE SCHEMA` **and** `SELECT` (plus
> `USE CATALOG` on `system`) — `SELECT` alone gives `INSUFFICIENT_PERMISSIONS: … does
> not have USE SCHEMA`. The combined grants above cover it.

**If a tab still shows "needs read access" after running the SQL above:** the warehouse may be
stopped, or a specific table grant is still missing. Check the exact error message in the tab
— it names the missing object and the exact `GRANT` to run.

<details>
<summary><b>FYI — widen the Access page to your whole estate</b> (advanced, optional; the app works fine without it)</summary>

<br>

The Access page's grant graph reads `system.information_schema.table_privileges`, which
Unity Catalog **row-filters to only the securables the app's SP can see**. With just the
`system` grants above, the Access tab covers the catalogs the app SP already sees — **not
necessarily your whole estate**. To cover everything, give the app SP broad *visibility*
(not data access): `GRANT BROWSE ON CATALOG <catalog> TO \`<app-sp>\`;` per catalog.
`BROWSE` is least-privilege — it makes objects **visible** in `information_schema` without
granting `SELECT` on the data. Optional: every other tab works from `system.*` alone, and
the Access page notes when its graph is scoped. Widening the app SP's reach across all
catalogs is a real security trade-off — decide per your posture.

Generate the grants for every catalog automatically (run as a metastore admin; review before running):
```sql
SELECT 'GRANT BROWSE ON CATALOG `' || catalog_name ||
       '` TO `<app-service-principal>`;' AS grant_stmt
FROM system.information_schema.catalogs
WHERE catalog_name NOT IN ('system','__databricks_internal','samples')
ORDER BY catalog_name;
-- Copy the grant_stmt column from the results and run it.
```

</details>

**That's the whole required grant** (a + b). The three below are **conditional — skip any
that don't apply to you**; most deployments need none of them.

<details>
<summary><b>Conditional grants</b> — only if you use Data Quality, a custom Genie Space, or the Delta writeback target (click to expand)</summary>

<br>

- **Data Quality tab** (`FINOPS_FEATURE_DQM: "true"`) — also run:
  ```sql
  -- Grant on the TABLE only. Do NOT grant on the schema.
  GRANT SELECT ON TABLE system.data_quality_monitoring.table_results TO `<app-sp>`;
  ```
  **Why table-only:** unlike the other system schemas, `system.data_quality_monitoring` is
  owned by a Databricks-internal service principal, and Databricks deliberately doesn't give
  customer admins MANAGE on the schema (it will hold internal tables). Admins *do* get MANAGE
  on `table_results`. So `GRANT USE SCHEMA, SELECT ON SCHEMA system.data_quality_monitoring …`
  fails with `PERMISSION_DENIED: User does not have MANAGE on Schema
  'system.data_quality_monitoring'` — even for an account + metastore admin — and the
  schema's **Permissions** button in Catalog Explorer is greyed out. `USE SCHEMA` on it is
  already granted to all account users by Databricks, so the table grant is all you need.
  Run it as an **account admin** (SQL, or Catalog Explorer → the `table_results` table →
  Permissions).
  That schema **only exists once someone has enabled anomaly detection** on a schema; if you
  don't use Data Quality Monitoring the grant errors with `SCHEMA_DOES_NOT_EXIST` — in that
  case just leave the tab off (`FINOPS_FEATURE_DQM: "false"` in Step ②) and skip this.
- **Your own Genie Space** — *only* if Ask Genie is on **and** you set `GENIE_SPACE_ID` to a
  Space you curated: grant the app SP **`CAN RUN`** on it (Genie Space → Share → add the app
  SP as *Can Run*). If you leave `GENIE_SPACE_ID` blank (the default) the deploy auto-creates
  a Space the app owns — no grant needed.
- **`-t customer-delta` target only** — the recommended `-t customer` (Lakebase) target
  needs none of this. On `customer-delta`, acknowledge/dismiss state goes in a Delta table,
  so grant the app SP `CREATE`/`MODIFY` on its writeback schema:
  `GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO \`<app-sp>\`;` (that schema is
  `FINOPS_GOLD_SCHEMA`, or `FINOPS_WRITEBACK_SCHEMA` if you set one). Without it the app just
  hides the ack controls — it never loses a dismissal silently.

</details>

> [!NOTE]
> **FYI — who runs these grants.** They need an **account/metastore + workspace admin**;
> the app's own SP is read-only and can't grant itself anything. If a grant is ever missing,
> the affected tab shows a clear message naming the exact statement to run — never a made-up
> number.

---

## Step ⑤ — Turn on the scheduled jobs

Both jobs ship paused, and every deploy pauses them again. With `RUN_GOLD_SYNC = True` the
deploy cell runs gold-sync once and turns its nightly schedule back on every time, so there is
nothing to do for it after an upgrade:

- **`estate-command-center-gold-sync`** (nightly; `finops_gold_sync` in `databricks.yml`; the
  prefix is your `app_name`) — **required for live mode.** Precomputes every estate
  payload (all tabs, including jobs, recommendations and governance) + a per-table size
  census into several tables under `<gold_schema>` — `finops_gold`, `finops_gold_table_sizes`,
  `finops_gold_warehouse_daily` and `finops_gold_cluster_daily` (the last two are daily-grain
  history that power the Warehouse Monitor / Cluster Efficiency time filters beyond system-table
  retention) — so the app serves each page in ~1s instead of re-scanning account-wide system
  tables per request. The deploy cell runs it once straight away so the app has data to
  serve (otherwise live pages read empty until the first nightly run). Its service principal
  needs: **`Can use` on the SQL warehouse + the `Databricks SQL access` entitlement** (the job
  runs all its SQL through that warehouse), read on the system tables the app reads, **`CREATE`
  on the catalog** (the job auto-creates the schema — you don't pre-create it) **+ `MODIFY` on
  `gold_schema`**. If your catalog is locked down, pre-create the schema and grant the SP
  `CREATE, MODIFY` on it instead.
- **estate-command-center-admin-sync** (daily) — fills the governance budget / policy /
  PAT / alert tiles and the Access groups + PAT panels. It reads admin APIs (budgets,
  cluster policies, tokens, SCIM groups, monitor configs), so set it to **run as a service
  principal that is an account admin + workspace admin** — a higher level than the
  read-only app SP above — with **`Can use` on the SQL warehouse + `Databricks SQL access`**
  (it also runs its writes through the warehouse) and **`CREATE` on the catalog + `MODIFY` on
  `ingest_schema`** (the job auto-creates the schema; it writes the `<ingest_schema>.finops_admin`
  Delta table). To turn it on, run this in a notebook with that service principal's ID:

  ```python
  from databricks.sdk import WorkspaceClient
  from databricks.sdk.service.jobs import CronSchedule, JobRunAs, JobSettings, PauseStatus
  w = WorkspaceClient()
  job = next(w.jobs.list(name="estate-command-center-admin-sync"))
  w.jobs.update(job_id=job.job_id, new_settings=JobSettings(
      run_as=JobRunAs(service_principal_name="<admin-sp>"),
      schedule=CronSchedule(quartz_cron_expression="0 0 2 * * ?", timezone_id="UTC",
                            pause_status=PauseStatus.UNPAUSED)))
  ```

  A deploy pauses it again, so rerun that snippet after each upgrade if you use admin-sync.

> [!NOTE]
> Until admin-sync has run, those specific panels show a "run the daily job" prompt (and a
> "last refreshed" time once it has). They never show sample numbers.

---

<details>
<summary><b>Prefer the CLI on your laptop?</b> (same bundle, from a terminal)</summary>

<br>

```bash
brew install databricks                                  # macOS (winget install Databricks.CLI on Windows)
git clone https://github.com/beeyal/databricks-estate-command-center && cd databricks-estate-command-center
databricks auth login --profile <name>                   # browser prompt
# edit app.yaml (the values in Step ②) before deploying
databricks bundle deploy -t customer -p <name>
databricks bundle run   estate -t customer -p <name>
```

Then do steps ④ and ⑤ exactly as above. The `customer` target is portable — it deploys
to whatever workspace your profile points at, with no host hardcoded.

</details>

---

## Step ⑥ *(optional)* — Light up **business unit** & **team**

Databricks doesn't know your org structure — which workspace belongs to which business
unit, or which user is on which team. Two small tables **you own** add that. Skip this and
the app simply hides the BU and team features (no empty placeholders); add them and the
**Workspaces BU facet**, **per-BU cost attribution**, and the Adoption tab's **Team column +
"Activity by team"** rollup (the *top-users-by-team* view for leadership reporting) light up.

It's a 2-minute copy-paste:

1. **Create the tables.** Open [`reference/mapping_tables_setup.sql`](../reference/mapping_tables_setup.sql) in a SQL editor, edit the sample rows to your real workspaces/users, and run it. It creates both tables and the `GRANT` for the app's service principal. (Find workspace IDs with `SELECT workspace_id, workspace_name FROM system.access.workspaces_latest`.)

   | Table | Columns | Powers |
   |---|---|---|
   | `workspace_bu` | `workspace_id`, `bu`, `owner` *(optional)*, `platform` *(optional)*, `environment` *(optional)* | Workspaces BU facet + per-BU cost attribution; the **Business unit** rows of the platform × environment grid; `platform` / `environment` override what the app reads from the workspace name |
   | `user_team` | `user` *(matches `query.history.executed_by`)*, `team` | Adoption Team column + Activity-by-team |

2. **Grant the app's service principal `SELECT`** on the two tables (plus `USE
   CATALOG`/`USE SCHEMA` on their catalog/schema) — the `mapping_tables_setup.sql`
   file includes the grant; just set the principal. Without it the app reads
   nothing and silently shows no BU/team (it fails soft — no error, no crash).
   **Grant the `estate-command-center-gold-sync` job's service principal too** — in live mode the
   team join runs when that (required) job precomputes the adoption payload (see the
   note below).
3. **Point the app at them**: add them to `APP_SETTINGS` in your deploy cell from Step ③:
   ```python
   APP_SETTINGS = {"FINOPS_WORKSPACE_BU_TABLE": "main.finops_ref.workspace_bu",
                   "FINOPS_USER_TEAM_TABLE": "main.finops_ref.user_team"}
   ```
4. **Redeploy**: run the deploy cell again.

> [!NOTE]
> **When the values appear:** the BU facet and Team column switch on **immediately**
> after the redeploy. BU, platform and environment on the Workspaces, Jobs and Pipelines
> tabs are applied when the page is served, so edits to `workspace_bu` show up within
> about 10 minutes. Team values on the Adoption tab are joined when `estate-command-center-gold-sync`
> precomputes that page, so they appear **after the next run** (run the job manually
> once to see them straight away).

> [!TIP]
> **Already have `workspace_bu` from an older version?** Add the two optional columns with
> `ALTER TABLE <your table> ADD COLUMNS (platform STRING, environment STRING);` and fill
> them in only for workspaces whose names don't say which environment they are. How the
> app reads names is explained in the README section [Environments and platforms](../README.md#environments-and-platforms--how-workspaces-are-grouped).

> [!NOTE]
> These are read-only lookups — the app never writes to them. Keep them current on your
> side (e.g. a scheduled job, or just edit rows as teams change); unmapped workspaces/users
> fall back gracefully (they simply show no BU/team).

---

## How the data works

Every panel is either a live system-table read or a value the daily job ingested — there
is **no sample data**. Anything unavailable **fails closed**: the tab returns a clear
message with the exact grant to run, never a fabricated number.

| Where the app reads | Powers |
|---|---|
| `system.billing.usage` + `list_prices` | Overview, Workspaces, Cost drivers, Genie $, AI $, Cluster Efficiency |
| `system.query.history` | Query Advisor, Warehouse Monitor, idle % |
| `system.compute.clusters` + `node_timeline` | Cluster Efficiency (interactive-cluster sizing / idle) |
| `system.access.*` (lineage, audit) | Tables health, Adoption & Value |
| `system.information_schema.*` | Access grant graph, Governed tags, Data Classification, Governance |
| `system.data_quality_monitoring.table_results` | Data Quality tab *(only if enabled)* |
| `system.lakeflow.*` | Jobs tab (runs, failures, owners, schedules), Pipelines tab (names, types, updates), Automated % |
| `system.serving.*` *(optional)* | AI $ **Who is calling** |
| The admin-sync job → Delta `<ingest_schema>.finops_admin` | Governance budget/policy/PAT/alert tiles, Access groups + PATs, monitor configs |
| The gold-sync job → Delta `<gold_schema>.finops_gold` | Every estate page, read in sub-second time |
| Your `workspace_bu` / `user_team` tables *(optional, step ⑥)* | Workspaces BU facet + per-BU cost attribution; platform / environment overrides for the grid; Adoption Team column + Activity-by-team |

**Ask Genie** (if enabled) streams answers from a **Genie Space** the deploy creates over
your billing tables — point `GENIE_SPACE_ID` at your own to use an existing one. The
in-region model endpoint (`llm_endpoint` / `FINOPS_LLM_ENDPOINT`, gated by the
`ai_narration` flag) powers **both** the optional recommendation-rationale narration **and**
the Query Advisor **EXPLAIN-validated rewrite** (it proposes a runnable `SELECT`, then
validates it with `EXPLAIN` against your warehouse and rejects any that don't bind). Keep it
an in-region endpoint for data residency; with `ai_narration` off, both fall back to
deterministic text.

---

## Upgrades

Download the repo ZIP again (Step ①), import it in place of the old one, and run your deploy
notebook again with the same values. Your settings live in the cell, so an upgrade never
disturbs them. Keep the same `TARGET` you originally deployed with. The cell updates the app
and both jobs, runs gold-sync once and turns its nightly schedule back on.

**Moving from a Git-folder deploy:** copy the values you changed in your Git folder's
`app.yaml` into the cell (`WAREHOUSE_ID`, `GOLD_SCHEMA`, and anything else into `APP_SETTINGS`),
or point `REPO_PATH` at the Git folder itself, and keep the same target. The deploy updates the
existing app and jobs in place.

**Upgrading from a release before October 2026** (check: no **Pipelines** tab):

1. Download the new ZIP and import it (Step ①). The built frontend is included, so there is
   nothing to build.
2. **Run the new grants** (step ④) in a `%sql` cell: the Data Quality grant is now on the
   **table** (`GRANT SELECT ON TABLE system.data_quality_monitoring.table_results …`). The old
   schema-level version fails by design. Optionally, also grant `system.serving` for the
   AI $ **Who is calling** section.
3. Run the deploy cell with your existing target and `RUN_GOLD_SYNC = True`. This updates the
   app **and both jobs** (the jobs run the Python files the deploy uploads, so they pick up the
   new code too), then runs gold-sync once, after the grants, and turns its nightly schedule
   back on. A deploy always resets both schedules to paused, so if you use admin-sync, rerun its
   snippet from step ⑤. Snapshots written by the older version are detected and recomputed on
   first use, so nothing breaks in the meantime.
4. Optional new settings (all blank by default, all safe to leave blank; set them in
   `APP_SETTINGS` in the deploy cell):

   | Setting | What it does |
   |---|---|
   | `FINOPS_ENV_TIERS` | How workspace names map to environments. The built-in words already cover Dev (`dev`, `develop`, `development`, `dv`), Test (`tst`, `test`, `testing`, `qa`, `uat`, `sit`, `fat`, `sat`), Pre-prod (`ppd`, `preprod`, `stg`, `stage`, `staging`, `perf`, `prf`), Sandbox (`sbx`, `sandbox`, `lab`, `poc`, `playground`), Non-prod (`nonprod`, `nonprd`, `npd`) and Prod (`prd`, `prod`, `production`, `live`), in any position and with numbered variants such as `DE2` or `TS2`. |
   | `FINOPS_WORKSPACE_GROUPS` | Roll workspaces up into broader platforms by name prefix, e.g. `"DATA-EDP-:EDP Core;FORE-:Forecasting"`. |
   | `FINOPS_PLATFORM_STRIP_WORDS` | Technology / region words dropped from the automatic platform name. Default `adb,dbw,databricks,aue,ase,aueast,ausoutheast,ause`; `"-"` keeps every word. |
   | `FINOPS_ENV_NONPROD_PATTERN` | An extra regex for names that are non-production but carry no environment word. |
   | `FINOPS_EFFECTIVE_DISCOUNT` | Your blended discount (e.g. `"0.30"`), so figures show effective cost as well as list. |
   | `FINOPS_WORKLOAD_RULES` | Steer job and pipeline names into Batch / Streaming / Interactive / ML-AI. |

   Two more are read if present but aren't in `app.yaml` by default: `FINOPS_PLATFORM_PATTERN`
   (a regex with a `(?P<platform>…)` group, for naming conventions the automatic rule can't
   handle) and `FINOPS_TABLES_LIMIT` (how many tables the Tables tab reads, default 25,000).

> [!IMPORTANT]
> **After an upgrade, re-run `estate-command-center-gold-sync` once.** The Overview, Jobs, Tables and
> Governance tabs serve the **nightly precomputed gold snapshot**, not a live scan — so a
> new release that changes how those tabs compute their numbers only takes effect once the
> gold job rebuilds with the new code. Redeploying the app alone updates the code but keeps
> serving the *previous* snapshot until the next scheduled run. The deploy cell does this when `RUN_GOLD_SYNC = True`.
> If you deployed with it off, run the job once from a notebook so every tab reflects the new
> version: `from databricks.sdk import WorkspaceClient; w = WorkspaceClient(); w.jobs.run_now(job_id=next(w.jobs.list(name="estate-command-center-gold-sync")).job_id)`. (The live-scan tabs — Warehouses, Clusters, Query Advisor, Genie $, AI $ — pick
> up the new code on the app restart alone; only bump `FINOPS_CACHE_VERSION` if you want to
> force-drop their cached payloads immediately instead of waiting out the 1-hour TTL.)

> [!NOTE]
> **Acknowledge / ignore** (dismissing an alert so it stops surfacing every refresh) is
> **on by default**. On the recommended **`-t customer` (Lakebase)** target its state lives
> in the bound Postgres — **nothing to set**. On the **`-t customer-delta`** fallback it
> instead stores a small `finops_ack_status` Delta table, so live mode there needs a
> writeback schema (`FINOPS_GOLD_SCHEMA` **or** `FINOPS_WRITEBACK_SCHEMA`) plus
> `GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO \`<app-service-principal>\``. Either
> way, if the state can't persist the app reports writeback off and hides the controls
> (never a silent loss).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| A tab shows a 503 / "read failed" | The warehouse is stopped, or a grant is missing — the message names the exact object. Re-run the grant from step ④. |
| App shows demo data + a "finish setup to go live" banner | Live mode is on but the warehouse and/or gold schema aren't set yet, so the app fell back to demo (it never fails to start). Set `WAREHOUSE_ID` + `GOLD_SCHEMA` in the deploy cell (step ②), grant read access (step ④), and run the cell again with `RUN_GOLD_SYNC = True`. For a read-only eval with no writable schema, set `FINOPS_ALLOW_LIVE_NO_GOLD: "true"` (slow; not for production). |
| Live mode on + configured, but pages are empty | Confirm `DATABRICKS_WAREHOUSE_ID` **and** `FINOPS_GOLD_SCHEMA` are set, that you re-ran the deploy cell, and that you **ran `estate-command-center-gold-sync` once** (step ⑤) — live pages serve from gold, which is empty until the first run. |
| Data stopped refreshing after an upgrade or redeploy (figures stuck on an earlier day) | A redeploy resets both job schedules to **paused** (that's how `databricks.yml` ships them). Run the deploy cell with `RUN_GOLD_SYNC = True`, which turns gold-sync's schedule back on, and rerun the admin-sync snippet from step ⑤ if you use it. |
| Governance / Access group panels show "run the daily job" | Turn on **estate-command-center-admin-sync** with the snippet in step ⑤ and let it run once. |
| **Acknowledge / Reopen buttons do nothing** (or the ack controls disappeared) after you **deleted + recreated** the app | A recreated app gets a **new service principal**, which cannot use the previous SP's writeback schema (Delta: "permission denied"; Lakebase: "permission denied for schema …"). The app now reports writeback **off** with the reason rather than failing silently. **Fix:** for the Lakebase backend set a **fresh** `FINOPS_LAKEBASE_SCHEMA` (e.g. `estate2`) so the new SP creates + owns its own schema, then redeploy; for the Delta backend re-run the `GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO \`<new-app-service-principal>\`` from step ④ with the new SP. |
| Pages slow | Confirm the `estate-command-center-gold-sync` job actually ran **and its output shows `ok`** for each key (step ⑤) — `skipped (kept previous snapshot)` names the failing read; the app serves from the precomputed gold tables, so a page is only slow if gold is missing/stale or you enabled the `FINOPS_ALLOW_LIVE_NO_GOLD` eval escape hatch. Use a serverless SQL warehouse (Small or larger) for the app. The first load after a redeploy can take longer while the cache warms; after that tab switches and filters are served from memory. |
| `GRANT … ON SCHEMA system.data_quality_monitoring` fails with **"User does not have MANAGE on Schema"** — even for an account + metastore admin | Expected: Databricks owns that schema and only gives admins MANAGE on the **table**. Run `GRANT SELECT ON TABLE system.data_quality_monitoring.table_results TO \`<app-sp>\`` instead (SQL, or the table's Permissions dialog — the schema's Permissions button is greyed out). |
| Jobs tab: "**Inline byte limit exceeded**" (often shown with a "needs read access" hint in older versions) | Not a permissions problem — the result was over the 25 MiB inline limit on a large estate. Upgrade (see Upgrades): current versions fall back to `EXTERNAL_LINKS`, then to sharded reads, and only mention grants for a real permission error. Then re-run `estate-command-center-gold-sync` so the nightly snapshot is rebuilt. |
| A tab keeps showing an old error after you fixed a grant | Older versions cached the error (in memory for an hour, and in the nightly snapshot until the next run). Current versions retry within a minute and never snapshot an error; on an older version, re-run `estate-command-center-gold-sync` and restart the app. |
| AI $ doesn't list some people who use AI | Genie (Spaces and Genie Code) is on the **Genie $** tab, and the Databricks Assistant isn't metered. For model-serving / Foundation Model API callers, grant `system.serving` (step ④) so **Who is calling** can read `endpoint_usage`. The window is month to date. |
| **Tables** shows far fewer tables than you have | Two causes. (1) `system.information_schema.tables` only lists tables the app's service principal can see — grant `BROWSE` per catalog (see "widen the Access page" in step ④; BROWSE shows metadata, not data). (2) Older versions read at most 200 tables; current versions read up to 25,000 (`FINOPS_TABLES_LIMIT`) and show the top 500 with **Load all**. Re-run `estate-command-center-gold-sync` after upgrading. Sizes come from the nightly census, which only covers tables the job's SP can `DESCRIBE DETAIL`. |
| The **platform × environment** grid is missing, shows an explanation, or puts workspaces in the wrong environment | The app reads platform and environment from the workspace name unless told otherwise. Add `platform` and `environment` columns to your workspace mapping table (see [`reference/mapping_tables_setup.sql`](../reference/mapping_tables_setup.sql)) for workspaces whose names don't follow the convention, or set `FINOPS_WORKSPACE_GROUPS` / `FINOPS_ENV_TIERS` in `APP_SETTINGS` in the deploy cell. Mapping-table values always win. |
| Can't find the cost of a pipeline / materialized view | Use the **Pipelines** tab — MV/ST refreshes are billed with a pipeline id (often under the SQL product), so they're not on Jobs or Query Advisor. |
| No **BU** facet / no **Team** column | Those are optional (step ⑥). Create the mapping tables from [`reference/mapping_tables_setup.sql`](../reference/mapping_tables_setup.sql), set `workspace_bu_table` / `user_team_table`, grant the app SP `SELECT`, and redeploy. |
| "Workspace has reached the maximum limit of 300 apps" | The target workspace is full — deploy to another workspace. System-table billing is account-wide, so any workspace in the account sees the same usage. |

---

<div align="center">

*Full configuration surface → [DEPLOYMENT.md](DEPLOYMENT.md) · architecture → [ARCHITECTURE.md](ARCHITECTURE.md)*

</div>
