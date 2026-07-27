# FinOps Command Center — Build Spec (original build blueprint)

> ⚠️ **Historical build spec, not current-state docs.** This is the original
> blueprint the app was built from; the shipped app has since moved on. Notably:
> the in-region/`in_geo_matrix.yaml`/`region.yaml`/`/api/in-geo` concept was
> dropped (no region gating), the nav grew to ~14 tabs (not the 7 listed here),
> and writeback (acknowledge/ignore) was added — on by default, backed by a Delta
> table by default with an optional bundle-native Lakebase backend
> (`-t customer-lakebase`). For current behaviour see **README.md**,
> **docs/ARCHITECTURE.md**, **docs/DEPLOYMENT.md**, and **docs/SECURITY.md** —
> those are the source of truth. This file is kept for design context.

**Databricks-native cost-control co-pilot.** One stop shop that consolidates every cost/usage tool we've built (query observability, workspace health check, GenAI cost governance, cost-by-driver hub, consumption dashboards) into a single app with **access**, **workspace analysis**, **query advisor (AI)**, **storage/table optimization**, and **governance** — every recommendation gated to **Australia (australiaeast / ap-southeast-2) in-region availability**.

Every agent working on this repo MUST read this file first and conform to it. Match the **GridSense Intelligence Hub** design system and repo conventions (`~/projects/gridsense-intelligence-hub`).

---

## 1. Tech stack (match GridSense exactly)
- **Frontend:** React 18 + Vite + TypeScript + Tailwind (darkMode: "class") + Zustand (state) + Recharts (charts) + react-markdown. DM Sans font.
- **Backend:** FastAPI (`app.py`) serving `/api/*` + mounting the built frontend `dist/` via StaticFiles.
- **Deploy:** Databricks App (`app.yaml`, `databricks.yml`, `deploy.sh`, `requirements.txt`). `region.yaml` for region config.
- **Data:** ships with a demo-data generator (`data/generate_demo.py`) producing realistic Acme-shaped data, AND the real system-table SQL wired behind a `USE_LIVE=false` flag. Swap to `databricks-sql-connector` against system tables when deployed. Never fabricate live numbers in the UI — demo data is clearly labelled "demo".

## 2. Design system (copy from GridSense `frontend/tailwind.config.ts` + `src/index.css`)
- CSS-var theming: `--color-surface`, `--color-card`, `--color-border`, `--color-brand-dark`, `--color-neutral`, `--color-accent`. Dark default.
- **Palette for this app:** near-black surface `13 17 23`, card `22 27 34`, border `48 54 61` (GitHub-dark base, same as GridSense dark). Accent = Databricks red `255 54 33`. Semantic: success `#1B8A4A`, warning `#F59E0B`, danger `#C0392B`, info/purple `#8B5CF6`. Insight-type pill colors: cluster=teal, resize=purple, rewrite=blue, vacuum=amber, convert=red, enable-PO/LC=green.
- Components: `.card`, `.pill`, KPI stat cards (icon + label + big number, colored left border by status), dense data tables with sortable columns, filter bar (time-range chips + dropdowns + search + threshold), status pills, progress bars (automated %, savings %), sparklines.
- Layout: top nav bar (logo "FinOps Command Center" + page tabs + dark-mode toggle), each page = KPI row → filter bar → main table/detail. Reference the two screenshots: query observability (query table + AI insight column + KPI row + filters) and workspace health check (workspace table + KPI row + faceted filters + health/complexity/optimization pills).

## 3. Pages (nav tabs)

### 3.0 Overview (Dashboard)
Global KPIs across all workspaces: Total spend/mo, Savings potential ($ + % of spend), Total DBUs, #Workspaces, #Need attention (critical), Self-optimize vs Partner/PS split. Trend chart (spend + savings over time). Top savings opportunities (cross-page: worst queries, worst external tables, most over-privileged principals). All figures filterable by workspace + time range.

