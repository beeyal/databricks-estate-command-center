<div align="center">

# Estate Command Center

*Databricks-native cost, governance & data-quality console — one stop shop for cost, usage, access & governance across your estate.*

[![Databricks App](https://img.shields.io/badge/Databricks-App-FF3621?style=for-the-badge&logo=databricks&logoColor=white)](/)
[![Stack](https://img.shields.io/badge/Stack-Databricks%20%7C%20React%20%7C%20FastAPI-1B3139?style=for-the-badge)](/)
[![Foundation Models](https://img.shields.io/badge/Foundation%20Models-Claude%20Sonnet%204.6-8B5CF6?style=for-the-badge&logo=anthropic&logoColor=white)](/)
[![Feature-flagged](https://img.shields.io/badge/Feature--flagged-genie%20%7C%20ai__narration%20%7C%20dqm%20%7C%20writeback-06B6D4?style=for-the-badge)](/)
[![Genie-powered](https://img.shields.io/badge/Genie--powered-Ask%20%26%20cost%20attribution-8B5CF6?style=for-the-badge)](/)
[![Demo-ready](https://img.shields.io/badge/Demo--ready-USE__LIVE%3Dfalse-F59E0B?style=for-the-badge)](/)

[**🏗 Architecture**](docs/ARCHITECTURE.md) ·
[**🔒 Security**](docs/SECURITY.md) ·
[**🚀 Deployment guide**](docs/DEPLOYMENT.md) ·
[**📋 Build spec**](SPEC.md) ·
[**✅ Submission checklist**](SUBMISSION_TODO.md)

</div>

---

> **Disclaimer** — Estate Command Center is a **field-built, open-source example**, **not an official Databricks product** and **not supported, endorsed, or maintained by Databricks**. It is provided **as-is, without warranty**, for you to review and adapt. It is **strictly read-only** over your `system.*` tables (it creates/modifies/deletes nothing in your lakehouse — see [Security](docs/SECURITY.md)). All savings/impact figures are **modeled estimates**; validate any recommendation independently before acting on it.

---

<div align="center">

### 📖 Two guides — pick your path

| 🚀 **[Customer deployment guide →](docs/CUSTOMER_DEPLOYMENT.md)** | 🛠 **[Internal field guide →](docs/FIELD_GUIDE.md)** |
|---|---|
| Deploying **live in a customer's workspace**. A clean, six-step, live-only guide written for a first-time Databricks Apps user. No demo/field jargon. | For the **Databricks field**. The foolproof 3-step **demo deploy**, feature-flag variants, and the field tooling (`deploy.sh`, `customise.yaml`) for showcasing and onboarding. |

</div>

*The README itself is the full product reference — every page, the architecture, the data model, feature flags, and the deploy-detail appendix that both guides draw on.*

---

![Overview page — global KPIs, weekly spend/savings, top opportunities](docs/screenshots/01-overview.png)

<div align="center">

**$642.5K/mo modelled spend** &nbsp;·&nbsp; **$241.6K/mo savings potential (37.6%)** &nbsp;·&nbsp; **12 workspaces, 64 queries, 30 tables, 91 grants, 12 monitored tables** &nbsp;·&nbsp; **24-play operator recommendation catalogue** &nbsp;·&nbsp; **up to 14 pages, 4 deploy-time feature flags, USD/AUD**.

</div>

> **New since first release:** a **Data Quality** tab (Lakehouse Monitoring inventory + cost tie-in), an **Ask Genie** streaming Q&A banner, **Genie cost attribution** (Genie Code vs Genie Spaces), **deploy-time feature flags** with **4 deploy variants**, **dual currency** (USD/AUD), and **cost-by-driver cost-by-driver / spike-detection** views. See the [Deployment guide](docs/DEPLOYMENT.md) for the flag matrix and grants.

---

## Contents

- [What is it?](#what-is-it)
- [Key capabilities — per page](#key-capabilities--per-page)
  - [Overview](#overview) · [Access](#access) · [Workspaces](#workspaces) · [Query Advisor](#query-advisor) · [Warehouse Monitor](#warehouse-monitor) · [Cluster Efficiency](#cluster-efficiency) · [Tables](#tables) · [Governance](#governance) · [AI $](#ai-) · [Adoption & Value](#adoption--value) · [Data Quality](#data-quality) · [Recommendations](#recommendations)
- [Ask Genie](#ask-genie)
- [Genie cost attribution](#genie-cost-attribution)
- [**How the numbers are calculated**](#how-the-numbers-are-calculated)
- [Feature flags & deploy variants](#feature-flags--deploy-variants)
- [Dual currency & cost-by-driver cost views](#dual-currency--alinta-style-cost-views)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Data requirements](#data-requirements)
- [**Deploy & productionalise in a customer workspace**](#deploy--productionalise-in-a-customer-workspace)
  - [Deploy from the workspace (recommended)](#deploy-from-your-databricks-workspace-recommended--no-laptop-no-python) · [Prefer the CLI on your laptop?](#prefer-the-cli-on-your-laptop) · [Feature flags](#reference--feature-flags-deploy-with-or-without-dqm--ai) · [Grants](#reference--grants-live-mode-only) · [Data Quality Monitoring](#data-quality-monitoring--enabling--configuring) · [Productionalising](#productionalising--the-operational-checklist) · [Troubleshooting](#troubleshooting)
- [Local development](#local-development)
- [Customisation](#customisation)
- [Roadmap](#roadmap)
- [Known limitations](#known-limitations)

---

## What is it?

**Estate Command Center** is a full-stack Databricks App that gives a platform operator a single pane of glass over the cost, usage, access, and governance posture of an entire Databricks estate — and turns every finding into a **grounded, priority-ranked recommendation**.

It consolidates the family of cost/usage tools the field has built into one deployable app:

| Tool folded in | What it contributed |
|---|---|
| **Query observability** | The AI-insight query table — statement-level metrics + per-query cost-optimisation rationale (now the **Query Advisor** page). |
| **Workspace health check** | Workspace-by-workspace health / complexity / optimisation scoring with faceted filters (now the **Workspaces** page). |
| **GenAI cost governance** | AI-Gateway / FMAPI budget + rate-limit plays (folded into **Governance** + **Recommendations**). |
| **Cost-by-driver hub** | Cost-by-driver, SKU-level in-region breakdowns, MoM spike detection (folded into **Overview**, **Workspaces**, and a **Recommendations** trigger). |
| **Consumption dashboards** | The spend/DBU trend, per-SKU and per-product attribution rollups behind the **Overview** and **Workspaces** views. |

It ships with a seeded, **Acme-shaped demo dataset** (clearly labelled `DEMO DATA` in the UI) and the **real system-table SQL** wired behind a `USE_LIVE=false` flag, so the same app runs offline for a demo and against live system tables in production without a code change.

---

## Key capabilities — per page

Nav tabs (up to 14): **Overview · Access · Workspaces · Jobs · Query Advisor · Warehouses · Clusters · Tables · Governance · Adoption & Value · Genie $ · AI $ · Data Quality · Recommendations**. **Data Quality** is a *feature-flagged* tab — it only appears when `features.dqm=true`, so a deployment without it shows 13 tabs (see [Feature flags & deploy variants](#feature-flags--deploy-variants)).

A persistent [**Ask Genie**](#ask-genie) banner sits beneath the nav when `features.genie=true`.

### Overview

The estate dashboard. A KPI row — **Total spend/mo**, **Savings potential** (`$` + `% of spend`), **Total DBUs/mo**, **Top cost driver** (largest `billing_origin_product` by spend), **#Need attention** (critical health) — over a weekly **spend-and-captured-savings** trend chart, then the [cost-by-driver cost views](#dual-currency--alinta-style-cost-views): **cost by cost-driver (product)**, **monthly trend — top-5 drivers**, and a **driver-change-vs-prior-30-days (%)** bar for **MoM spike detection** (drivers up ≥ 25% MoM fire a cost-spike recommendation). A **Top savings opportunities** rollup ranks the highest-impact recs cross-page (worst queries, worst external tables, most over-privileged principals) and deep-links into the Recommendations hub. This is the *only* page besides Recommendations that carries advice, and only as a teaser. A **USD/AUD** toggle in the header restates every `$` figure.

### Access

**A factual, bidirectional grants explorer — no AI recommendations.** It answers *"what does each AD group / user / service principal have access to?"* and the exact reverse, off the same grant graph:

- **Data-centric ("who can access this securable?")** — cascade **workspace → catalog → schema → table** and see every grant: principal, privilege (`USE`/`SELECT`/`MODIFY`/`ALL PRIVILEGES`/…), **direct vs inherited**, plus any row filter, column mask or ABAC policy in effect.
- **Principal-centric ("what can this principal access?")** — pick a group / user / SP and see all their entitlements (workspace access, cluster-create, SQL-access) and every object grant they hold, resolved through **(nested) group membership** so *effective* access is correct.

Plus a **principal directory** (source AIM / SCIM / local, status, last-active), an expandable **group-membership tree**, a **PAT/token inventory** with age, and **risk flags** (over-privileged prod, direct-to-user grants, broad `account users`/`PUBLIC`, admin sprawl, stale grants, external-location write). Demo set: **20 principals · 91 grants · 26 risk flags**.

![Access — bidirectional grants explorer](docs/screenshots/07-access.png)

### Workspaces

Workspace-by-workspace analysis mirroring the workspace health check. A sortable, faceted table — **Workspace · Workspace ID · Spend/mo · Savings/mo · Savings % · Automated % · Health · Complexity · trend sparkline** — with facets on complexity, health, search, and customer/BU (the customer/BU facet appears only when a `workspace_bu_table` is configured).

- **Health rule:** Good ≥ 60% automated, Warning 25–60%, Critical < 25% (automated = scheduled-jobs compute vs interactive/all-purpose). The per-workspace **savings/mo** formula is in [**How the numbers are calculated**](#workspaces--savings--health).
- **Drill-down** per workspace: product/SKU mix (serverless · DLT-Lakeflow · jobs-classic · all-purpose · SQL warehouse · model serving · apps · FMAPI · PO) with % share; monthly trend; top users / jobs / warehouses by `$`; DBU total; idle %; serverless share; region; #clusters/#warehouses; and the **by-SKU** sub-view with `_AUSTRALIA_EAST` SKUs and `pct_of_total`.

![Workspaces — health / complexity / optimisation table + drill-down](docs/screenshots/08-workspaces.png)

### Jobs

**A facts-only, per-job cost & reliability view — no serverless-migration advice.** It answers *"which jobs cost the most, which are failing, and which are getting slower or more expensive?"* straight from `system.lakeflow.jobs` + run timelines and `system.billing.usage`. Per job it surfaces: **cost/mo**, **failure rate** + **rising-failure** trend, **run-time spikes**, **cost growth** (month-over-month cost change), **setup + queue share** of run time, **failed-run `$/mo`** (an estimate — the compute burned by runs that failed), and **cost/run**.

- **Deliberately fact-only:** the Jobs tab does **not** flag serverless-migration candidates and does **not** estimate serverless savings. This is intentional — serverless jobs DBUs list at ~2× classic and net cost often *rises*, so a credible serverless saving needs an A/B run, not a static estimate. The page reports the facts an operator needs to decide, and leaves the migration call to them.

### Query Advisor

The query observability — query-level *observability* that feeds the Recommendations hub. KPI row (**Runs · Critical · Compute-h · Est. Cost · Biggest Spill · Applied**) over the AI-insight table:

- Columns: **# / Impact (score bar) / Query (truncated, expandable) / AI INSIGHT (pill: `cluster` | `resize` | `rewrite` | `vacuum` + one-line rationale) / SRC / Warehouse / User / Runs / p95 / Cost / Flags / Actions**. Expand a row for full SQL, full insight, estimated savings and next steps.
- Filters: time range (`1h`/`6h`/`24h`/`7d`/custom — scales runs & cost in the KPI row), warehouse ▾, workspace ▾, p95-threshold slider, flags ▾, search.
- Flags: `slow`, `high-spill`, `capacity-bound`, `full-scan`, `small-files`, `skew`, `cross-region`. Powered by the deterministic [recommender](#architecture) (§4 of the spec). Demo set: **64 queries**. → The exact flag thresholds, insight-classification order, savings formulas, impact score and confidence are in [**How the numbers are calculated**](#query-advisor--flags-savings--impact).
- **EXPLAIN-validated AI rewrite** *(on `rewrite`-type insights; needs `ai_narration`)* — the drill-down offers **"Suggest & validate rewrite"**: the in-region model (`FINOPS_LLM_ENDPOINT`) proposes a concrete rewritten `SELECT`, then the app runs `EXPLAIN` against your own warehouse and **only shows a rewrite that binds to your real schema** — a hallucinated column fails EXPLAIN and is *rejected*, never presented as runnable. A read-only guard runs before anything touches the warehouse, and EXPLAIN never executes the query. Honest by design: EXPLAIN proves it *runs and binds*, not that results are identical — review before running. (Demo mode shows a deterministic illustrative rewrite that projects the query's own predicate columns.)

![Query Advisor — AI-insight query table](docs/screenshots/09-queryadvisor.png)

### Warehouse Monitor

Per-SQL-warehouse operational health (incl. serverless SQL) — *observable facts only, no model, always on*. KPI row (**Warehouses · Capacity-bound · Idle spend · Spilling · Total cost/mo**) over a warehouse list: type/size, runs, p50/p95, **queued %**, spill, users, cost/mo, and per-warehouse **watch-flags** (`capacity-bound` >15% queued · `slow` high p95 · `spilling` ≥25 GB · `idle` low-runs-real-$). Drill into a warehouse for its **hourly query-volume heatmap**, top users by runs, and a **query-source split** (SQL editor / notebook / job / dashboard / Power BI / Tableau / JDBC). Multi-select filters (workspace · type · watch) + time period. Live source: `system.query.history` aggregated per warehouse — **fail-soft** (an ungranted table shows a clear message, never a fabricated number). Demo set: **8 warehouses**.

### Cluster Efficiency

Interactive / all-purpose clusters — the right lens for *cluster* waste (sizing/idle, not SQL tuning). KPI row (**Clusters · Idle / no auto-stop · Oversized · Cost · Recoverable**) over a cluster list: workers (fixed vs autoscale), auto-stop, mean CPU, cost, **estimated recoverable $**, and per-cluster **findings** (`no-auto-stop` · `idle` · `oversized` · `no-autoscale` · `long-auto-stop` · `mem-pressure`). A **utilisation-window** selector (7 / 14 / 30 / 60 / 90 days) rescales CPU/idle, cost, DBUs and the recoverable estimate — every window-dependent value carries its window in the label (e.g. *Cost (90d)*), and the config-based `oversized` finding is called out as moving less with the window. Drill in for config, CPU/memory utilisation, CPU-by-hour and each finding's detail. The recoverable figure is deliberately **conservative — a single dominant lever per cluster, not additive** — and labelled *estimated*. Built on `system.compute.clusters` + `system.compute.node_timeline` + `system.billing.usage`; the time filter re-windows from a **daily-grain accumulator** (`finops_gold_cluster_daily`) so it can look back **further than `node_timeline` retention**. It **does not** analyse individual queries — cluster query telemetry isn't in system tables (the Spark UI is for per-query tuning); the UI says so. Demo set: **8 clusters**.

### Tables

Managed vs external tables + optimisation economics. Split KPIs (**#Managed vs #External · % managed · external-table `$` opportunity · storage reclaimable GB + `$` · #needing OPTIMIZE / VACUUM / clustering**) over a table list — `catalog.schema.table`, **type (MANAGED / EXTERNAL)**, `sizeInBytes`, `numFiles`, avg file size, small-file ratio, last OPTIMIZE / VACUUM, partitioned?, clustered?, PO enabled?, LC enabled?, scan `$/mo`, **est. savings**, **recommendation**.

- **External tables** get a flag + `$`-saved estimate (storage reclaim + scan reduction) and a **convert-to-managed** path (CTAS / DEEP CLONE) unlocking Predictive Optimization, Liquid Clustering and catalog-managed commits.
- **Managed tables without PO/LC** get an *enable Predictive Optimization + `CLUSTER BY AUTO`* rec, with the DLT `cluster_by_auto` known-issue caveat (needs DBR 15.4 LTS+ and PO enabled at the target catalog/schema *before* pipeline validation). All savings labelled *estimated* with assumptions shown. Demo set: **30 tables**. → Savings formulas (OPTIMIZE / VACUUM / convert-to-managed / enable-PO+LC) in [**How the numbers are calculated**](#tables--savings-components).

![Tables — managed/external classification + savings](docs/screenshots/10-tables.png)

### Governance

Cost-governance posture as **scored tiles** (Good / Warning / Critical), each with the specific gap + next action: tagging coverage % + untagged spend `$`; budget-policy count + coverage; PO / LC adoption %; certification %; lineage coverage; external/unmanaged data % (ties to Tables); cluster-policy coverage; PAT/token risk; over-privileged-principal count (ties to Access); alert/budget setup; audit anomalies. Demo set: **13 tiles**.

![Governance — scored posture tiles](docs/screenshots/11-governance.png)

Governance also surfaces **Governed Tags + Data Classification**: governed-tag coverage (e.g. `system.certification_status`, domain, team) and auto **Data Classification** results (`class.*` column tags — PII/email/name/IP/age) read live from `system.information_schema.{table_tags,column_tags}`. The budget / budget-policy / cluster-policy / PAT / alert tiles come from admin APIs via the `estate-command-center-admin-sync` daily job into the Delta `<ingest_schema>.finops_admin` table; until it runs, live mode shows a "run finops_admin_sync" prompt for exactly those tiles (never sample), and a **Last refreshed** time once ingested.

### AI $

**All AI/GenAI spend a team would investigate, on one page** — Model Serving, Foundation Model APIs, Vector Search, Agent Bricks, AI Functions, and fine-tuning — by product, endpoint, owner, workspace, and trend, from `system.billing.usage`. Carries **cost-optimisation flags** (e.g. endpoints that could scale-to-zero) and a **"% of total platform spend"** context line so AI spend is seen in proportion (it's usually a small slice) rather than in isolation. Genie is on its own **Genie $** page; this page covers the rest of the AI family. Always on (calls no model).

### Adoption & Value

**Who actually uses the estate, and which data earns its keep.** A **value map** (usage × recency, bubble-sized by distinct users) places every heavily-used, fresh asset in a "gold" quadrant and every stale/unused one in an "archive/prune" quadrant — from `system.query.history` + `system.access.table_lineage`. Turns "we have 40k tables" into "these 200 carry the value; these 3k are candidates to archive."

### Data Quality

**Feature-flagged (`features.dqm`) — a Lakehouse-Monitoring inventory with a FinOps cost tie-in.** Because this is a cost tool, the page answers *"which monitors am I paying for, and are they earning their keep?"* not just *"are my tables healthy?"*. Split KPI row — **#Monitors** (Lakehouse Monitoring) · **Critical/Warning** (drift or nulls) · **Stale refreshes** (past cadence) · **Alerts firing** · **DQM cost/mo** (the `DATA_QUALITY_MONITORING` billing line) · **Prune savings/mo** — over a monitored-tables list:

- Columns: **Monitored table** (`catalog.schema.table` + workspace) / **Type** (Snapshot / TimeSeries / Inference) / **Quality** (Good / Warning / Critical) / **Drift (PSI/KS)** / **Null %** / **Anomalies** / **#Metrics** / **Freshness** (Fresh / OK / Stale + *hours ago* vs its refresh cadence) / **Alerts** (set / firing) / **Cost/mo** — with a **`prune?`** hint on low-value monitors.
- **FinOps tie-in:** a **`DATA_QUALITY_MONITORING` cost-by-workspace** breakdown (from `system.billing.usage`) shows which monitored tables/workspaces drive the monitoring spend, and a **"prune monitors on low-value tables"** panel lists monitors that are stale or quiet (no anomalies/alerts) — monitoring that surfaces nothing actionable — with the `$/mo` reclaimed by pausing them. Refresh DBUs run on serverless; figures are list-price reference, Account Console is the source of truth.
- **Data source:** [Lakehouse Monitoring](https://learn.microsoft.com/azure/databricks/lakehouse-monitoring/) — live mode detects existing monitors via monitor metadata / `information_schema`; demo mode ships 12 representative monitors. When `features.dqm=false` the tab **and** `/api/dqm` are absent (inert server-side, not just hidden). Demo set: **12 monitored tables**.

![Data Quality — Lakehouse-Monitoring inventory + DQM cost tie-in (flags-on, with Ask Genie + Genie spend + USD/AUD)](docs/screenshots/12-data-quality.png)

### Recommendations

**★ The primary advisor surface — the unified operator advisor hub.** It aggregates recs from Query Advisor, Tables, Governance and Access-cost, then layers **platform-operator strategic plays** from a **24-play catalogue** (`reference/recommendation_catalog.yaml`) across six categories — *behavioural/enablement · compute · storage · tagging & cost attribution · AI/GenAI spend · access/governance*.

- **Two views:** **Overall** (all recs ranked by priority/savings across the estate) and **Per-workspace** (filtered to one workspace). Filters: **workspace ▾ · priority (P1/P2/P3) · category · scope**.
- **Each rec:** priority pill, category pill, operator-facing title, scope (global/workspace/BU/team/user), **est `$`/mo savings**, effort (Low/Med/High), **evidence chips** (the metrics that fired it — e.g. *"412 queries · 3 users · $4.2K"*), **"What to do"** concrete steps, recommended-feature pills each naming the feature, and a suggested owner. Expandable.
- **Priority model:** `P1 = high $ impact × low effort × (or high risk)`; score = `normalise($/mo × frequency/severity ÷ effort) × risk_multiplier`. Sorted by tier then `$`. → Full scoring + tier-assignment rules in [**How the numbers are calculated**](#recommendations--priority).
- **Cost-attribution panel:** spend by workspace / BU / tag / user, untagged %, top cost drivers, the specific tagging + budget-policy plays to close attribution gaps, **and a [Genie-spend breakdown](#genie-cost-attribution)** (Genie Code vs Genie Spaces per workspace × user).

> **Working with alerts (acknowledge / ignore).** Every alert surface (Recommendations, Jobs, Warehouses, Clusters, Tables, Query Advisor) carries a single **always-visible action bar** above the table: a **status filter** (Open · Acknowledged · In progress · Won't fix · All, with counts) and an **Acknowledge** action that's disabled-with-a-hint until you tick one or more rows (so it's discoverable before selecting). Tick rows → acknowledge them in one step (with an optional reason + snooze), or **reopen** acked ones. Acknowledged items drop out of the default *Open* view and **auto-reopen** if the underlying metric gets materially worse (>25%) or the snooze lapses — so dismissing an alert is never permanent forgetting. Requires the `writeback` flag (on by default). See [Feature flags](#feature-flags--deploy-variants) for the live-mode persistence requirement.

> **Business-unit filters.** When you supply a `workspace_bu_table` mapping, a **Business unit** filter appears on Jobs, Warehouses, Clusters, Query Advisor, Workspaces and Recommendations (a job/warehouse/etc. inherits its workspace's BU). When no mapping is configured the BU dimension is hidden everywhere — nothing shows a blank placeholder.

> **Built for a real estate's scale.** The heavy live tabs are served through a **two-tier cache** (in-process + an optional persistent Lakebase tier) with stale-while-revalidate, so repeat loads are ~1–2s and the cache **survives restarts/redeploys** rather than re-paying a cold scan. Wide tables **hide lower-priority columns responsively** (they move into the row drill-down) so you don't scroll sideways on a laptop, and big lists (e.g. tens of thousands of jobs) show the **most-worth-investigating top-N by default** with a **"Load all"** opt-in — KPI totals are always computed over the full set.

| Overall, ranked | Filtered to P1 |
|---|---|
| ![Recommendations — overall ranked](docs/screenshots/02-recommendations-overall.png) | ![Recommendations — P1 filter](docs/screenshots/03-filter-p1.png) |
| **Expanded rec — evidence + steps + feature pills** | **Per-workspace view** |
| ![Recommendation expanded](docs/screenshots/04-rec-expanded.png) | ![Recommendations per-workspace](docs/screenshots/06-recommendations-perworkspace.png) |
| **Cost-attribution + Genie spend** | |
| ![Recommendations cost-attribution panel with Genie spend](docs/screenshots/15-recommendations-attribution.png) | |

---

## Ask Genie

**A streaming natural-language front door to the estate — gated by `features.genie`.** A persistent banner sits beneath the nav: *"Ask Genie about your costs & usage"* — a text input plus suggested-prompt chips (*"Which team's queries cost the most last month?"*, *"Untagged spend by workspace"*, *"Genie Code vs Genie Space spend for a user"*, *"Top external tables to convert"*). Answers **stream in over SSE** (like GridSense's `useGenieStream`) via the **Genie Conversation API** against a **Genie Space**, and a **follow-up** chip row keeps the conversation going.

**The Genie Space is comprehensive + auto-provisioned — no manual setup.** It's curated over **9 system tables** so questions can range across cost, SQL, jobs, warehouses, clusters, lineage and workspace context — not just billing:

`system.billing.usage` · `system.billing.list_prices` · `system.query.history` · `system.access.table_lineage` · `system.access.workspaces_latest` (so answers use workspace **names**, not ids) · `system.compute.warehouses` · `system.compute.clusters` · `system.compute.node_timeline` · `system.lakeflow.job_run_timeline`

- **Auto-provisioned + auto-discovered:** on deploy the app **creates** a `Estate Command Center` Genie Space over those tables if one doesn't exist, and **discovers it by title** on startup — so you do **not** need to set `genie_space_id` (set it only to point at your own Space instead). Re-provisioning **reuses and syncs** the existing Space (dedupe by title prefix), so restarts never spawn duplicates. *(Deliberately excluded from the Space: `information_schema.*` — per-catalog + noisy for NL — `access.audit` — huge — and the optional/grant-gated `data_quality_monitoring` / `data_classification` tables, which would error the Space if ungranted.)*
- **Live mode = real Genie only, never a demo answer.** In live the app calls the real Genie Conversation API. If a Space isn't configured/available it says so honestly ("Ask Genie isn't configured…"); if Genie can't answer it says so — it **never** fabricates a canned answer. The `demo answer` (with its synthetic chart) appears **only in demo mode**.
- **Demo mode:** returns **canned, streamed** answers grounded in the seeded data, badged `demo answer`.
- **Inert when off:** with `features.genie=false` the banner is hidden **and** `/api/genie/ask` returns 404 — no Genie or model calls happen server-side.

![Ask Genie — streaming NL Q&A over cost/usage/access](docs/screenshots/14-ask-genie.png)

---

## Genie cost attribution

**Always on** (it calls no model, so it is not an "AI feature") and it has its own **Genie $** tab plus panels in [Recommendations](#recommendations) and the [Workspaces drill-down](#workspaces). Genie is billed under `billing_origin_product = 'GENIE'` with a `usage_metadata.genie` struct `{surface, channel, agent_id}`. The app splits Genie spend **by surface, per workspace × user** — verified against real `system.billing.usage`:

| `usage_metadata.genie.surface` | Shown as | What it is |
|---|---|---|
| `GENIE_CODE` | **Genie Code** | NL-to-SQL in the SQL editor / notebook (technical users) |
| `GENIE_ONE` | **Genie Spaces** | Genie One / Databricks One conversational chat (business users) |
| `GENIE_AGENTS` | **Genie Agents** | Genie called programmatically inside an agent (Agent Bricks / API) |

The surface enum is **open** — the app labels whatever values appear rather than assuming a fixed set. Three queries (in [`data/queries.sql`](data/queries.sql), assembled once in `data/genie_cost.py` so demo and live reconcile) back it: (1) ground-truth usage by `usage_date × workspace × run_as × surface`; (2) an approximate **paid** split, pro-rating each user's paid DBUs across surfaces *after* the shared **150-free-DBU-per-identified-user-per-month** allowance; (3) per-workspace totals. **This is the first panel wired for [live mode](#what-reads-live-vs-how).**

**Caveats shown in the UI (not glossed over):**

- The surface split is **real in the billing data but not yet in the published schema doc** — treat as may-change; the app degrades to per-user/workspace totals if the field is null.
- The 150 free DBUs are **shared across all surfaces**, so per-surface *paid* `$` is a pro-rated estimate; only the per-user total paid figure is exact.
- **Budgets can target Genie** (Unity AI Gateway + tag `databricks-product: genie`) but **cannot be split by surface**, and cannot remove the free tier.
- `$` is **list-price reference** (`usage_quantity × pricing.effective_list.default`); the **Account Console is the source of truth**. Filter on `billing_origin_product = 'GENIE'`, never by SKU.
- Consumption-based pricing began **6 Jul 2026**, so figures **read light until usage flows**.

![Genie spend in the Workspaces drill-down](docs/screenshots/16-workspaces-drilldown-genie.png)

---

## How the numbers are calculated

Every `$` figure in the app is a **deterministic, rule-based estimate** — no model is involved in any calculation. (The optional LLM only rewrites the *wording* of a recommendation's rationale, never its number; see [Feature flags](#feature-flags--deploy-variants).) Each estimate follows one shape: **observed cost × a published reduction fraction × how severe the measured inefficiency is**, so it is fully explainable and auditable. Those reduction fractions are expert-set constants named at the top of [`data/recommender.py`](data/recommender.py) and [`data/generate_demo.py`](data/generate_demo.py) — change them there and every downstream figure moves with them. The inputs are the customer's real `system.*` metrics in live mode or the seeded Acme metrics in demo mode; the formulas are identical either way. All `$` are **list-price reference** (`usage_quantity × pricing.effective_list.default`), labelled *estimated* in the UI — the Account Console is the source of truth.

### Query Advisor — flags, savings & impact

Each query row is scored purely from its statement metrics (`system.query.history`), in three steps: **flag → classify → estimate**.

**1. Diagnostic flags** — each maps to one measurable condition:

| Flag | Fires when |
|---|---|
| `slow` | p95 latency ≥ 30 s |
| `high-spill` | disk spill ≥ 20 GB |
| `capacity-bound` | queued ratio ≥ 20% |
| `full-scan` | pruning < 30% **and** ≥ 50 GB read (or `SELECT *` on a ≥ 50 GB read) |
| `small-files` | small-file ratio ≥ 40% |
| `skew` | partition skew ratio ≥ 3× |
| `cross-region` | query reads across a region boundary |

**2. Classification** — the single highest-leverage lever wins, in this order: **resize** (spill ≥ 20 GB) → **cluster** (pruning < 30% on a ≥ 50 GB scan) → **rewrite** (`SELECT *` / weak predicates under 45% pruning) → **optimize / vacuum** (small files / stale versions).

**3. Estimated `$/mo` savings** = the statement's observed monthly cost × the lever's expected reduction × the strength of the signal:

| Lever | Formula |
|---|---|
| `cluster` | `cost × 0.55 × (1 − pruning)` |
| `resize` | `cost × 0.60 × min(1, spill_gb ÷ 220)` |
| `rewrite` | `cost × 0.40 × min(1, (1 − pruning) + 0.3)` |
| `optimize` | `cost × 0.35 × max(0.4, small_file_ratio)` |
| `vacuum` | `cost × 0.255` (stale-version re-read overhead) |

The **impact score (0–100)** that ranks the table is `savings + frequency + severity`, each capped and weighted:
`min(1, savings ÷ $3,500)×40 + min(1, runs ÷ 3,000)×30 + severity×30`, where `severity = (1 − pruning)×0.5 + min(1, spill ÷ 220)×0.3 + min(1, p95 ÷ 260)×0.2`.
**Confidence** rises with signal strength and run count: `0.55 + 0.30×severity + 0.10×min(1, runs ÷ 1,000)` (rewrites −0.05), clamped to 0.50–0.97.

### Tables — savings components

In live mode `table_type` comes from `information_schema` (authoritative — external tables classify correctly) and **bulk sizes** come from the nightly gold census (`finops_gold_table_sizes`); **file-hygiene** stats (`numFiles`, small-file ratio via `DESCRIBE DETAIL`) are **deferred to drill-down**. A **cold-storage** view flags large-but-rarely-read tables. Storage is priced at `$0.023/GB/mo` (list). The savings components (`$/mo`):

- **OPTIMIZE** (compaction) = `scan_cost × small_file_ratio × 0.60`
- **VACUUM** (reclaim) = `reclaimable_GB × $0.023`, where `reclaimable_GB = size_GB × stale_ratio`
- **Convert external → managed** = `OPTIMIZE + VACUUM + scan_cost × 0.35` (the auto-Liquid-Clustering scan uplift unlocked by going managed)
- **Enable PO + Liquid Clustering** (managed without PO) = `size_GB × $0.023 × 0.50` (PO ≈ 50% storage cut) `+ scan_cost × 0.95` on an over-partitioned table, else `× 0.35`

The headline **est. savings** for a table is the figure for its *recommended* action only, so components are never double-counted.

### Workspaces — savings & health

Per-workspace **savings/mo** = `spend × (0.10 + 0.55×automation_gap + 0.40×idle% + 0.25×(1 − serverless_share))`, capped at **62% of spend**, where `automation_gap = max(0, 0.75 − automated%)` and *automated* = scheduled-job compute ÷ all compute. **Health** is graded on automation alone: **Good ≥ 60%**, **Warning 25–60%**, **Critical < 25%**.

### Recommendations — priority

The hub aggregates every page's recs, then scores each one:

```
priority_score = normalise($/mo × severity) ÷ effort_weight × risk_multiplier
```

`effort_weight` is Low 1.0 / Med 2.0 / High 3.0, and `$/mo` is normalised against the highest-value rec in the set. A pure risk-avoidance rec (`$0` savings but high risk) still scores via `0.15 × severity × risk_multiplier`. **Tiering:** a rec is **P1** when it is both high-`$` (≥ 50% of the top rec) *and* low/medium effort, **or** it carries a high risk multiplier (≥ 1.3); **P2** when it is ≥ 20% of the top rec or medium effort; otherwise **P3** (the catalogue can also pin an explicit tier for specific plays). The list sorts by tier, then `$` descending.

### Genie spend & currency

The Genie **paid** split pro-rates each user's billable DBUs across surfaces *after* the shared 150-free-DBU-per-user allowance — see [Genie cost attribution](#genie-cost-attribution) for the full caveats (only the per-user total is exact; the per-surface paid `$` is an estimate). The **AUD** toggle restates every figure as `USD list price × fx_rate` (`config.yaml`, default `1.52`), labelled indicative.

---

## Feature flags & deploy variants

The app reads a single config source — `config.yaml`, with every value overridable by the matching `app.yaml` / App-UI environment variable (env wins) — and surfaces it via **`/api/config`**. The frontend fetches `/api/config` once and **conditionally renders**: the [Ask Genie](#ask-genie) banner only if `genie`, the [Data Quality](#data-quality) tab only if `dqm`, and LLM rec narration only if `ai_narration`. Anything hidden is also **inert server-side** — its endpoints and any Genie/model calls are absent, not merely unrendered.

| Flag | Built-in default | Shipped `config.yaml` | Gates | Notes |
|---|---|---|---|---|
| `features.genie` | `true` | `true` | Ask Genie banner + `/api/genie/ask` SSE | Genie Conversation API over the auto-provisioned/`genie_space_id` Space. |
| `features.ai_narration` | **`false`** | `true` | AI SQL-rewrite panel (+ optional LLM rec rationale) | **Off by default** (no model calls / no surprise spend) — the shipped demo config turns it on to showcase the EXPLAIN-validated rewrite. Falls back to deterministic text if no endpoint. |
| `features.dqm` | `true` | `true` | Data Quality tab + `/api/dqm` | Lakehouse Monitoring inventory + cost tie-in. |
| `features.writeback` | `true` | `true` | Acknowledge/ignore controls on every alert surface + `/api/acks` | **On by default** — independent of AI/Genie. State lives in a small Delta table via the read warehouse (no extra infra); the optional `-t customer-lakebase` deploy swaps in managed Postgres. Off ⇒ controls hidden + `/api/acks` 404. |

> The **deterministic recommender**, **cost-attribution**, and **[Genie cost attribution](#genie-cost-attribution)** are **always on** regardless of these flags — they call no model, so they are not "AI features".

**Composable deploy variants** — the capabilities toggle independently, so any subset can be on. This repo's `config.yaml` ships the three AI/observability flags on (to showcase everything); the safe built-in default is genie + dqm on, ai_narration off. `writeback` is a separate, AI-independent flag and is on by default in every variant. A few common combinations:

| Variant | `genie` | `ai_narration` | `dqm` | Tabs | For |
|---|:--:|:--:|:--:|:--:|---|
| **Default (everything on)** | ✅ | ✅ | ✅ | 14 | Full experience. |
| **AI only** | ✅ | ✅ | ❌ | 13 | AI appetite, no Lakehouse Monitoring. |
| **DQM only** | ❌ | ❌ | ✅ | 14 | DQM inventory, no Genie/LLM. |
| **Deterministic core only** | ❌ | ❌ | ❌ | 13 | Most conservative — no Genie, no LLM, no DQM. |

**Only `dqm` changes the tab count** — it gates the Data Quality tab. `genie` gates the Ask Genie banner (not a tab) and `ai_narration` gates only the LLM rec narration (no tab), so both leave the count unchanged.

`deploy.sh` provides four independent, composable switches — `--no-genie` (Ask Genie off), `--no-narration` (LLM rec-narration off), `--no-ai` (both Genie + narration off — a shortcut), and `--no-dqm` (Data Quality tab off) — usable in any combination, no code edits to onboard a customer. The same is settable declaratively via the `features:` block in `customise.yaml`. See the **[Deployment guide](docs/DEPLOYMENT.md)** for the full flag matrix, per-customer `customise.yaml`, the exact grants per variant, and the one-command bundle deploy.

The screenshot below is the **"neither" variant** — 13 tabs (no Data Quality), no Ask Genie banner — contrast it with the [Data Quality shot](#data-quality) above (the "AI + DQM" variant, showing DQM, Ask Genie, Genie spend and the USD/AUD toggle in one frame):

![Neither variant — flags off: 13 tabs (no Data Quality), no Ask Genie banner](docs/screenshots/13-flags-off-overview.png)

---

## Dual currency & cost-by-driver cost views

Folded in from the **cost-by-driver reference dashboard** — they validate the AU-East SKU framing (SKUs literally end in `_AUSTRALIA_EAST`):

- **Dual currency (USD/AUD).** A header toggle restates every `$` figure across the estate: AUD = list-price USD × `fx_rate` (`config.yaml`, default `1.52`), clearly labelled indicative. USD is the default.
- **Cost by cost-driver (product).** A bar of total cost per `billing_origin_product` (SQL, Jobs, Interactive, DLT/Lakeflow, All-Purpose, Model Serving, AI Gateway, Predictive Optimization, Vector Search, Apps, Lakebase, Data Quality Monitoring). → Overview + Workspaces drill-down.
- **Monthly trend — top-5 drivers.** A multi-line trend of the five biggest drivers over the last 6 months. → Overview + Workspaces.
- **MoM spike detection.** A **driver-change-vs-prior-30-days (%)** bar; drivers up **≥ 25% MoM** fire a **cost-spike recommendation** (*investigate + attribute*) in the [Recommendations](#recommendations) hub. → Overview + Workspaces + recommendation catalogue trigger.
- **SKU-level breakdown.** The Workspaces **By Service/SKU** sub-view lists every `_AUSTRALIA_EAST` SKU with `total_cost`, `DBUs/mo` and **`pct_of_total`** (e.g. `PREMIUM_SERVERLESS_SQL_COMPUTE_AUSTRALIA_EAST` 15.5%), plus cost-by-SKU (stacked by driver) and DBU-consumption-by-SKU charts.

![Workspaces — By Service/SKU sub-view with `_AUSTRALIA_EAST` SKU breakdown](docs/screenshots/17-workspaces-sku-view.png)

---

## Architecture

Data sources → system tables → FastAPI (`app.py` + `data/*`) → React pages. Full narrative and per-layer design rationale in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```mermaid
flowchart LR
  subgraph SRC["Estate data sources"]
    WS[Workspaces / SQL warehouses]
    UCAT[Unity Catalog<br/>catalogs · schemas · tables]
    IDP[Identity<br/>AIM / SCIM / groups]
  end

  subgraph STAB["Databricks system tables (live mode)"]
    BILL[billing.usage<br/>billing.list_prices]
    QRY[query.history]
    AUD[access.audit]
    PRIV[information_schema<br/>*_privileges]
    LF[lakeflow.jobs · job_run_timeline]
    DD[DESCRIBE DETAIL<br/>per-table physical stats]
  end

  subgraph API["FastAPI · app.py"]
    CFG[config.yaml + env<br/>feature flags → /api/config]
    EP["/api/* endpoints"]
    DEMO[data/generate_demo.py<br/>seeded Acme demo]
    REC[data/recommender.py<br/>deterministic rules]
    HUB[data/rec_hub.py<br/>catalogue matcher + scorer]
    GENIE["/api/genie/ask<br/>SSE · Genie Conversation API"]
    DQM["/api/dqm<br/>Lakehouse Monitoring"]
  end

  subgraph UI["React 18 SPA — up to 14 pages"]
    P1[Overview] & P2[Access] & P3[Workspaces] & P4[Jobs]
    P5[Query Advisor] & P6[Warehouses] & P7[Clusters] & P8[Tables]
    P9[Governance] & P10[Adoption & Value] & P11["Genie $"] & P12["AI $"]
    P13["Data Quality (if dqm)"] & P14[Recommendations]
    BAN["Ask Genie banner (if genie)"]
  end

  WS --> BILL & QRY & LF
  UCAT --> PRIV & DD
  IDP --> PRIV
  BILL & QRY & AUD & PRIV & LF & DD -.->|USE_LIVE=true| EP
  DEMO -.->|USE_LIVE=false| EP
  CFG -->|gates genie · dqm · ai_narration| EP
  EP --> REC --> HUB
  EP -->|serve dist/| UI
  UI -->|fetch /api/config then /api| EP
  BAN -.->|SSE stream| GENIE
  P13 -.-> DQM
```

**Recommendation-engine flow** — how estate metrics become scored recs:

```mermaid
flowchart TB
  M[Estate metrics<br/>per query · table · workspace · BU · principal] --> T{Triggers<br/>pruning &lt;30% · spill high · idle warehouse ·<br/>external-table $ · untagged % · MoM spike · over-privilege}
  T --> C[recommendation_catalog.yaml<br/>24 plays × 6 categories<br/>+ per-query/table recommender rules]
  C --> S[Score &amp; prioritise<br/>norm $/mo × severity ÷ effort × risk → P1/P2/P3]
  S --> A[Actionable rec<br/>apply CTA + feature pills]
  A --> OUT["/api/recommendations/hub<br/>ranked list + cost-attribution rollup"]
```

---

## Tech stack

- **Frontend:** React 18 · Vite · TypeScript · Tailwind (`darkMode: "class"`, CSS-var theming) · Zustand (state) · Recharts (charts) · react-markdown · DM Sans. GitHub-dark surface with a Databricks-red accent.
- **Backend:** FastAPI (`app.py`) serving `/api/*` and mounting the built SPA from `frontend/dist` via `StaticFiles`. Deterministic recommender (`data/recommender.py`) + catalogue-driven hub (`data/rec_hub.py`). A `config.yaml` + env feature-flag layer surfaced via `/api/config`; SSE streaming for [Ask Genie](#ask-genie) (`/api/genie/ask`); a Lakehouse-Monitoring [Data Quality](#data-quality) endpoint (`/api/dqm`, flag-gated).
- **Deploy:** Databricks App — deployed as an **Asset Bundle** (`databricks.yml`) with `databricks bundle deploy -t customer` — **no Python, no npm** (the built `frontend/dist` is committed). `app.yaml` (run config) · `deploy.sh` (optional convenience wrapper) · `requirements.txt`.
- **Data:** `data/generate_demo.py` (seeded Acme-shaped demo, default) or live system tables via the Databricks SDK Statement Execution API (`USE_LIVE=true`, in `data/live.py`), with the exact SQL in `data/queries.sql`. Live mode serves from nightly precomputed **gold Delta tables** (`data/gold.py` → `<gold_schema>.finops_gold`).

---

## Data requirements

Demo mode (`USE_LIVE=false`, the default) needs **nothing** — no warehouse, no grants. Live mode (`USE_LIVE=true` + a SQL warehouse) runs the queries in [`data/queries.sql`](data/queries.sql). The app service principal needs **`SELECT`** on:

| System table / source | Powers | Notes |
|---|---|---|
| `system.billing.usage` | Overview, Workspaces spend/DBUs | GA |
| `system.billing.list_prices` | `$` figures (join to usage) | GA |
| `system.query.history` | Query Advisor, Warehouse Monitor, top users/warehouses | **PuPr** — badge in UI |
| `system.compute.clusters`, `system.compute.node_timeline` | [Cluster Efficiency](#key-capabilities--per-page) — interactive-cluster sizing / idle / CPU–memory | GA (`clusters` is SCD — latest row per cluster) |
| `system.access.audit` | Access last-access / stale grants | **PuPr** — badge in UI |
| `system.access.table_lineage` | Tables usage/health, Adoption value map | GA |
| `system.information_schema.*_privileges` | Access grant graph (table/schema/catalog/routine) | GA |
| `system.information_schema.{tables,table_tags,column_tags}` | External share, governed tags, `class.*` auto-classification | GA |
| `system.data_quality_monitoring.table_results` | Data Quality tab only (inventory + status). Deliberately NOT folded into Tables health — DQM has its own tab. | Only if `features.dqm` (degrades gracefully if ungranted) |
| `system.lakeflow.jobs`, `job_run_timeline`, `job_task_run_timeline` | Automated %, top jobs | GA (`*.pipelines` is PuPr) |
| `DESCRIBE DETAIL <fqn>` | Tables — `numFiles`, `sizeInBytes`, clustering/partition cols | Per-table; not a single system query |
| `system.billing.usage` (`billing_origin_product = 'GENIE'`, `usage_metadata.genie.surface`) | [Genie $](#genie-cost-attribution) — Code / Spaces / Agents per workspace × user | GA; always on; **live-wired** |
| `system.billing.usage` (`billing_origin_product = 'DATA_QUALITY_MONITORING'`) + monitor metadata / `information_schema` | [Data Quality](#data-quality) — monitor inventory + DQM cost | Only if `features.dqm` |
| **Genie Space** (`genie_space_id`) via the Genie Conversation API | [Ask Genie](#ask-genie) streaming Q&A | Only if `features.genie` |

The **Access** grant graph (principals · grants · risks) and drill-downs read live from `system.information_schema.*_privileges`. Group membership, workspace assignments, entitlements and PAT inventory come from the account **SCIM / Groups** + **workspace-assignments** + **token-management** REST APIs — not SQL — so they're pulled by the daily **`finops_admin_sync`** job into the Delta **`<ingest_schema>.finops_admin` table** and the app reads them from there (with a "Last refreshed" time). See `docs/ARCHITECTURE.md`.

The last three rows are **only** needed for the corresponding feature flag: DQM reads need the monitor metadata + the `DATA_QUALITY_MONITORING` billing line; Ask Genie needs the app SP to be able to **create / list Genie Spaces** (for auto-provision + title discovery) and `CAN RUN` on the Space, plus `SELECT` on the 9 curated system tables the Space queries (a subset of what the app already reads). All reads are read-only. The [Deployment guide](docs/DEPLOYMENT.md) lists the exact grants per deploy variant.

**Grant statement (minimum):**

```sql
GRANT SELECT ON SCHEMA system.billing            TO `<app-service-principal>`;
GRANT SELECT ON TABLE  system.query.history      TO `<app-service-principal>`;
GRANT SELECT ON SCHEMA system.compute            TO `<app-service-principal>`;  -- Cluster Efficiency (clusters + node_timeline)
GRANT SELECT ON TABLE  system.access.audit       TO `<app-service-principal>`;
GRANT SELECT ON SCHEMA system.information_schema  TO `<app-service-principal>`;
GRANT SELECT ON SCHEMA system.lakeflow           TO `<app-service-principal>`;
-- plus USE CATALOG / USE SCHEMA on any user catalog you want DESCRIBE DETAIL to reach.
```

Every API payload carries `demo` / `mode` metadata; the UI renders a **DEMO DATA** badge whenever `USE_LIVE=false`, so demo numbers are never mistaken for live ones.

---

## Deploy & productionalise in a customer workspace

The design goal is **one artifact, config-switched, fail-closed**: the same app ships to your demo workspace and to a customer's production workspace — a single config surface (the env vars in `app.yaml`) decides whether it shows seeded sample data or reads the customer's real system tables. No code changes per customer.

> [!TIP]
> **Just want to stand up a demo?** The foolproof 3-step demo deploy lives in the
> **[Internal field guide](docs/FIELD_GUIDE.md)**. The rest of this section is the
> reference detail (grants, the scheduled jobs, the nightly gold-table serving layer,
> productionalising) that the
> [Customer deployment guide](docs/CUSTOMER_DEPLOYMENT.md) draws on for a live deploy.

### TL;DR — deploy the Asset Bundle from inside your workspace (no laptop, no Python, no npm)

You do **not** need to install anything on your laptop. The recommended path adds the code to your Databricks workspace as a **Git folder**, then deploys the **Asset Bundle** from a notebook cell. The built `frontend/dist` is already committed to the repo, so **there is nothing to build** — no Python, no npm, no local tools. It ships in **demo mode** by default (no warehouse, no grants, nothing to break), and you flip it to live data with three settings when you're ready.

The steps below are the whole thing: **add the code → set config → deploy from a notebook → grant read access → turn on the daily jobs** (plus an optional final step to light up BU/team). A CLI-on-your-laptop alternative follows in [Prefer the CLI on your laptop?](#prefer-the-cli-on-your-laptop) if you'd rather work from a terminal.

### Deploy from your Databricks workspace (recommended — no laptop, no Python)

Follow these one-action-at-a-time steps inside the Databricks workspace. If you only want to *try* the app, do steps 1 and 3 and stop — with demo mode you don't need steps 2, 4, or 5.

#### Step 1 — Add the code to your workspace

In the top-right of the workspace, click **Create ▾ → Git folder**. In the **Git repository URL** box paste:

```
https://github.com/beeyal/databricks-estate-command-center
```

(This is a **private** repo — if you can't see it, request access first.) Click **Create**. The code lands at `/Workspace/Users/<you>/databricks-estate-command-center` (where `<you>` is your own email). That's your copy of the app — no download, no build.

#### Step 2 — Set your configuration

Open the file `app.yaml` inside that Git folder (double-click it in the workspace file browser). To read your real data, change just these three lines:

```yaml
USE_LIVE: "true"                       # "false" = seeded demo data (safe default); "true" = your real system tables
DATABRICKS_WAREHOUSE_ID: "<your-serverless-sql-warehouse-id>"   # a serverless SQL warehouse in this workspace
FINOPS_GOLD_SCHEMA: "main.finops_gold" # REQUIRED in live mode — schema the nightly gold-sync job precomputes estate payloads into (the app refuses to boot live without it)
```

To turn a **feature off**, set its flag in this same file (genie / dqm / writeback
ship on; ai_narration's built-in default is off, though this repo's config turns it on):

```yaml
FINOPS_FEATURE_DQM: "false"            # hide the Data Quality tab
FINOPS_FEATURE_GENIE: "false"          # hide the Ask Genie banner
FINOPS_FEATURE_AI_NARRATION: "false"   # drop the optional LLM rationale on recommendations
FINOPS_FEATURE_WRITEBACK: "false"      # strictly read-only: hide acknowledge/ignore controls
```

**To just try it, skip this step entirely** — with `USE_LIVE` left at `"false"` the app runs in demo mode with **no warehouse and no grants** needed.

#### Step 3 — Deploy it from a notebook

Create a new notebook (or open the workspace **web terminal**) and run a single `%sh` cell — replace `<you>` with your email:

```
%sh
cd /Workspace/Users/<you>/databricks-estate-command-center
databricks bundle deploy -t customer
databricks bundle run estate -t customer
```

In plain words: this creates the app and its daily job in your workspace and prints the app's URL when it's done. You don't have to sign in or paste any token — the notebook already runs **as you**, so the bundle authenticates automatically. Open the printed URL and the app is live (in demo mode unless you did step 2).

#### Step 4 — Grant read access (skip if you're staying in demo mode)

Open a **SQL Editor** tab and run the `GRANT` block from [Reference — grants](#reference--grants-live-mode-only) below. It gives the app's service principal read-only `SELECT` on the system tables. Copy it, paste it in, replace `<app-service-principal>` with the value shown on your app's page, and run it.

#### Step 5 — Turn on the scheduled jobs (skip if you're staying in demo mode)

Go to **Workflows** and **unpause** the jobs (both ship paused): **`finops_gold_sync`** (nightly, **required for live mode** — precomputes every estate payload + table-size census into `<gold_schema>.finops_gold`; **run it once manually right after unpausing** so live pages have data to serve; its SP needs read on the system tables + `CREATE`/`MODIFY` on `gold_schema`) and **`estate-command-center-admin-sync`** (daily, optional — set it to **run as an admin service principal**, since it needs account + workspace admin to read the admin APIs and `CREATE`/`MODIFY` on `ingest_schema`; see [Reference — the scheduled jobs](#reference--the-two-scheduled-jobs)).

That's the whole workspace flow: **Git folder → edit `app.yaml` → `%sh` deploy cell → grants in SQL Editor → unpause the two jobs.** The frontend is pre-built and committed, so nothing compiles and no laptop is involved.

> **Good to know.** The app serves a compiled React bundle (`frontend/dist`) that is **committed to the repo**, so the deploy never runs npm or a build step. The bundle deploy creates the app and **two** scheduled jobs — `finops_gold_sync` (nightly serving-layer precompute) and `estate-command-center-admin-sync` (daily admin-API ingest).

### Prefer the CLI on your laptop?

This is the **secondary** path — use it only if you'd rather work from your own terminal. It does the same thing as the workspace flow above, just from your laptop.

1. **Install the Databricks CLI.** On macOS: `brew install databricks`. On Windows: `winget install Databricks.CLI`.
2. **Get the code:** `git clone https://github.com/beeyal/databricks-estate-command-center` (private repo — request access first), then `cd databricks-estate-command-center`.
3. **Log in:** `databricks auth login --profile my-workspace` and follow the browser prompt.
4. **Set your config:** edit `app.yaml` exactly as in [Step 2 — Set your configuration](#step-2--set-your-configuration) above (`USE_LIVE`, `DATABRICKS_WAREHOUSE_ID`, `FINOPS_GOLD_SCHEMA`, and any `FINOPS_FEATURE_*` flags).
5. **Deploy and run:**

```bash
databricks bundle deploy -t customer -p my-workspace
databricks bundle run   estate -t customer -p my-workspace
```

The `customer` target is portable — it no longer hardcodes a host, so it deploys to whatever workspace the `-p` profile points at. Then run the grants ([Reference — grants](#reference--grants-live-mode-only)) and unpause the daily job as above.

> `./deploy.sh` exists as a convenience wrapper (it reads a `customise.yaml`, writes the matching env into `app.yaml`, and runs the bundle for you), but it **needs Python** — so the plain `databricks bundle` commands above are the recommended path.

### Reference — prerequisites

| For | You need |
|---|---|
| **Any deploy** | A Databricks workspace with **Databricks Apps enabled**, and permission to create an app. For the recommended workspace flow, that's all — the `%sh` deploy cell authenticates as you automatically. (Only the laptop CLI path needs `databricks auth login --profile <name>`.) |
| **Live mode** (real data) | A **serverless SQL warehouse** in that workspace, and the ability to `GRANT SELECT` on the `system.*` schemas to the app's service principal (metastore-admin, or someone who can). |
| **Admin-API panels** (governance budget/policy/PAT/alert tiles, Access groups + PATs, DQM monitor configs) | A UC schema (`ingest_schema`) the `finops_admin_sync` job can write, and the **`finops_admin_sync` job** scheduled — its service principal needs **account admin** + **workspace admin** + `CREATE`/`MODIFY` on `ingest_schema` (see [Reference — the scheduled jobs](#reference--the-two-scheduled-jobs)). |
| **Live mode** (any estate) | **Required:** set **`gold_schema`** (e.g. `main.finops_gold`) and unpause the nightly **`finops_gold_sync`** job. It precomputes every estate payload + a per-table size census into `<gold_schema>.finops_gold` + `finops_gold_table_sizes`, so the app serves each page in ~1s on a scale-to-zero warehouse — no per-request scan of the account-wide system tables (which is not production-viable). The job's SP needs read on the system tables + `CREATE`/`MODIFY` on `gold_schema`. The app **won't boot in live mode without `gold_schema`** — for a read-only eval only, `FINOPS_ALLOW_LIVE_NO_GOLD=true` runs live off system tables (slow). |
| **Data Quality tab** | For live data the app SP needs `SELECT` on `system.data_quality_monitoring`; monitor configs come from the `finops_admin_sync` ingest. |
| **Ask Genie** | Auto-provisioned — the deploy **creates a "Estate Command Center" Genie Space** over 9 cost/usage/ops system tables if `genie_space_id` is blank (most customers won't have one), and **auto-discovers it by title** on startup, so no manual id is needed. Re-provisioning reuses + syncs the existing Space (no duplicates). Set `genie_space_id` only to point at your own Space instead. |
| **AI narration** (optional) | A **model-serving / FMAPI endpoint** (e.g. `databricks-claude-sonnet-4-6` — a pay-per-token Foundation Model endpoint). The deploy verifies it exists; only needed when `features.ai_narration: true`. |

### Reference — the full config surface

For the recommended workspace / bundle deploy, config is the **env vars in `app.yaml`** (see [Step 2 — Set your configuration](#step-2--set-your-configuration)); that's all you edit. The table below is the *complete* set of settings for reference — each `app.yaml` env var maps to one of these keys. If you use the `deploy.sh` wrapper (CLI path), you can instead supply them declaratively in a `customise.yaml`, which the wrapper turns into the same `app.yaml` env. Every field is optional and falls back to a safe default; precedence is **env var → `config.yaml` → built-in default**.

```yaml
customer_name: "Contoso Energy"      # shown in the header
use_live:      false                 # false = seeded demo (safe); true = read real system tables
warehouse_id:  ""                    # REQUIRED when use_live=true — a serverless SQL warehouse id
genie_space_id: ""                   # blank ⇒ auto-create + auto-discover the FinOps Genie Space (live); demo mode uses canned answers
llm_endpoint:  databricks-claude-sonnet-4-6   # FMAPI model for optional rec narration
fx_aud:        1.52                   # AUD = USD list price × this (dual-currency toggle)
catalog_scope: []                    # [] = all catalogs; or an allow-list, e.g. [prod, analytics]
gold_schema: ""                      # schema the nightly finops_gold_sync job precomputes estate payloads into; blank = compute live (env FINOPS_GOLD_SCHEMA)
workspace_bu_table: ""               # optional UC table mapping workspace → customer/BU; blank ⇒ BU dimension hidden. Copy-paste setup: reference/mapping_tables_setup.sql
user_team_table: ""                  # optional UC table mapping user → team; blank ⇒ team dimension hidden. Same setup file
ingest_schema: ""                    # UC schema the finops_admin_sync job writes the finops_admin Delta table into
writeback_schema: ""                 # UC schema for the writeback finops_ack_status table (env FINOPS_WRITEBACK_SCHEMA); blank ⇒ falls back to gold_schema, and if that's blank too, writeback reports off (Delta backend, live mode)
features:                            # all four capabilities toggle independently
  genie:        true                 # Ask Genie banner + /api/genie/ask
  ai_narration: true                 # optional LLM rationale on recs (off = deterministic rule text)
  dqm:          true                 # Data Quality tab + /api/dqm
```

> `gold_schema` is **required in live mode** (the app refuses to boot in live mode without
> it — its reads are over account-wide system tables). With it set, the nightly
> `finops_gold_sync` job precomputes every estate page into `<gold_schema>.finops_gold` and
> the app serves each in ~1s. Blank `ingest_schema` ⇒ the admin-API-backed panels show a
> "run finops_admin_sync" prompt. See [Reference — the scheduled jobs](#reference--the-two-scheduled-jobs).

### Reference — demo or live?

|  | **Demo** (`use_live: false`) | **Live** (`use_live: true`) |
|---|---|---|
| Data | Seeded, deterministic sample (labelled **DEMO DATA**) | The customer's real `system.*` tables via their warehouse |
| Setup | None | `warehouse_id` + `SELECT` grants ([Reference — grants](#reference--grants-live-mode-only)) |
| Safety | Cannot touch real data | Read-only; **fail-closed** — a missing grant returns a clear error, never fake data mislabelled as real |
| Use for | Demos, sharing, buildathons | Deployed in the customer's own workspace |

Live is an explicit opt-in: it requires **both** `use_live: true` **and** a `warehouse_id`. An accidental deploy is always demo.

### Reference — feature flags (deploy with or without DQM / AI)

Turn whole capabilities on/off at deploy time — the tab disappears **and** its endpoint goes inert (404), so nothing half-renders. **The simple way** (bundle / UI path) is a one-line edit in `app.yaml` — set any of these to `"false"` (genie / dqm / writeback ship on; ai_narration's built-in default is off but this repo's config turns it on), then re-run the deploy cell from [Step 3 — Deploy it from a notebook](#step-3--deploy-it-from-a-notebook) or flip the same env var on the app page in the workspace UI:

```yaml
FINOPS_FEATURE_GENIE: "false"          # Ask Genie banner off
FINOPS_FEATURE_AI_NARRATION: "false"   # LLM rec-narration off (recs fall back to deterministic rule text)
FINOPS_FEATURE_DQM: "false"            # Data Quality tab off
FINOPS_FEATURE_WRITEBACK: "false"      # acknowledge/ignore off (strictly read-only)
```

No Python and no code edits — the flags are independent, so any combination can be turned off.

**If you use the `deploy.sh` wrapper instead** (the CLI path, needs Python), the same result comes from CLI switches or the `features:` block in a `customise.yaml`:

```bash
./deploy.sh --profile <P> --no-genie                                # genie=false (Ask Genie off)
./deploy.sh --profile <P> --no-narration                            # ai_narration=false (LLM rec-narration off)
./deploy.sh --profile <P> --no-ai                                   # genie=false + ai_narration=false (shortcut)
./deploy.sh --profile <P> --no-dqm                                  # dqm=false (no Data Quality tab)
./deploy.sh --profile <P> --no-narration --no-dqm                   # any subset — the switches are independent & composable
```

These `--no-*` switches just write the same `FINOPS_FEATURE_*` values into `app.yaml`.

The deterministic recommender, cost-attribution, and the **Genie $** cost view are **always on** regardless of flags (they call no model — see [Feature flags](#feature-flags--deploy-variants)).

### Reference — grants (live mode only)

Grant the **app's service principal** read-only access. **The warehouse grant is a
workspace-UI permission, not SQL** — a SQL warehouse is not a Unity Catalog securable, so
`GRANT ... ON WAREHOUSE` does **not** parse:

- **Warehouse `Can use`** — set in the UI: **SQL Warehouses → your warehouse → Permissions → add the app SP → Can use** (and tick the **Databricks SQL access** entitlement for the SP if it still can't query). Not settable in SQL.

The system-table reads *are* SQL. Minimum for the core cost/billing views:

```sql
GRANT USE CATALOG ON CATALOG system                            TO `<app-service-principal>`;  -- see the system catalog
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing            TO `<app-service-principal>`;  -- Overview, Workspaces, Genie $
GRANT USE SCHEMA, SELECT ON SCHEMA system.query              TO `<app-service-principal>`;  -- Query Advisor, idle %
GRANT USE SCHEMA, SELECT ON SCHEMA system.access             TO `<app-service-principal>`;  -- Tables, Adoption, Access
GRANT USE SCHEMA, SELECT ON SCHEMA system.information_schema TO `<app-service-principal>`;  -- Access grant graph, Governance
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow           TO `<app-service-principal>`;  -- Automated %, jobs
-- Only if features.dqm = true (Data Quality tab):
GRANT USE SCHEMA, SELECT ON SCHEMA system.data_quality_monitoring TO `<app-service-principal>`;  -- Data Quality
-- Only if writeback stays on in live mode with the Delta backend (see note below):
GRANT CREATE, MODIFY ON SCHEMA <writeback-schema> TO `<app-service-principal>`;  -- app's own finops_ack_status table
-- + USE CATALOG / USE SCHEMA on any user catalog you want the Tables page to DESCRIBE DETAIL.
```
> Reading a UC table needs **`USE SCHEMA` + `SELECT`** (and `USE CATALOG` on `system`); `SELECT` alone throws `INSUFFICIENT_PERMISSIONS … does not have USE SCHEMA`.
> The system-table `SELECT`s (and the two UI permissions above) require an account/metastore + workspace admin; the app's own SP is read-only and cannot grant them to itself.

> The app **fails closed**: if the SP is missing a grant, the affected tab returns a
> 503 with the exact `GRANT` to run — it never falls back to sample data in live mode.

**Ask Genie** additionally needs `CAN RUN` on the Genie Space. **Writeback** (on by default,
Delta backend): in live mode the acknowledge/ignore feature persists a single `finops_ack_status`
Delta table in a **writeback schema**, so set a schema (`FINOPS_GOLD_SCHEMA` **or** the dedicated
`FINOPS_WRITEBACK_SCHEMA`, both in `app.yaml`) and grant the **app's** SP `CREATE, MODIFY` on it. If
neither schema is set the app reports writeback **off** and hides the ack controls (it no longer
silently fails); the `-t customer-lakebase` deploy needs none of this (state lives in the bound
Postgres). **Serving layer** (if `gold_schema`
is set): the `finops_gold_sync` job's SP needs read on the system tables + `CREATE`/`MODIFY` on
`gold_schema` to write the gold tables the app reads. Everything the
system tables don't expose (budgets, budget policies, cluster-policy coverage, PATs,
alerts, SCIM groups, workspace assignments, DQM monitor configs) is **ingested by the
`finops_admin_sync` job** ([Reference — the scheduled jobs](#reference--the-two-scheduled-jobs)) into the Delta `<ingest_schema>.finops_admin` table — the app reads those
read-only, so its own SP does **not** need admin API access; the *job's* SP does. Full per-variant
grant matrix → [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

### Reference — the two scheduled jobs

There are **two** scheduled jobs, both shipped in `databricks.yml` **PAUSED**.

**`finops_gold_sync`** (`data/gold.py`, nightly 01:30) — precomputes every estate payload +
a per-table size census into `<gold_schema>.finops_gold` and `<gold_schema>.finops_gold_table_sizes`,
so the app serves each estate page in ~1s on a scale-to-zero warehouse. **Required in live mode** —
the app's reads are over account-wide system tables (tens of millions of rows), so live-per-request
isn't production-viable and the app refuses to boot in live mode without it. Enable it: set
`FINOPS_GOLD_SCHEMA` in `app.yaml` (key `gold_schema` on the CLI-wrapper path), grant its SP read on
the system tables + `CREATE`/`MODIFY` on `gold_schema`, then **unpause and run it once**. (Read-only
eval escape hatch, not for production: `FINOPS_ALLOW_LIVE_NO_GOLD=true` runs live off system tables.)

**`finops_admin_sync`** (`data/admin_sync.py`, daily 02:00) — pulls admin-API data the system
tables don't carry into the Delta **`<ingest_schema>.finops_admin` table** (keyed rows, each stamped
`ingested_at`, surfaced as "Last refreshed" in tooltips). It is the *only* way the
governance budget/policy/PAT/alert tiles, the Access groups + PAT panels, and the DQM
monitor configs get real data. Its service principal needs, **on top of** the app's
read-only system-table grants:

```
- Account admin (budgets, budget policies, SCIM groups, workspace assignments) +
  workspace admin (token management, alerts, quality monitors) — assign via the
  account console / workspace admin settings, not SQL.
- CREATE/MODIFY on <ingest_schema> to write the finops_admin Delta table.
```

Enable it: set `FINOPS_INGEST_SCHEMA` in `app.yaml` (or the matching key in `customise.yaml`
on the CLI-wrapper path), grant the SP as above, then **unpause** the job. Daily is the default
cadence; the app always falls back gracefully (on-demand scans / "run the job" prompts) when the
gold/admin tables are absent.

### Reference — AI features are auto-provisioned

So customers don't have to set anything up by hand:

- **Genie Space** — if `genie_space_id` is blank and `features.genie` is on, the deploy **creates a "Estate Command Center" Genie Space** grounded on **9 cost/usage/ops system tables** (`billing.usage`, `billing.list_prices`, `query.history`, `access.table_lineage`, `access.workspaces_latest`, `compute.warehouses`, `compute.clusters`, `compute.node_timeline`, `lakeflow.job_run_timeline` — see the [Ask Genie](#ask-genie) section) via the Genie `create_space` API. The app **auto-discovers it by title** ("Estate Command Center") on startup. Idempotent (dedupes by title prefix + syncs the table set on a reused space — never spawns duplicates). Runs at deploy time and again daily from `finops_admin_sync`. Set `genie_space_id` explicitly to use your own Space instead.
- **LLM endpoint** — when `features.ai_narration` is on, the deploy verifies the configured Claude endpoint (`llm_endpoint`) exists and is ready, and reports the exact fix if not.

Both are non-fatal: a provisioning gap degrades the AI feature gracefully, never blocking the deploy.

### Reference — verify (preflight + honest labelling)

- **`GET /api/health`** runs a preflight in live mode: is the warehouse reachable, and can the app read each required system table? It returns a per-check report, so a missing grant surfaces **on boot**, not mid-demo.
- The header shows a green **● LIVE DATA** badge in live mode (amber **DEMO DATA** otherwise), plus a banner naming exactly which tabs read live data vs still-sample.
- Every API payload carries `demo` / `mode` / `live_pending`, so real and sample numbers are never confused.

### Data Quality Monitoring — enabling & configuring

The **Data Quality** tab is a Lakehouse-Monitoring inventory with a FinOps cost tie-in ("which monitors am I paying for, and are they earning their keep?"). It's fully **deploy-time optional**:

- **Turn it on/off:** set `FINOPS_FEATURE_DQM` to `"true"`/`"false"` in `app.yaml` (or `features.dqm` in a `customise.yaml` / `--no-dqm` on the CLI-wrapper path). When off, the tab **and** `/api/dqm` are absent — inert server-side, not just hidden — so a customer who doesn't use Lakehouse Monitoring never sees it.
- **What it reads (live mode):** quality/freshness results from **`system.data_quality_monitoring.table_results`**, joined to the **`DATA_QUALITY_MONITORING`** line in `system.billing.usage` for the cost tie-in. Monitor *configs* (type, schedule) come from the `finops_admin_sync` ingest (`GET /api/2.1/unity-catalog/tables/{full_name}/monitor`, which `information_schema` doesn't fully expose).
- **Extra grants (only when `dqm: true` + live):** the app SP needs `SELECT` on `system.data_quality_monitoring` (used only by the Data Quality tab; the tab degrades gracefully to a calm info state if it's not granted).
- **What the customer sees:** monitor inventory (type, quality, drift PSI/KS, null %, anomalies, freshness vs cadence, alerts), the **`DATA_QUALITY_MONITORING` cost by workspace**, and a **"prune monitors on low-value tables"** panel — stale/quiet monitors surfacing nothing actionable, with the `$/mo` reclaimed by pausing them.
- **Demo mode:** ships 12 representative monitors so the tab is meaningful without any monitors configured.

### What reads live vs how

**In live mode there is no sample data** — every panel is either a real system-table read or an ingested-admin-API read, and anything unavailable fails closed (a clear 503, never fabricated numbers). Each panel was validated against real system tables before it shipped.

| Panel | Live status |
|---|---|
| **Genie $ · AI $ · Cost-drivers · Workspaces · Overview · Data Quality · Governed tags + Data Classification · Adoption & Value · Query Advisor · Tables (health) · Recommendations · Access grant graph** | ✅ **Live** — validated against real `system.*` data (billing, `data_quality_monitoring`, `information_schema` incl. tag + `class.*` classification tags, `query.history` through the real recommender, `access.table_lineage`, `table_privileges`). Table **health** is live from bulk signals (freshness + usage recency; DQM is a separate tab, not folded in); file-hygiene (small files / OPTIMIZE) is deferred to DESCRIBE-on-drill-down. Idle % is a labelled estimate from `query.history`. Recommendation savings are labelled estimates. |
| **Governance budget/policy/PAT/alert tiles · Access groups + PAT inventory · DQM monitor configs** | ✅ **Live via the `finops_admin_sync` job** — these come from admin REST APIs (not system tables), so a daily job ingests them into the Delta `<ingest_schema>.finops_admin` table and the app reads them as real data with a "last refreshed" time. Until the job runs, these panels fail closed with a "run finops_admin_sync" prompt (never sample). |

There is **no sample data in live mode** — every panel is either a system-table read or an ingested-API read; anything not available fails closed. The fail-closed executor lives in [`data/live.py`](data/live.py).

### What gets deployed into the customer workspace

| Object | Type | Cadence | Purpose |
|---|---|---|---|
| `estate-command-center` | Databricks App | always-on | The app itself (`USE_LIVE=true`) |
| Serverless SQL warehouse | Compute (customer-provided) | on-demand | System-table reads + gold-table lookups |
| `<gold_schema>` (e.g. `main.finops_gold`) | UC schema — `finops_gold` + `finops_gold_table_sizes` Delta tables | — | Nightly-precomputed estate payloads + table-size census, served in sub-second time |
| `<ingest_schema>` (e.g. `main.finops_cc`) | UC schema — `finops_admin` Delta table | — | Grant target for the admin-sync SP; holds ingested admin-API data |
| **`finops_gold_sync`** | Job — `data/gold.py` | **nightly** | Precomputes every estate payload + per-table size census → `<gold_schema>.finops_gold` + `finops_gold_table_sizes` |
| **`finops_admin_sync`** | Job — `data/admin_sync.py` | **daily** | Pulls budgets, budget policies, cluster-policy coverage, tokens, alerts, SCIM groups, workspace assignments, DQM monitors → Delta `<ingest_schema>.finops_admin` |
| Model Serving / FMAPI endpoint | Serving endpoint | on-demand | **Only if AI narration is enabled** (Claude); otherwise not deployed |

This is **two scheduled jobs** — `finops_gold_sync` (nightly serving-layer precompute) and `finops_admin_sync` (daily admin-API ingest). Both ship **PAUSED** — unpause after the schemas + grants are set. `finops_admin_sync` needs a service principal with **account admin** (budgets, budget policies, SCIM groups, workspace assignments) + **workspace admin** (token management, alerts, quality monitors) — heavier than the app's read-only system-table access; provision a dedicated SP. See [`data/admin_sync.py`](data/admin_sync.py).

### What this costs to run

Rough **US list price**; multiply by your cloud/tier rate. The app is a lean single-container app, so the dominant cost is the always-on container — everything else is small and scales to zero.

| Component | Sizing | ~ Cost (US list) | Nature |
|---|---|---|---|
| **App container** | `compute_size: MEDIUM` = **0.5 DBU/hr** × 720 hr × ~$0.75/DBU | **~$270 / mo** always-on | Fixed, **not** usage-driven (does not auto-idle). ~$16/mo if you stop it nights/weekends. |
| **Serverless SQL warehouse** | scale-to-zero; system-table reads + gold lookups | **~$1–20 / mo** | Variable; near-zero at rest. Heavy interactive use lands in the low tens of $. |
| **`finops_gold_sync` + `finops_admin_sync` jobs** | serverless, a few minutes nightly/daily | **~$10–20 / mo** | Fixed, small. |
| **Writeback (acknowledge/ignore)** | Default: a tiny Delta table via the read warehouse | **$0** added | Always on. Uses the existing warehouse — no new infra. |
| **Lakebase (optional writeback backend)** | Only if deployed `-t customer-lakebase`; smallest tier (CU_1) managed Postgres | **$0 unless opted in** (then a **CU_1 instance billed while running**, order of tens of $/mo — stop it to pay nothing) | Opt-in premium path for low-latency writeback at scale. NOT created on the default deploy; the free Delta backend is the default. |
| **DQM** | read-only reads (if `dqm` on) | **$0** added | No extra compute; the tab surfaces existing `DATA_QUALITY_MONITORING` spend. |

**AI OFF (default) — all-in ≈ $290–320 / mo** (container + warehouse + jobs). This is the recommended baseline: recommendations, cost attribution, and all observability work with zero model spend.

**AI ON — add the pay-per-token / PAYGO items on top:**

| AI feature | When it bills | ~ Cost (US list) |
|---|---|---|
| **AI narration + AI SQL rewrite** (`ai_narration=true`) | FMAPI/Model-Serving, **per call**, only on drill-down / "Regenerate" | ~$0.01–0.05 per call → **~$5–15 / mo** at typical use |
| **Ask Genie** (`genie=true`) | Genie PAYGO DBUs, **only when a user asks** | **a few–tens of $ / mo**, usage-dependent |

So a full **AI-ON** deployment is roughly **$300–350 / mo** at typical usage — the AI features add tens of dollars, not hundreds, because they bill per-call rather than always-on.

> **Note on the internal FEVM demo numbers:** on Databricks' shared Field-Engineering demo workspace, apps bill across multiple regions concurrently (an attribution artifact — e.g. a `hello-world` app appears to consume ~46 DBU/hr there), so raw FEVM billing over-states the container cost by ~3×. The **`compute_size`-based figure above (MEDIUM = 0.5 DBU/hr ≈ $270/mo) is the representative customer cost**, matching the public [Databricks Apps pricing](https://www.databricks.com/product/pricing) table.

### Serving layer — gold tables (required for live mode)

Estate-wide live reads (all-product billing, the Access grant graph, table
stats) scan large system tables, which is slow per-request on a big account.
The **gold tables** fix this: the nightly **`finops_gold_sync`** job precomputes
every estate payload — plus a per-table **size census** (metadata-only: it
iterates `information_schema.tables` → `DESCRIBE DETAIL`, no data scan) — into
`<gold_schema>.finops_gold` and `<gold_schema>.finops_gold_table_sizes`. The app
then serves each estate page as a keyed lookup against those Delta tables via a
small **serverless SQL warehouse that scales to zero** between reads — **sub-second,
no per-request system-table scan**. Day-old data is fine for FinOps trending.

Enable it:
1. Set `FINOPS_GOLD_SCHEMA` in `app.yaml` (key `gold_schema` on the CLI-wrapper
   path), e.g. `main.finops_gold`.
2. Grant the `finops_gold_sync` job's service principal read on the system tables
   the app reads + `CREATE`/`MODIFY` on `gold_schema`.
3. Unpause `finops_gold_sync` (ships `PAUSED`, nightly). No extra Python deps
   (just `databricks-sdk`).

**Admin-API ingestion** (`finops_admin_sync`) is the second scheduled job — for data
not in system tables (budgets, policies, tokens, SCIM groups, DQM monitors):
1. Set `FINOPS_INGEST_SCHEMA` in `app.yaml` (key `ingest_schema` on the CLI-wrapper path).
2. Grant its SP **account + workspace admin** (see the inventory above) plus
   `CREATE`/`MODIFY` on `ingest_schema` to write the `finops_admin` Delta table.
3. Unpause `finops_admin_sync` (ships `PAUSED`, daily). Each row carries
   `ingested_at`, surfaced as "Last refreshed" in the relevant tooltips.

Gold-table rows carry their precompute timestamp so the UI can show "as of". In
live mode `gold_schema` is **required** (the app won't boot without it); the app
reads from the precomputed tables and falls back to a direct scan only transiently
(a missing/stale key between nightly runs), never as the steady-state serving path.
This is also what makes the **Access grant graph** practical to serve live.

### Productionalising — the operational checklist

- **Security.** Read-only by construction (only `SELECT`/`DESCRIBE`/read APIs run). Auth is the app's **own service principal** via the SDK credential chain — no PATs, no user tokens baked in. Grant least-privilege (`SELECT` on the system schemas only). Fail-closed means a permission gap is an honest error, never fabricated data.
- **Performance & cost.** The app's reads are over **account-wide** system tables (every workspace in the account, tens of millions of rows), so the **gold-tables serving layer is required in live mode** — set `gold_schema` + unpause `finops_gold_sync` (nightly precompute → ~1s keyed reads, no per-request scan). Use a **serverless** warehouse (scales to zero) for both the app and the job. Big result sets are already capped in the UI (e.g. the Genie tables show top-N with a count). The app refuses to boot in live mode without a gold schema (read-only-eval escape hatch: `FINOPS_ALLOW_LIVE_NO_GOLD=true`, slow).
- **Isolation.** One deploy = one workspace = that workspace's own data via its own SP. There is no cross-customer data path; each deployment is self-contained.
- **Upgrades.** To ship a new version, pull the latest code into the Git folder and re-run the deploy cell (`databricks bundle deploy -t customer`), or re-run the bundle from your CLI. Config lives in `app.yaml` (external to the code), so upgrades don't disturb per-customer settings.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `/api/health` shows a table `ok: false` | Grant the app SP `SELECT` on that schema ([Reference — grants](#reference--grants-live-mode-only)). |
| Live tab shows a 503 "live read failed" | The warehouse is stopped or the grant is missing — the message names the exact object. |
| `use_live: true` but data is still demo | If deploying via the bundle/UI, set `USE_LIVE`/`DATABRICKS_WAREHOUSE_ID` directly in `app.yaml`. If using the `deploy.sh` wrapper, it couldn't parse `customise.yaml` (no PyYAML on its `python3`) and fell back to defaults — run it with a `python3` that has `pyyaml`, or set the env directly in `app.yaml`. |
| "Workspace has reached the maximum limit of 300 apps" | The target workspace is full — deploy to another workspace (system-table billing is account-wide, so any workspace in the same account sees the same usage). |
| Tabs slow | Confirm the `finops_gold_sync` job has actually run — the app serves from the precomputed gold tables, so a page is only slow if gold is missing/stale (or you set the `FINOPS_ALLOW_LIVE_NO_GOLD` eval escape hatch, which scans live). |
| App won't boot in live mode ("LIVE MODE REQUIRES A GOLD SCHEMA") | Set `FINOPS_GOLD_SCHEMA` and redeploy — required in live mode. Read-only eval only: `FINOPS_ALLOW_LIVE_NO_GOLD=true`. |
| `npm install` / `pip install` stalls | The bundle/UI deploy needs **neither** — `frontend/dist` is committed. For local dev, registries are proxied here — reuse the checked-in `frontend/node_modules` and `.venv`. |

---

## Local development

```bash
# 1. Backend — API serves demo data by default. Reuse the checked-in venv if present.
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # registries are proxied — see Known limitations
uvicorn app:app --reload --port 8000

# 2. Frontend (another shell) — Vite proxies /api → :8000. Reuse node_modules if present.
cd frontend
[ -d node_modules ] || npm install
npm run dev                            # http://localhost:5173

# Production build (then FastAPI serves the SPA at http://localhost:8000):
npm run build
```

> **Reuse existing installs.** `.venv/` and `frontend/node_modules/` are typically already populated in this repo — skip the install step if they are, to avoid hitting the proxied registries unnecessarily. See [Known limitations](#known-limitations) for the registry-proxy note.

---

## Customisation

- **Demo data:** everything the UI shows in demo mode comes from `data/generate_demo.py` (seeded, deterministic). Edit the seed data there — the KPI rollups and recommendation feed recompute from it, so numbers stay reconciled across pages.
- **Recommendation catalogue:** add or tune operator plays in `reference/recommendation_catalog.yaml` (24 plays × 6 categories). Each play declares its trigger, impact, effort, and feature pills; the hub matcher + scorer in `data/rec_hub.py` picks it up automatically.
- **Feature flags:** flip `features.genie` / `features.ai_narration` / `features.dqm` in `config.yaml` (or the matching env var — env wins) to pick a [deploy variant](#feature-flags--deploy-variants); hidden features are inert server-side. Set `genie_space_id` to point Ask Genie at your own Genie Space (blank ⇒ auto-create + auto-discover the FinOps Space in live; demo mode uses canned answers).
- **Currency:** every page supports an AUD toggle alongside USD (list-price USD × `fx_rate` in `config.yaml`, clearly labelled).
- **Theming:** CSS-var tokens in `frontend/src/index.css` + `frontend/tailwind.config.ts` (surface / card / border / accent, dark by default).

---

## Roadmap

- **Validate the admin-API ingest in a real account.** Every panel is wired live and validated against real system tables. The one piece that can only be exercised where the deploying SP is **account + workspace admin** is the `finops_admin_sync` job (budgets, SCIM, tokens, etc.) — the code is built against the confirmed SDK surface and fails safe, but wants one end-to-end run in such an environment.
- **Global filter bar** — a single workspace + time-range filter that scopes every page at once (today each page filters independently).
- **CSV export** — download the current (filtered) table to CSV; a common ask for cost data.
- **Genie spend fills out** — [Genie cost attribution](#genie-cost-attribution) reads light until consumption-based Genie usage flows after 6 Jul 2026; the split will populate as real usage lands.

---

## Known limitations

- **Demo data is synthetic.** The default dataset is seeded, Acme-*shaped* fiction — labelled `DEMO DATA` throughout. Do not read the numbers as real Acme consumption. Live mode is the source of truth.
- **PyYAML for the full catalogue.** The recommendation catalogue parses best with **PyYAML** (`pyyaml>=6.0`, in `requirements.txt`). `app.py` has a minimal fallback line-parser so the app still boots without PyYAML, but the full `recommendation_catalog.yaml` (and the `recommender_policy` block) need PyYAML installed to load completely.
- **Registry proxy.** This environment proxies package registries (npm / PyPI). If `pip install` / `npm install` stalls or 403s, the venv and `node_modules` in the repo are usually already populated — reuse them rather than reinstalling.
- **No sample data in live mode.** Every panel is a real system-table read or an ingested-admin-API read; anything unavailable fails closed with a clear 503 (never fabricated numbers). Admin-API panels (governance budget/policy/PAT/alert tiles, Access groups + PATs, DQM monitor configs) depend on the `finops_admin_sync` job — until it runs they show a "run finops_admin_sync" prompt. Idle % and recommendation savings are labelled estimates; BU collapses to workspace unless the customer tags for it.
- **Ask Genie: real in live, canned only in demo.** In **live** mode the app calls the real Genie Conversation API against an auto-provisioned/auto-discovered Genie Space (no `genie_space_id` needed) and **never** serves a canned answer — if a Space isn't configured or Genie can't answer it says so honestly. Canned, seeded `demo answer` responses appear **only in demo mode** (`USE_LIVE=false`).
- **Genie spend split is real-but-undocumented and reads light.** The [Genie surface split](#genie-cost-attribution) (`usage_metadata.genie.surface`: `GENIE_CODE` / `GENIE_ONE` / `GENIE_AGENTS`) is populated in the billing data but **not yet in the published schema doc**, so treat as may-change; the app labels whatever values appear and degrades to per-user/workspace totals if null. The free 150 DBUs/user/month are shared across surfaces (per-surface *paid* `$` is an estimate), budgets can't be split by surface, `$` is list-price reference (Account Console is source of truth), and numbers read light until consumption usage flows after 6 Jul 2026.
- **DQM shows representative monitors in demo mode.** The [Data Quality](#data-quality) tab ships 12 demo monitors; live mode detects real ones from Lakehouse Monitoring metadata. With `features.dqm=false` the tab and `/api/dqm` are absent entirely.
