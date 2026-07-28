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
> **The 60-second version** — everything else on this page is just detail:
> ```sh
> %sh
> cd /Workspace/Users/<you>/databricks-estate-command-center   # the Git folder from Step ①
> databricks bundle deploy -t customer              # creates the app + jobs
> databricks bundle run   estate -t customer        # starts it, prints the URL
> ```
> Run that in a notebook cell and the app is live **in demo mode** — no config, no
> grants, nothing to build. Then edit **three values** in `app.yaml` (Step ②) and
> **grant read access** (Step ④) to point it at your real data. That's it.

> [!NOTE]
> **The whole thing is six steps:** ① add the code → ② set three values → ③ run one
> deploy cell → ④ grant read access → ⑤ turn on the daily jobs → ⑥ *(optional)* light up
> BU/team. Steps ① – ③ stand the app up (it runs immediately in safe demo mode); ④ – ⑤
> connect it to your data.

---

## What gets created in your workspace

| | Object | What it is |
|---|---|---|
| 📊 | **estate-command-center** | The app itself — a small always-on container (~0.5 DBU/hr at `compute_size` MEDIUM, ≈ $270/mo; stop it to pay nothing). A shared multi-region demo workspace can over-report this ~3×. |
| ⚡ | **1 SQL warehouse** *(you provide — not created by the deploy)* | A serverless SQL warehouse you already have powers the reads; supply its id as `DATABRICKS_WAREHOUSE_ID`. Scales to zero when idle. |
| 🗄️ | **Gold Delta tables** under `<gold_schema>` (`finops_gold`, `finops_gold_table_sizes`, `finops_gold_warehouse_daily`, `finops_gold_cluster_daily`) | Nightly-precomputed estate payloads (every tab) + a per-table size census + daily-grain warehouse/cluster history, so estate pages load in ~1s instead of re-scanning account-wide system tables per request. **Required in live mode** — populated by the gold-sync job below. |
| 🔁 | **Two scheduled jobs** (`finops_gold_sync` nightly + `estate-command-center-admin-sync` daily) | Gold-sync precomputes the estate payloads (**required for live mode**); admin-sync fills the governance & access panels that don't live in system tables (budgets, policies, tokens, groups). **Both ship paused** — unpause gold-sync for live (admin-sync is optional). |

> [!IMPORTANT]
> The app runs as its own **service principal** — a machine identity Databricks creates
> when you deploy it. You grant *that* identity read access to your system tables in
> step ④. Nothing else needs credentials.

---

## Step ① — Add the code to your workspace

Top-right of the workspace, click **Create ▾ → Git folder**. In **Git repository URL**
paste:

```
https://github.com/beeyal/databricks-estate-command-center
```

Click **Create**. The code lands at `/Workspace/Users/<you>/databricks-estate-command-center`
(where `<you>` is your own email). That's your copy — no download, no build.

> [!NOTE]
> The repo is private. If you can't see it, request access first.

---

## Step ② — Set your configuration

**There is one file to edit: `app.yaml`.** (No `config.yaml`, no `customise.yaml` — just
this one.) Open it in the Git folder. It's a list of `- name: … / value: …` pairs, each
with a comment explaining what it does and what happens if you leave it blank.

> [!TIP]
> **Want to see it first?** Do nothing here and jump to Step ③ — deploying as-is runs the
> app in **demo mode** (realistic synthetic data, no warehouse, no grants). Come back when
> you're ready to point it at your real estate.

### To go live, set these **3** values (everything else can stay as-is):

```yaml
  - name: USE_LIVE
    value: "true"                              # read your real system tables

  - name: DATABRICKS_WAREHOUSE_ID
    value: "<serverless-sql-warehouse-id>"     # a serverless SQL warehouse in THIS workspace

  - name: FINOPS_GOLD_SCHEMA
    value: "main.finops_gold"                  # catalog.schema the nightly job precomputes into
```

That's the whole required configuration. Deploy (Step ③), grant the app read access
(Step ④), and run the gold job once (Step ⑤).

**What each of the three does:**

| Value | What it is | Leave blank? |
|---|---|---|
| `USE_LIVE` | `"true"` = real data, `"false"` = demo. | Leave `"false"` for demo. |
| `DATABRICKS_WAREHOUSE_ID` | The serverless SQL warehouse the app queries with. Grant its service principal **CAN USE** (Step ④). | Required for live; blank in demo. |
| `FINOPS_GOLD_SCHEMA` | Where the nightly `finops_gold_sync` job stores its pre-aggregated tables. The app reads these in ~1 s instead of scanning account-wide system tables on every page. **The job creates the schema + tables for you** — you just need `CREATE` on the catalog. | Required for live; ignored in demo. |