### 3.1 Access  (page: "Access")
**NO AI recommendations on this page** — it is a factual access/grants explorer, not an advisor.
Two first-class, **bidirectional** modes (a toggle at the top of the page):
- **Data-centric ("who can access this?"):** filter by **workspace → catalog → schema → table** (cascading dropdowns) and see **every grant on that securable** — principal, privilege (USE/SELECT/MODIFY/ALL/…), direct vs inherited, plus any row filter / column mask / ABAC policy in effect.
- **Principal-centric ("what can this principal access?"):** filter by **group / user / service principal** and see **all their entitlements** (workspace access, cluster-create, sql-access, etc.) **and every object grant they hold** (which catalogs/schemas/tables/volumes/functions, which privilege, direct or via which group).
Both modes share the same filter bar; switching mode swaps rows-vs-columns of the same grant graph. Group membership (incl. nested groups) is expandable so a user's *effective* access resolves through their groups.
Answers **"what does each AD group / user / SP have access to?"** and the reverse — every capturable detail.
- **Principal directory:** users, service principals, groups (account + workspace-local); source (AIM / SCIM / local); status; last-active; #entitlements; #grants.
- **Group membership tree** (nested groups expanded), member counts, AD group → Databricks group mapping.
- **Entitlements matrix:** per principal — workspace access, cluster-create, databricks-sql-access, allow-cluster-create, etc.
- **Unity Catalog privileges:** effective grants per principal × securable (metastore/catalog/schema/table/volume/function), showing privilege (USE/SELECT/MODIFY/ALL PRIVILEGES/etc.), whether inherited vs direct, plus ABAC policies, row filters, column masks in effect.
- **Workspace assignments:** which workspaces each group/principal is assigned to + which permission (USER/ADMIN).
- **Compute access:** SQL warehouse ACLs, cluster policy grants, per principal.
- **Two bidirectional views:** (a) "What can this principal access?" (b) "Who can access this securable?" (data-centric).
- **Risk flags:** over-privileged principals (MODIFY/ALL on prod), grants direct-to-user (should be via group), broad `account users`/PUBLIC grants, admin sprawl, stale/unused grants, PAT/token inventory + age, external-location write access.
- **Filters:** workspace, group, principal type, privilege, risk flag, catalog.
- **Data model:** `system.information_schema.*_privileges` (table/schema/catalog/routine privileges), `system.access.audit` (last-access), account SCIM/groups API + workspace-assignments API (documented; demo data provided). Effective-privilege resolution documented.

### 3.2 Workspaces  (page: "Workspaces")
Workspace-by-workspace analysis (mirrors workspace health check).
- **Table columns:** Workspace, Customer/BU, Spend/mo, Savings/mo, Savings %, Automated %, Health (Good/Warning/Critical), Complexity (Simple/Moderate/Complex), Optimization (Self-Optimize / Partner-PS), trend sparkline. Sortable, faceted filters (customer, complexity, health, optimization) + search — exactly like screenshot 2.
- **Health rule:** Good ≥60% automated, Warning 25–60%, Critical <25% (automated = scheduled-jobs compute vs interactive). Savings = f(health score, automation gap, idle, non-serverless share).
- **Drill-down (per workspace):** product/SKU mix (serverless / DLT-Lakeflow / jobs-classic / all-purpose / SQL warehouse / model serving / apps / FMAPI / PO), % each; monthly trend; top users by $; top jobs by $; top warehouses by $; DBU total; idle %; region; #clusters; #warehouses; serverless share.
- **Data model:** `system.billing.usage` × `system.billing.list_prices` grouped by `workspace_id` + `sku_name` + `billing_origin_product`; `system.query.history` (top users/warehouses); `system.lakeflow.*` (jobs). Automated % from job-run vs interactive compute split.

### 3.3 Query Advisor  (page: "Query Advisor" — query observability)
The AI-insight query table (screenshot 1).
- **KPI row:** Runs, Critical, Compute-h, Est. Cost, Biggest Spill, Applied.
- **Table columns:** # / Impact (score bar) / Query (truncated + expand) / **AI INSIGHT** (pill: cluster|resize|rewrite|vacuum + one-line rationale) / SRC / Warehouse / User / Runs / p95 / Cost / Flags (count) / Actions (view SQL, apply). Expandable rows show full SQL + full insight + estimated savings + next steps.
- **Filters:** time range (1h/6h/24h/7d/custom), All warehouses ▾, All workspaces ▾, search (queries/users/warehouses), p95 threshold slider, flags dropdown. All in-geo aware.
- **Flags:** slow, high-spill, capacity-bound, full-scan, small-files, skew, cross-region.
- **AI recommendation engine** (§4).
- **Data model:** `system.query.history` (query_text, statement metrics: bytes read/pruned, spill, duration p50/p95, rows, warehouse_id, executed_by, statement_id, run count) × billing for $.

### 3.4 Storage & Tables  (page: "Tables")
Managed vs external tables + optimization economics.
- **Split KPIs:** #Managed vs #External tables, % managed, external-table $ optimization opportunity, storage reclaimable (GB + $), tables needing OPTIMIZE / VACUUM / clustering.
- **Table list:** catalog.schema.table, **type (MANAGED / EXTERNAL)**, sizeInBytes, numFiles, avg file size, small-file ratio, last OPTIMIZE, last VACUUM, partitioned?, clustered?, PO enabled?, LC enabled?, scan $/mo, **est. savings from OPTIMIZE+VACUUM**, **recommendation**.
- **For EXTERNAL tables:** flag those needing OPTIMIZE/VACUUM; estimate $ saved (storage reclaim + scan reduction); **path to convert to managed** (CTAS / DEEP CLONE steps) and the benefits unlocked (Predictive Optimization auto OPTIMIZE/VACUUM/ANALYZE, Liquid Clustering, catalog-managed commits); **next steps** per table.
- **For MANAGED tables without PO/LC:** recommend enabling Predictive Optimization + `CLUSTER BY AUTO`, with expected benefit (cite: PO ~50% storage savings + >2x query speed; LC replaces partitioning, ~95% faster scans on over-partitioned tables). Include the DLT `cluster_by_auto` known-issue caveat (needs DBR 15.4 LTS+, PO enabled; a known validation bug in Declarative Pipelines — link to raising an Azure support case).
- **Savings formulas:** OPTIMIZE saving ≈ scan_reduction_from_compaction × query_cost_on_table; VACUUM saving ≈ stale_bytes × storage_$/GB/mo; convert-to-managed unlocks PO ⇒ ongoing auto-maintenance cost avoidance. All estimates labelled "estimated", assumptions shown.
- **Data model:** `system.information_schema.tables` (table_type), `DESCRIBE DETAIL` (numFiles, sizeInBytes, clusteringColumns, partitionColumns), predictive-optimization system tables, `system.storage` where available.

### 3.5 Governance  (page: "Governance")
Cost-governance posture + controls.
- Tagging coverage % (by workspace + overall), untagged spend $; budget-policy count + coverage; PO adoption %, LC adoption %; certification %; lineage coverage; **external/unmanaged data %** (ties to Tables page); cluster-policy coverage; PAT/token risk; over-privileged principal count (ties to Access); region-compliance (all compute in AU?); alert/budget setup status; audit anomalies. Each as a scored tile (Good/Warning/Critical) with the specific gap + next action.