> [!IMPORTANT]
> **Why gold is required for live.** The app reads `system.billing.usage`,
> `system.query.history`, `system.lakeflow.*` — account-wide tables with tens of millions
> of rows. Scanning those on every page load isn't viable, so a nightly job pre-aggregates
> them once into the gold schema and the app reads that. The app **won't start in live mode
> without `FINOPS_GOLD_SCHEMA` set**. (Read-only eval with no writable schema? Set
> `FINOPS_ALLOW_LIVE_NO_GOLD: "true"` to read straight off system tables — slow, not for
> production.) After going live, run `finops_gold_sync` **once** (Step ⑤) or the estate
> pages stay empty until the first nightly run.

### Everything else is optional — leave it blank unless you want it

Every other value in `app.yaml` has a safe default and its comment says what blank does.
The common ones:

- **`FINOPS_CUSTOMER_NAME`** — the label in the app header. Change to your company (cosmetic).
- **Feature toggles** (`FINOPS_FEATURE_GENIE` / `_AI_NARRATION` / `_DQM` / `_WRITEBACK`) —
  all ship **on**. Set any to `"false"` to hide that surface (its API also goes inert — no
  half-rendered tabs, no model calls).
- **Acknowledge/dismiss storage** — on by default. **You don't pick a storage type here —
  the deploy target does** (see Step ③): the default `customer` target stores it in a tiny
  Delta table in your gold schema (nothing to set — leave `FINOPS_WRITEBACK_SCHEMA` blank to
  reuse the gold schema); the `customer-lakebase` target uses managed Postgres and wires
  itself up. If the app can't write the state it reports writeback **off** and hides the
  controls — it never silently drops a dismissal.
- **Business-unit / team reporting** (`FINOPS_WORKSPACE_BU_TABLE`, `FINOPS_USER_TEAM_TABLE`)
  — blank hides those columns (Step ⑥ if you want them).

> [!WARNING]
> `app.yaml`'s `env:` is a YAML **list**. Edit only the text inside the quotes on a
> `value:` line — don't change the `- name:` keys, the `- `, or the indentation, and keep
> every value quoted. If the app won't start after an edit, run `git checkout app.yaml` to
> reset and re-edit just the value.

---

## Step ③ — Deploy it from a notebook

Create a notebook (or open the workspace **web terminal**) and run one `%sh` cell —
replace `<you>` with your email:

```sh
%sh
cd /Workspace/Users/<you>/databricks-estate-command-center
databricks bundle deploy -t customer
databricks bundle run   estate -t customer
```

This creates the app and the two scheduled jobs, then prints the app's
URL. You don't sign in or paste a token — the notebook runs **as you**, so the deploy
authenticates automatically. Open the printed URL and the app is live.

> [!NOTE]
> **Which target — `customer` or `customer-delta`?**
> **Use `customer` (the recommended default).** The only thing the deploy target decides is
> **where the acknowledge/dismiss state is stored** — the app itself is identical either way:
>
> | Target | Acknowledge/dismiss state stored in | When to pick it |
> |---|---|---|
> | **`customer`** *(recommended)* | A small managed **Lakebase (Postgres)** instance the bundle creates + wires for you. Snappy at any scale; fully self-provisioned (nothing to set). | **The default. Use this** unless you specifically don't want a managed-Postgres instance. Adds one small always-on instance (~tens of $/mo). |
> | **`customer-delta`** | A tiny **Delta** table via your warehouse. Zero extra infrastructure, zero extra cost. | The fallback for **"don't use Lakebase."** In live mode it needs a writeback schema + one grant (below). |
>
> **On `customer` (Lakebase) you set nothing for writeback** — the bundle creates the
> instance, binds it, and auto-provisions the app's Postgres role. (If you ever delete +
> recreate the app, set a fresh `FINOPS_LAKEBASE_SCHEMA` so the new app owns its own schema.)
>
> **On `customer-delta`** writeback uses a `finops_ack_status` Delta table in your writeback
> schema — leave `FINOPS_WRITEBACK_SCHEMA` blank to reuse `FINOPS_GOLD_SCHEMA` (the app
> auto-creates the table; the Step ④ grant covers it). If it can't write, the app reports
> writeback off and hides the controls — never a silent loss.
>
> Both are a single `bundle deploy`. To switch targets later, run
> `databricks bundle deployment bind estate estate-command-center -t <target>` once first so
> the bundle adopts the existing app instead of erroring that it already exists.