### 3.7 Recommendations  (page: "Recommendations" — the unified AI advisor hub)  ★ PRIMARY ADVISOR SURFACE
This is the **full** AI advisor. Exception: the **Overview page keeps its "Top savings opportunities" rollup** (a ranked teaser of the highest-impact recs that deep-links into this tab) — that stays. All *other* pages stay recommendation-free; the depth lives here. It aggregates recs from Query Advisor, Tables, Governance, Access-cost, and adds **platform-operator strategic plays**. Grounded in `reference/recommendation_catalog.yaml` (real Databricks features + triggers + impact + AU in-geo status).
- **Two views:** (a) **Overall** — all recs ranked by priority/savings across the estate; (b) **Per-workspace** — filter to one workspace. Filters: workspace ▾, **priority (P1/P2/P3)**, category, scope, in-geo.
- **Recommendation altitudes (think like an operator):**
  1. **Behavioural / enablement:** e.g. "Team/BU X ran N inefficient queries ($Y), mostly Z users → stand up a governed **Genie Space** for self-serve; add a **cluster policy**/query timeout; targeted enablement." Power-user coaching. Right tool for the wrong pattern.
  2. **Compute:** serverless migration (Jobs/SQL), auto-stop/right-size idle warehouses, Photon, pools/spot, materialized views + result cache for repeated BI, old-runtime → versionless serverless.
  3. **Storage:** external→managed conversion, enable Predictive Optimization + Liquid Clustering / CLUSTER BY AUTO, OPTIMIZE/VACUUM.
  4. **Tagging & cost attribution:** untagged-spend rollup, **budget policies** (serverless attribution), tag taxonomy (BU/Project/Env), chargeback/showback, Account Budgets + threshold alerts.
  5. **AI/GenAI spend:** Unity AI Gateway budgets + rate limits (AU Beta — flag), FMAPI cost controls.
  6. **Access-cost/governance:** over-privileged prod access, missing budgets/alerts, region non-compliance.
- **Each recommendation:** priority pill, category pill, **title (operator-facing)**, scope (global/workspace/BU/team/user), **est $/mo savings**, effort (Low/Med/High), **evidence chips** (the metrics that fired it, e.g. "412 queries · 3 users · $4.2K"), **"What to do"** concrete steps, recommended feature pills (each with AU in-geo status GA/PuPr/gated), suggested owner. Expandable. Only surfaces AU GA/PuPr features; anything else flagged "not yet in AU region".
- **Cost-attribution panel:** spend by workspace / BU / tag / user; untagged %; top cost drivers; the specific tagging + budget-policy plays to close attribution gaps.
- **Priority model:** P1 = high $ impact × low effort × (or high risk); score = normalise($/mo × frequency/severity ÷ effort). Sort default by priority then $.
- **Data model:** aggregates `/api/recommendations` (query-level, from recommender.py), `/api/tables` savings, `/api/governance` gaps, `/api/access` risks, plus a `catalog`-driven strategic-play generator that matches triggers (per-BU inefficient-query counts, untagged-spend %, interactive-share, idle warehouses, external-table $, over-privilege counts) against `recommendation_catalog.yaml`. New endpoint `/api/recommendations/hub` returns the unified, scored, filterable list + attribution rollup.
- **Query Advisor page** stays as query-level *observability* (the table) and feeds this hub; it no longer needs its own recommendations column emphasis — the deep advice lives here.

### 3.6 In-region (Australia) — INTERNAL constraint, NO user-facing tab
**Remove the "In-Region" nav tab and `InGeoPage`** (built in iteration 1). In-region is enforced internally, not shown as a page:
1. **Recommender gating:** the recommendation engine only surfaces features that are GA/PuPr in australiaeast per `reference/in_geo_matrix.yaml`; anything PrPr/NOT_IN_REGION is suppressed (or, where already in use, flagged inline). Keep the matrix file as an internal reference — just don't render it as a tab.
2. **The app's OWN model calls must be in-region:** any LLM narration (optional, for rec rationale) MUST call a **Foundation Model API Claude endpoint hosted in australiaeast** (fmapi_claude = GA in AU East). Make the model endpoint a backend config value defaulting to an AU-region endpoint; never call a non-AU model. If no AU model endpoint is configured, fall back to deterministic rule-templated text — do NOT silently call an out-of-region model.
**Nav tabs (final):** Overview · Access · Workspaces · Query Advisor · Tables · Governance · **Recommendations**. (In-Region removed.)

## 4. AI recommendation engine (`data/recommender.py` + `/api/recommendations`)
Deterministic, explainable rules first (LLM narration optional, in-geo-gated). For each query / table / warehouse produce: `{insight_type, rationale, est_savings_usd_month, confidence, next_steps[], in_geo_ok}`.
- **cluster (Liquid Clustering):** pruning_efficiency < 30% AND bytes_read large ⇒ "Apply Liquid Clustering on filter/join columns." savings ∝ runs × cost × expected_prune_gain.
- **resize:** spill_gb high vs warehouse memory ⇒ "Insufficient memory — resize warehouse up / split." savings from avoided spill re-reads.
- **rewrite:** SELECT * / low projection / low pruning on high read ⇒ "Rewrite: project columns, add predicates." 
- **vacuum / optimize:** many small files / stale versions ⇒ "Run OPTIMIZE + VACUUM."
- **convert-to-managed:** external table ⇒ "Convert to UC managed to unlock auto-maintenance." 
- **enable-PO / enable-LC:** managed table without PO/LC ⇒ "Enable Predictive Optimization + CLUSTER BY AUTO."
Impact score 0–100 = normalized($ savings × frequency × severity). Optional LLM (Databricks FMAPI Claude) writes the human rationale — only if in-geo (§5); else rule-templated text.

## 5. In-region (Australia) feature matrix  (`reference/in_geo_matrix.yaml`)
Region: **australiaeast (Azure)** primary; ap-southeast-2 (AWS) secondary. Status ∈ {GA, PuPr, PrPr, NOT_IN_REGION}. The recommender/UI only surface GA/PuPr and flag the rest. Seed values (verify at build; store source + date):
- Serverless SQL Compute — **GA**
- Serverless Jobs / Notebooks — **GA**
- Serverless DLT / Lakeflow — **GA**; Standard (cost-optimized) mode — **PuPr**
- Predictive Optimization (UC managed) — **GA**
- Liquid Clustering / `CLUSTER BY AUTO` — **GA** (auto-LC in Declarative Pipelines has a known validation bug; DBR 15.4 LTS+)
- Photon — **GA**
- Budget Policies (serverless tagging) — **PuPr**
- Account-level Budgets — target GA Apr FY27 (treat as PuPr)
- Unity AI Gateway budgets — **PuPr / rolling out**
- Automatic Identity Management (Entra ID) — GA on AWS/GCP; **verify Azure AU** status
- Genie / AI-BI — **GA** (ANZ)
- Foundation Model APIs (Claude) in AU East — **verify**; if not GA in-region, LLM narration falls back to rule text
- System tables (billing, query, access, lakeflow, information_schema) — **GA**
- Model Serving — **GA**
Build task: an agent verifies each row against current docs/Glean/DBRA and writes the matrix with citations + date.

## 6. API surface (FastAPI)
`/api/config`, `/api/overview`, `/api/access/principals`, `/api/access/grants`, `/api/access/risks`, `/api/workspaces`, `/api/workspaces/{id}`, `/api/queries`, `/api/recommendations`, `/api/tables`, `/api/tables/{fqn}`, `/api/governance`, `/api/in-geo`. Each reads demo data (default) or live system tables (`USE_LIVE=true` + warehouse). Filters via query params (workspace, warehouse, time_range, etc.).

## 7. README (match GridSense style)
Centered title + badges (Databricks App / Stack / in-geo AU / Foundation Models), one-line pitch, contents TOC, "What is it", screenshots/GIF placeholders, key metrics, **Architecture** (mermaid), Capabilities per page, Tech stack, Data requirements (exact system tables + grants needed), Deploy to Databricks, Local development, Customisation, In-region notes, Roadmap, Known limitations. Plus `docs/ARCHITECTURE.md` with mermaid data-flow.

## 8. Build plan (coordinated agents + iterations)
- **Foundation (iteration 1):** scaffold repo from GridSense conventions; design tokens; backend skeleton + demo-data generator + data-model SQL; App shell + nav + shared components (KPI card, DataTable, FilterBar, Pill, Sparkline); Overview page. Self-test (Vite build + FastAPI boot + web-devloop-tester screenshot).
- **Specialist agents (iteration 2, parallel):** Access page · Workspaces page + drill-down · Query Advisor + recommender · Storage/Tables + savings engine · Governance page · In-geo matrix (verify + data) · README/ARCHITECTURE.
- **Iterations 3–10:** build → run (Vite build + backend) → web-devloop-tester visual + console-error check → fix → repeat. Validate: no console errors, all pages render, filters work, tables sort, numbers reconcile to demo dataset, in-geo gating enforced, README accurate. Track issues in `SUBMISSION_TODO.md`.