---

## Step ④ — Grant the app read access to your data

**Find the app's service principal first.** Click the **app switcher** (top-left, next
to the Databricks logo) → **Databricks Apps** → click **estate-command-center** in the
Name column. On the app's **Overview** / **Authorization** tab you'll see its **service
principal** — a client ID like `a1b2c3d4-…`. Copy it.

There are two things to grant. One is set in the workspace UI (it is **not** a SQL
statement), and the rest are SQL.

**a. Let the app use the warehouse (UI, not SQL).** A SQL warehouse cannot be granted in
the SQL editor. In the left nav open **SQL Warehouses**, click your warehouse, click the
**Permissions** button (top-right), add the app's service principal, and give it **Can
use**. If the SP still can't query, an admin should also tick the **Databricks SQL
access** entitlement for it in workspace admin settings.

**b. Grant read access to the system tables (SQL).** Open **SQL Editor** (left nav), paste
the block below, replace `<app-sp>` with the service principal client ID, and click
**Run**:

```sql
GRANT USE CATALOG ON CATALOG system                          TO `<app-sp>`;  -- see the system catalog
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing            TO `<app-sp>`;  -- Overview, Genie $, AI $, Cost drivers
GRANT USE SCHEMA, SELECT ON SCHEMA system.query              TO `<app-sp>`;  -- Query Advisor, Warehouse Monitor, idle %
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute            TO `<app-sp>`;  -- Cluster Efficiency (clusters + node_timeline)
GRANT USE SCHEMA, SELECT ON SCHEMA system.access             TO `<app-sp>`;  -- Tables, Adoption
GRANT USE SCHEMA, SELECT ON SCHEMA system.information_schema TO `<app-sp>`;  -- Access, Governance
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow           TO `<app-sp>`;  -- Automated %, jobs
-- + USE CATALOG / USE SCHEMA on any user catalog you want the Tables page to inspect in detail.
```
> Reading a Unity Catalog table needs **both** `USE SCHEMA` **and** `SELECT` (plus
> `USE CATALOG` on `system`) — `SELECT` alone gives `INSUFFICIENT_PERMISSIONS: … does
> not have USE SCHEMA`. The combined grants above cover it.

> [!NOTE]
> **Access page scope (optional, whole-estate coverage).** The Access page's grant
> graph reads `system.information_schema.table_privileges`, which Unity Catalog
> **row-filters to only the securables the app's service principal can see**. With
> just the `system` grants above, the Access tab shows the grants on catalogs the
> app SP already has visibility into — **not necessarily your whole estate**. To
> make it cover everything, give the app SP broad *visibility* (not data access):
> `GRANT BROWSE ON CATALOG <catalog> TO \`<app-sp>\`;` per catalog you want covered
> (a metastore admin can do this estate-wide). `BROWSE` is least-privilege — it
> makes objects **visible** in `information_schema` without granting `SELECT` on the
> data. This is **optional**: every other tab works from `system.*` alone, and the
> Access page itself notes when its graph is scoped. Decide per your security
> posture — widening the app SP's reach across all catalogs is a real trade-off.
>
> **Generate the BROWSE grants for every catalog automatically** — rather than
> hand-writing one per catalog, run this as a metastore admin: it *produces* the
> GRANT statements from your live catalog list, which you then review and run.
> ```sql
> -- 1) Generate: lists a GRANT BROWSE for every catalog except the system ones.
> SELECT 'GRANT BROWSE ON CATALOG `' || catalog_name ||
>        '` TO `<app-service-principal>`;' AS grant_stmt
> FROM system.information_schema.catalogs
> WHERE catalog_name NOT IN ('system','__databricks_internal','samples')
> ORDER BY catalog_name;
> -- 2) Copy the grant_stmt column from the results and run it (review first).
> ```
> Replace `<app-service-principal>` with the app's SP (its client-id or name). Re-run
> whenever you add catalogs. (Databricks SQL has no native loop, so this
> generate-then-run pattern is the idiomatic way to grant across many catalogs.)

> [!NOTE]
> **Only if you use the Data Quality tab** (`FINOPS_FEATURE_DQM: "true"`), also run
> `GRANT USE SCHEMA, SELECT ON SCHEMA system.data_quality_monitoring TO \`<app-sp>\`;`.
> That schema **only exists once someone has enabled anomaly detection on a schema** — if
> you don't use Data Quality Monitoring it won't exist and the grant errors with
> `SCHEMA_DOES_NOT_EXIST`. In that case just leave the tab off with
> `FINOPS_FEATURE_DQM: "false"` (step ②) and skip this grant entirely.