Naming: app title "FinOps Command Center", short "FinOps CC". British English in copy. No emoji section markers in the app UI (pills/icons OK).

## 9. Ideas folded in from cost-by-driver reference dashboard (2026-07-07)
Incorporate these into the relevant pages (they validate the AU-East SKU framing — SKUs literally end in `_AUSTRALIA_EAST`):
- **Cost by cost-driver (product)**: bar of total cost per driver (AI_GATEWAY, ALL_PURPOSE, APPS, DATA_QUALITY_MONITORING, DLT, INTERACTIVE, JOBS, LAKEBASE, MODEL_SERVING, NETWORKING, PREDICTIVE_OPTIMIZATION, SQL, VECTOR_SEARCH) + **monthly trend by top-5 drivers** (multi-line). → Overview + Workspaces drill-down.
- **SKU-level breakdown table** with `total_cost` + **`pct_of_total`** (e.g. PREMIUM_SERVERLESS_SQL_COMPUTE_AUSTRALIA_EAST 23.8%, PREMIUM_JOBS_SERVERLESS_COMPUTE_AUSTRALIA_EAST 20.7%, …). → Workspaces page "By Service/SKU" sub-view.
- **Driver change vs prior 30 days (%)**: MoM %-change bar per driver → **spike detection**; also fires a Recommendations trigger ("driver X up N% MoM — investigate/attribute"). → Overview + Workspaces + recommendation_catalog trigger.
- **Cost by SKU per workspace** (stacked bar) and **DBU consumption by SKU** (bar). → Workspaces.
- **Daily estimated USD cost by SKU, last 90 days** (stacked) — enrich the Overview/Workspaces trend beyond a single line.
- **Egress / networking SKUs** as a first-class cost category: DATABRICKS_INTER_CONTINENTAL_EGRESS_OCEANIA, INTER_REGION_EGRESS_AUSTRALIA_EAST, INTERNET_EGRESS_OCEANIA, PUBLIC/PRIVATE_CONNECTIVITY_*. Add a **"cross-region / egress cost" recommendation** (keep compute + data co-located in AU East to avoid egress — ties directly to the in-geo theme: egress is both a $ and a data-residency signal).
- **Dual currency**: show AUD alongside USD (customers here are AU). Add a currency toggle (USD default, AUD optional) — list-price USD × FX, clearly labelled.

## 10. Deploy-time FEATURE FLAGS (one customer wants AI, another not; same for DQM)
Single config source (`config.yaml` / `app.yaml` env), surfaced via `/api/config`:
- `features.genie` (bool) — the Ask Genie banner/chat (§11).
- `features.ai_narration` (bool) — optional LLM rationale on recommendations (the in-region FMAPI Claude call).
- `features.dqm` (bool) — the Data Quality Monitoring tab (§12).
- plus `USE_LIVE`, `genie_space_id`, `warehouse_id`, `catalog_scope`, `fx_rate`.
Frontend reads `/api/config` once and CONDITIONALLY renders: Ask Genie banner only if `genie`; the "Data Quality" nav tab only if `dqm`; LLM narration only if `ai_narration`. **The deterministic recommender + cost-attribution + Genie-cost view are ALWAYS on** (they call no model — not "AI features"). Deploy variants: AI+DQM / AI-only / DQM-only / neither — a customer with no AI appetite sets `genie=false, ai_narration=false`; one without DQM sets `dqm=false`. Provide `--no-ai` / `--no-dqm` convenience in deploy.sh that just set these flags. Anything hidden must also be inert server-side (no Genie/model calls when disabled).