> [!NOTE]
> **Only if Ask Genie is on** (`FINOPS_FEATURE_GENIE: "true"`) **and you point
> `GENIE_SPACE_ID` at your own Genie Space** — grant the app's service principal
> **`CAN RUN`** on that Space (Genie Space → Share → add the app SP as *Can Run*).
> Without it the Ask Genie banner can't start conversations. If you leave
> `GENIE_SPACE_ID` blank the deploy auto-creates a Space owned by the app SP and no
> grant is needed.

> [!NOTE]
> **Only if writeback stays on in live mode with the default Delta backend** — the app
> persists its acknowledge/ignore state in a `finops_ack_status` table in your writeback
> schema, so grant the **app's own** service principal `CREATE`/`MODIFY` on that schema:
> `GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO \`<app-sp>\`;` (the writeback
> schema is `FINOPS_GOLD_SCHEMA` or `FINOPS_WRITEBACK_SCHEMA` from step ②). Skip this on
> `-t customer-lakebase` (state lives in the bound Postgres) or if you set
> `FINOPS_FEATURE_WRITEBACK: "false"`.

> [!IMPORTANT]
> All three need an **account / metastore + workspace admin** — the app's own SP is
> read-only and can't grant itself anything. If a grant is ever missing, the affected tab
> shows a clear message naming the exact statement to run, never a made-up number.

---

## Step ⑤ — Turn on the scheduled jobs

In **Workflows**, unpause the jobs (both ship paused):

- **`finops_gold_sync`** (nightly) — **required for live mode.** Precomputes every estate
  payload (all tabs, including jobs, recommendations and governance) + a per-table size
  census into several tables under `<gold_schema>` — `finops_gold`, `finops_gold_table_sizes`,
  `finops_gold_warehouse_daily` and `finops_gold_cluster_daily` (the last two are daily-grain
  history that power the Warehouse Monitor / Cluster Efficiency time filters beyond system-table
  retention) — so the app serves each page in ~1s instead of re-scanning account-wide system
  tables per request. **Run it once manually right after unpausing** so the app has data to
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
  Delta table). Use **Run as** on the job, or set `FINOPS_JOB_RUN_AS`.

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
# edit app.yaml as in Step ②
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
   | `workspace_bu` | `workspace_id`, `bu`, `owner` *(optional)* | Workspaces BU facet + per-BU cost attribution |
   | `user_team` | `user` *(matches `query.history.executed_by`)*, `team` | Adoption Team column + Activity-by-team |

2. **Grant the app's service principal `SELECT`** on the two tables (plus `USE
   CATALOG`/`USE SCHEMA` on their catalog/schema) — the `mapping_tables_setup.sql`
   file includes the grant; just set the principal. Without it the app reads
   nothing and silently shows no BU/team (it fails soft — no error, no crash).
   **Grant the `finops_gold_sync` job's service principal too** — in live mode the
   BU/team join runs when that (required) job precomputes the workspaces/adoption
   payloads (see the note below).
3. **Point the app at them** — in the **same `app.yaml`** you edited in Step ②, fill in the
   `value:` for these two entries (they're already there, blank):
   ```yaml
     - name: FINOPS_WORKSPACE_BU_TABLE
       value: "main.finops_ref.workspace_bu"
     - name: FINOPS_USER_TEAM_TABLE
       value: "main.finops_ref.user_team"
   ```
4. **Redeploy** — re-run the same deploy cell from Step ③.

> [!NOTE]
> **When the BU/team values appear:** the `bu_enabled`/`team_enabled` toggles (which
> reveal the facet + Team column) flip on **immediately** after the redeploy. The
> actual BU/team *values* on the Workspaces and Adoption pages appear as soon as
> the payload that carries them is (re)built: **after the next `finops_gold_sync`
> run** (the join happens at precompute time — run the job manually once to see it
> right away rather than waiting for the nightly schedule).

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
| `system.data_quality_monitoring` | Data Quality tab *(only if enabled)* |
| `system.lakeflow.*` | Automated %, top jobs |
| The admin-sync job → Delta `<ingest_schema>.finops_admin` | Governance budget/policy/PAT/alert tiles, Access groups + PATs, monitor configs |
| The gold-sync job → Delta `<gold_schema>.finops_gold` | Every estate page, read in sub-second time |
| Your `workspace_bu` / `user_team` tables *(optional, step ⑥)* | Workspaces BU facet + per-BU cost attribution; Adoption Team column + Activity-by-team |

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

Pull the latest code into the Git folder and re-run the deploy cell
(`databricks bundle deploy -t customer` then `databricks bundle run estate -t customer`).
Your settings live in `app.yaml`, outside the code, so an upgrade never disturbs them.
Use the same target you originally deployed (`-t customer` or `-t customer-lakebase`).

> [!IMPORTANT]
> **After an upgrade, re-run `finops_gold_sync` once.** The Overview, Jobs, Tables and
> Governance tabs serve the **nightly precomputed gold snapshot**, not a live scan — so a
> new release that changes how those tabs compute their numbers only takes effect once the
> gold job rebuilds with the new code. Redeploying the app alone updates the code but keeps
> serving the *previous* snapshot until the next scheduled run. Trigger `finops_gold_sync`
> manually after upgrading (or wait for the nightly run) so every tab reflects the new
> version. (The live-scan tabs — Warehouses, Clusters, Query Advisor, Genie $, AI $ — pick
> up the new code on the app restart alone; only bump `FINOPS_CACHE_VERSION` if you want to
> force-drop their cached payloads immediately instead of waiting out the 1-hour TTL.)

> [!NOTE]
> **Acknowledge / ignore** (dismissing an alert so it stops surfacing every refresh) is
> **on by default**. On the default (Delta) backend it stores state in a small
> `finops_ack_status` table in a **writeback schema**, so live mode needs a writeback
> schema set (`FINOPS_GOLD_SCHEMA` **or** `FINOPS_WRITEBACK_SCHEMA`) plus
> `GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO \`<app-service-principal>\`` — if
> neither is set the app reports writeback off and hides the controls (no silent failure).
> The optional `customer-lakebase` target (see Step ③) swaps that for a managed-Postgres
> backend for lower latency at scale and needs none of the schema setup; everything else is
> identical.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| A tab shows a 503 / "read failed" | The warehouse is stopped, or a grant is missing — the message names the exact object. Re-run the grant from step ④. |
| App won't start in live mode / logs say "LIVE MODE REQUIRES A GOLD SCHEMA" | Set `FINOPS_GOLD_SCHEMA` in `app.yaml` (step ②) and redeploy — it's required in live mode. For a read-only eval with no writable schema, set `FINOPS_ALLOW_LIVE_NO_GOLD: "true"` instead (slow; not for production). |
| Data still looks empty after `USE_LIVE: "true"` | Confirm `DATABRICKS_WAREHOUSE_ID` **and** `FINOPS_GOLD_SCHEMA` are set in `app.yaml`, that you re-ran the deploy cell, and that you **ran `finops_gold_sync` once** (step ⑤) — live pages serve from gold, which is empty until the first run. |
| Governance / Access group panels show "run the daily job" | Unpause **estate-command-center-admin-sync** (step ⑤) and let it run once. |
| **Acknowledge / Reopen buttons do nothing** (or the ack controls disappeared) after you **deleted + recreated** the app | A recreated app gets a **new service principal**, which cannot use the previous SP's writeback schema (Delta: "permission denied"; Lakebase: "permission denied for schema …"). The app now reports writeback **off** with the reason rather than failing silently. **Fix:** for the Lakebase backend set a **fresh** `FINOPS_LAKEBASE_SCHEMA` (e.g. `estate2`) so the new SP creates + owns its own schema, then redeploy; for the Delta backend re-run the `GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO \`<new-app-service-principal>\`` from step ④ with the new SP. |
| Pages slow | Confirm the `finops_gold_sync` job actually ran (step ⑤); the app serves from the precomputed gold tables, so a page is only slow if gold is missing/stale or you enabled the `FINOPS_ALLOW_LIVE_NO_GOLD` eval escape hatch. |
| No **BU** facet / no **Team** column | Those are optional (step ⑥). Create the mapping tables from [`reference/mapping_tables_setup.sql`](../reference/mapping_tables_setup.sql), set `workspace_bu_table` / `user_team_table`, grant the app SP `SELECT`, and redeploy. |
| "Workspace has reached the maximum limit of 300 apps" | The target workspace is full — deploy to another workspace. System-table billing is account-wide, so any workspace in the account sees the same usage. |

---

<div align="center">

*Full configuration surface → [DEPLOYMENT.md](DEPLOYMENT.md) · architecture → [ARCHITECTURE.md](ARCHITECTURE.md)*

</div>