## 11. Ask Genie banner (like GridSense)  — gated by `features.genie`
Persistent banner beneath the nav: "Ask Genie about your costs & usage" — text input + suggested-prompt chips ("Which team's queries cost the most last month?", "Untagged spend by workspace", "Genie Code vs Genie Space spend for <user>", "Top external tables to convert"). Streams answers (SSE, like GridSense `useGenieStream`) via the **Genie Conversation API** against a configured **Genie Space (`genie_space_id`)** curated over the cost/usage/access tables. Demo mode returns canned streamed responses. In-region: the Genie Space + any FMAPI must be **australiaeast** (config; never call out-of-region). Show the "list-price reference, Account Console is source of truth" caveat.

## 12. Data Quality Monitoring tab  — gated by `features.dqm`
"If DQM is switched on, show the tables already being monitored." (DQM = Lakehouse Monitoring.)
- **Monitored-tables list:** catalog.schema.table, monitor type (Snapshot / TimeSeries / Inference), profile + drift metric tables, auto dashboard link, refresh schedule + last-refresh **freshness**, drift/quality status (anomaly count, PSI/KS drift, null %, distinct), #metrics, alert count/status.
- **FinOps tie-in (this is a cost tool):** the `DATA_QUALITY_MONITORING` billing line (from `system.billing.usage`) + which monitored tables/workspaces drive that cost; recommend pruning monitors on low-value/idle tables. 
- Live mode: detect existing monitors (monitor metadata / information_schema); demo mode: representative monitors. If `dqm=false`, the tab and `/api/dqm` are absent.

## 13. Genie cost attribution — Genie Code vs Genie Space, per workspace, per user  (from the Ritika email; ALWAYS on)
Facts: Genie billed under `billing_origin_product = 'GENIE'`; `usage_metadata.genie` = struct {surface, channel, agent_id}; **`surface = 'GENIE_CODE'` ⇒ Genie Code**, **empty surface ⇒ Genie Spaces (directional signal, not an official split)**; from **6 Jul 2026** consumption-based with **150 free DBUs of LLM usage per identified user per month**, then billed DBUs; `$ = usage_quantity × pricing.effective_list.default` (list price); `identity_metadata.run_as` = user; `workspace_id` = origin. **Budgets cannot be separated by Genie product — only by account/workspace/user/group.**
Encode these three queries (from the email PDF) in `data/queries.sql`:
1. **Ground-truth** paid Genie usage by usage_date × workspace_id × run_as × usage_metadata.genie (source of truth for who/where/which surface).
2. **Approximate paid split** by month × workspace × Genie subtype, pro-rating each user's paid DBUs across subtypes AFTER the shared 150-free-DBU/user/month allowance (`GREATEST(total_user_dbus - 150, 0) × usage_share_of_month`).
3. **Total Genie spend per workspace, this month** (distinct users, total DBUs, list $).
Surface as a **"Genie spend" breakdown** in the Recommendations cost-attribution panel AND the Workspaces drill-down: per workspace × user × subtype (Code vs Spaces), DBUs + list $, with the caveat text ("directional split; budgets can't separate Genie products"). Will read light until usage flows after 6 Jul — label accordingly.

## 14. Integratability — deploy into a customer's own workspace
- **Databricks Asset Bundle** (`databricks.yml`) + `deploy.sh`: one-command `databricks bundle deploy -t <target>` into the customer's workspace; small always-on App container (stop it to pay nothing).
- **Per-customer config** (`customise.yaml`, like GridSense): `warehouse_id`, `genie_space_id`, `catalog_scope`/workspace allowlist, feature flags (§10), `fx_rate`, `USE_LIVE`. No code edits to onboard a customer.
- **Grants doc** (README + docs): the exact `SELECT` grants — `system.billing.usage`+`list_prices`, `system.query.history`, `system.access.audit`, `system.information_schema.*_privileges`, `system.lakeflow.*`; DQM monitor tables (if `dqm`); Genie Space access (if `genie`). All read-only.
- Document the 4 deploy variants and the minimal-footprint story.
