# Where would Lakebase add value? — assessment + what shipped

> **Status: assessed, then partly built.** This began as a "where would Lakebase
> earn its place?" assessment (the original body below). The outcome that SHIPPED:
> a **writeback** capability (acknowledge/ignore alerts) that is **on by default**
> and backed by a small **Delta** table via the read warehouse — no Postgres
> required. **Lakebase is an optional, bundle-native backend** for that writeback
> (deploy `-t customer-lakebase`; the bundle creates + binds a CU_1 instance),
> not a mandatory layer. See the "DECISIONS SINCE THIS DOC WAS DRAFTED" box below
> and **README.md** / **docs/CUSTOMER_DEPLOYMENT.md** for current behaviour.

## TL;DR

| Scenario | Verdict | Why |
|---|---|---|
| **(a) AI/Genie ON** — conversation history / agent memory | **Conditional GO** — but only if we add stateful agent/chat features. Not needed for today's stateless Ask-Genie. |
| **(b) No AI/Genie** — writeback: BU/owner mapping, acknowledgements, budgets, saved views | **GO for a "writeback tier"** — this is the strongest case, and it's *independent of AI*. It unlocks capabilities system tables fundamentally cannot provide (user-generated state). |
| Caching expensive aggregations | **NO-GO** — the gold Delta tables already solve this; Lakebase would duplicate it. |

**Bottom line:** Lakebase is **worth including even in a no-AI/no-Genie deployment** — not as a cache, but as the **writeback/state store** the app currently lacks. Recommend making it an **optional, feature-flagged capability** (`features.writeback`), off by default, that a customer enables when they want in-app editing/acknowledgement/budgets. It should NOT become a hard dependency (mirrors the AI/Genie pattern).

---

## The core distinction

Everything the app does today is **read-only over system tables** + a **nightly gold precompute**. System tables can tell you *what happened* (spend, usage, grants). They fundamentally **cannot store anything a user creates**: an edit, an acknowledgement, a budget, a saved view, a comment. That "user-generated state" is the entire gap Lakebase fills — and it has nothing to do with AI.

So the real question isn't "AI vs no-AI" — it's **"does this deployment want writeback?"** Both scenarios below reduce to that.

---

## Scenario (a) — deployments WITH AI/Genie enabled

**Candidate value: persist Genie/agent conversation history + agent memory across sessions.**

- **What system tables can't do:** `system.billing.usage` records that a GENIE call happened and its cost — it does **not** store the *conversation* (the question, the returned SQL, the follow-ups, the user's thread). Today Ask-Genie is **stateless**: each question is a fresh Conversation-API call; nothing persists after the page reloads. If we wanted "resume your thread," "here's what you asked last week," or an agent that remembers context across sessions, that state has to live somewhere — Lakebase (Postgres) is the natural home (transactional, low-latency, per-user rows).
- **But:** today's app doesn't *have* stateful agents or a persistent chat surface. The Genie integration is a single-shot Q&A. So this value is **latent** — it only materialises if/when we build a stateful agent or a "conversation history" feature. Persisting history for its own sake (a log nobody reads) isn't worth a Postgres instance.
- **Cost/complexity:** Lakebase instance (~$ small-but-nonzero always-on managed Postgres) + a `conversations` / `messages` schema + write path in the Genie SSE handler + a read path for a history panel. Moderate.
- **Integration sketch (if we build stateful Genie later):**
  - `conversations(id, user, created_at, title)`, `messages(conversation_id, role, content, sql, cost_dbus, created_at)`.
  - On each Genie turn, INSERT the user question + the returned answer/SQL.
  - A "History" panel reads the user's recent conversations; "resume" re-hydrates the thread into the Conversation API.
- **Go/No-go:** **NO-GO now, GO-when-stateful.** Don't add Lakebase *for* conversation history until we actually build a stateful agent/chat feature. When we do, it's the right tool. It must stay gated behind `features.genie` AND the new writeback flag, so a Genie-on-but-no-writeback deployment still works statelessly.

---

## Scenario (b) — deployments WITHOUT AI or Genie

This is where Lakebase earns its keep, and none of it needs AI. Ranked by value:

### 1. Writeback for the BU→workspace + owner mapping ⭐ (highest value)
- **Today:** the BU/owner dimension comes from an *optional customer-managed table* (`workspace_bu_table`). If it's not populated, the whole BU dimension hides. Editing it means a customer goes and writes a UC table by hand — friction that means most never do, so the BU features stay dark.
- **With Lakebase:** an **in-app editable grid** — assign a workspace to a BU, set an owner, right there in the UI. This is the difference between "BU features exist if you did homework" and "BU features work out of the box, editable by the platform lead." It directly amplifies features the app *already has* (multi-workspace BU filters, per-BU cost splits).
- **What system tables can't do:** they're read-only; there is no place to persist a user's mapping edits. The gold tables are machine-regenerated nightly, so they can't hold hand edits either.
- **Value:** high — turns a dormant optional feature into a first-class, self-service one.

### 2. Acknowledged-vs-open optimization tracking ⭐
- **Today:** every page recomputes recommendations from scratch each refresh. There's no notion of "we looked at this one and decided to skip it" or "this is in progress." The same already-triaged recs reappear forever — the #1 complaint about any recommendations surface.
- **With Lakebase:** persist a status per rec (`open / acknowledged / in-progress / dismissed / done`) + who/when/note. The Recommendations tab and the new landing "needs attention" list filter out acknowledged items, so the list stays *actionable* over time.
- **What system tables can't do:** there is no per-recommendation state anywhere — recs are derived, not stored.
- **Value:** high — converts a static report into a workflow. Pairs naturally with the new landing view.

### 3. Budgets / targets
- **Today:** the app shows spend and MoM deltas, but has no notion of a *target*. "Is $642K good or bad?" has no answer without a budget.
- **With Lakebase:** store per-BU / per-workspace monthly budgets; the landing view and Workspaces tab show actual-vs-budget and flag overruns. This is a genuinely new capability, not a re-skin.
- **What system tables can't do:** budgets are a business input, not observable data.
- **Value:** medium-high — very demo-able ("you set a target, we alert on breach"), no AI needed.

### 4. Annotations / comments on cost anomalies
- Persist a short note against a flagged anomaly ("known — quarterly batch reload"). Stops the team re-investigating the same expected spike each month.
- **Value:** medium — nice, lower urgency than 1–3.

### 5. Saved views / filters / user preferences
- Persist a user's default workspace filter, currency, favourite tabs, saved filter sets.
- **Value:** low-medium — pure convenience; `localStorage` already covers the simplest cases (currency persists today). Only worth it once Lakebase is in for reasons 1–3.

### NON-value: caching expensive aggregations ❌
- **Explicit no:** the app **already** has a serving/cache layer — the nightly **gold Delta tables** (`finops_gold*`) that turn multi-second system-table scans into sub-second keyed reads, on a scale-to-zero warehouse. Adding Lakebase *as a cache* would duplicate that with a second, always-on store. The gold layer is the right cache for read aggregations; Lakebase is for **writeback state**, which gold can't do (it's machine-regenerated). Keep the two concerns separate.

---

## Cost / complexity (scenario b, the recommended one)

- **Cost:** one small Lakebase (managed Postgres) instance. Always-on, so it adds a modest fixed monthly cost (low tens of $ at the smallest tier) — the same objection that got it removed before. **Mitigant:** make it opt-in (`features.writeback=false` by default). A customer who wants in-app editing/budgets accepts the cost; everyone else pays nothing and the app is 100% unchanged.
- **Complexity:** moderate and **contained** — the app already has the `databricks-sdk` + a clean data-access layer. Add: a thin `data/store.py` (psycopg connection + a handful of tables), a few write endpoints (`PUT /api/mapping`, `POST /api/recs/{id}/ack`, `PUT /api/budgets`), and small UI affordances (editable grid, ack buttons, budget inputs). No new framework.
- **Critical constraint (mirrors AI/Genie):** it MUST be feature-flagged and degrade gracefully. With `writeback` off: no Lakebase instance, mapping stays read-only from the optional UC table, recs don't persist status, no budgets — exactly today's behaviour, zero errors, zero blank panels. This is non-negotiable given the multi-customer, capability-varying deployment model.

## Minimal integration sketch (if you greenlight scenario b)

```
features.writeback (default false) ─┬─ off → no Lakebase, everything read-only (today)
                                    └─ on  → provision 1 small Lakebase instance
data/store.py         # psycopg pool; ensure_schema() idempotent
  tables:
    ws_mapping(workspace_id PK, bu, owner, updated_by, updated_at)
    rec_status(rec_id PK, status, note, updated_by, updated_at)
    budgets(scope_type, scope_id, month, budget_usd, PRIMARY KEY(scope_type,scope_id,month))
endpoints (all 404 when writeback off):
    GET/PUT /api/mapping           # read + edit BU/owner grid
    POST    /api/recs/{id}/ack     # acknowledge / set status
    GET/PUT /api/budgets
UI (all hidden when writeback off):
    Workspaces/landing: editable BU-owner grid; budget inputs; actual-vs-budget
    Recommendations + landing "needs attention": ack/dismiss + status filter
read fallback: if writeback off, mapping still read from workspace_bu_table (UC) as today
```

## Recommendation

- **Scenario (a) AI-on:** **No-go now.** Revisit only when we build a stateful agent / persistent Genie chat — then Lakebase is the right store, gated behind Genie + writeback.
- **Scenario (b) no-AI:** **Go — as an optional `features.writeback` tier.** This is the stronger, AI-independent case: it unlocks BU-mapping editing, acknowledged-vs-open rec tracking, and budgets — real capabilities system tables can't provide — and it directly strengthens features the app already ships (BU filters, the new landing "needs attention" list). Keep it opt-in and gracefully-degrading so it never re-introduces the "always-on cost with no benefit" problem that got it removed. **Do NOT use it as an aggregation cache — the gold tables already own that.**

**Yes, Lakebase is worth including in a no-AI/no-Genie deployment** — specifically as a writeback/state store (not a cache), and only when the customer opts into the writeback features. Awaiting your go/no-go before any build.

---

# Design: Acknowledge / Ignore for alerts & recommendations (BUILT)

> **DECISIONS SINCE THIS DOC WAS DRAFTED (now implemented):**
> 1. **`features.writeback` defaults ON** (not off) and stays on in normal
>    deployments. Acknowledge/ignore is cheap, is genuinely valuable, and is
>    **independent of AI/Genie** — it governs the deterministic rules engine's
>    findings, so it matters *most* in an AI-off deployment where the same
>    rule-based alerts recur every refresh. (`FINOPS_FEATURE_WRITEBACK=false`
>    remains as an escape hatch for a strictly read-only deployment.)
> 2. **Delta is the default backend — no mandatory Lakebase/Postgres.** The store
>    is **pluggable** (`data/store.py`): in-process in demo, a small **Delta
>    table** (via the existing read warehouse) in live by default — **zero new
>    always-on infra / cost**, works everywhere.
> 3. **Lakebase is an opt-in premium backend, and it's bundle-native.** Deploy
>    `-t customer-lakebase` and the bundle *creates* a CU_1 Lakebase instance and
>    *binds* it to the app — no CLI, no env var, no manual Postgres role (the
>    binding auto-provisions the app service-principal's role and injects
>    `PGHOST`/`PGUSER`/`PGINSTANCE`/… at runtime, which `store.py` auto-detects to
>    switch from Delta to Lakebase). It's a separate target rather than a flag
>    because a DAB can't conditionally create a *resource* from a boolean.
> 4. Applies to **every alert surface** (recommendations, jobs, warehouses,
>    clusters, tables, query advisor) via one source-agnostic `(source,
>    entity_key)` overlay.
>
> The scenario-(b) sections above ("off by default", "opt-in", "one small
> Lakebase instance") reflect the original recommendation; the shipped default is
> on-with-Delta, Lakebase-opt-in per the decisions above.

This is the first writeback capability to build (scenario b, #2). Goal from the ask:
a team can **acknowledge or ignore** an alert/recommendation so it stops surfacing
every refresh — e.g. *"this job's growth is expected, we added new sources"* —
**without** muting a genuinely new problem later.

## The core principle: acknowledge a SITUATION, not a row

A naive "dismiss → hide forever" is a trap. If job X is acked as "expected growth"
and then grows another 60% next month for a real new reason, a dumb dismiss keeps
it hidden — you've muted a real alert. So an acknowledgement must (1) bind to a
**stable key** that survives the nightly recompute, (2) capture the **magnitude at
ack time**, and (3) **auto-reopen when the situation materially worsens**.

## Applies to EVERY alerting surface — not just recommendations

Acknowledge/ignore is a **cross-cutting capability**, built once and applied to
every surface that flags something to act on. It's deliberately generic: a single
`(source, entity_key, metric)` contract that each surface plugs into by declaring
three things — its stable key, its magnitude metric, and its re-open rule. Full
coverage:

| Surface | `source` | Stable `entity_key` | Magnitude metric (snapshot) | What "acknowledge" means here |
|---|---|---|---|---|
| Recommendations hub | `rec` | `{catalog_id}::{scope_label}` (exists) | `est_savings_usd_month` | "We've accepted / won't action this play" |
| **Jobs** — cost anomaly | `job_anomaly` | `{workspace_id}:{job_id}` | `cost_usd_month` | *your example* — "growth expected, new sources added" |
| **Jobs** — failing / rising-fail | `job_health` | `{workspace_id}:{job_id}` | `fail_rate` | "known-flaky / owned by team X, tracking separately" |
| **Jobs** — fastest-growing | `job_growth` | `{workspace_id}:{job_id}` | `growth_pct` | "expected ramp" |
| **Warehouses** — queuing/idle/spill | `warehouse` | `{warehouse_id}` (+ reason) | `queued_ratio` / `spill_gb` / idle$ | "sized deliberately for peak / batch window" |
| **Clusters** — oversized/idle | `cluster` | `{cluster_id}` (+ finding) | `idle_pct` / oversize$ | "kept warm on purpose" |
| **Tables** — external→managed, etc. | `table` | `{fqn}` (+ rec type) | `est_savings_usd_month` | "external by design (shared location)" |
| **Query Advisor** — SELECT * / rewrite | `query` | statement fingerprint (+ rec) | `est_savings_usd_month` | "generated by BI tool, can't change" |
| **DQM** — prune candidate (if `dqm` on) | `dqm` | `{monitor_fqn}` | monitor `$/mo` | "keep — compliance requires it" |
| **Governance** — over-privilege, untagged | `gov` | `{scope_label}` (+ tile) | count / $ | "exception approved" |

The state store is **source-agnostic** (`ack_status(source, entity_key, …)`), so
adding a new alert type later is just: pick a stable key + a metric + a tolerance.
No per-surface storage, no duplication. Each tab (and the landing "needs attention"
list, and the Recommendations hub) applies the same overlay + filter.

Build order simply sequences the *wiring* of surfaces onto this one mechanism —
recs + jobs first (jobs is your worked example), then warehouses/clusters/tables,
then query/DQM/governance — but the capability itself covers all of them by design.

## Why it's feasible today: stable keys already exist

- **Recommendations** already carry a durable id: `f"{catalog_id}::{scope_label}"`
  (e.g. `CMP-002::wh-metering-l`, `BEH-002::Data Platform`, `STO-004::estate`) —
  see `data/rec_hub.py:351`. Human-meaningful and stable across recomputes.
- **Jobs** key on `workspace_id + job_id`; **warehouses** on `warehouse_id`;
  **cost anomalies** on `workspace_id + job_id` (+ the anomaly type). All stable.
- So an ack row keys on `(source, entity_key)` and re-binds to the same finding
  every night. This is the linchpin — without stable keys this feature is unsafe.

## Smart re-alert (the chosen behaviour)

At acknowledge time we snapshot the finding's **magnitude metric** (the one that
matters for that source):

| Source | Snapshot metric | Re-open trigger (default) |
|---|---|---|
| Recommendation (cost play) | `est_savings_usd_month` | current > 1.25 × acked value |
| Job — cost growth / anomaly | `cost_usd_month` (or `growth_pct`) | current cost > 1.25 × acked, OR growth re-accelerates > acked + 15pp |
| Warehouse — queueing/spill | `queued_ratio` / `spill_gb` | current > acked × 1.25 |
| Generic fallback | the rec's primary $ | > 1.25 × acked |

Rules:
- **Within tolerance → stays hidden** (the "expected growth" case: it grew a bit
  more, still expected, stays quiet).
- **Materially worse → auto-reopens** with a badge: *"Re-opened — cost is 61%
  above the $4.2K it was at when acknowledged as 'expected — new sources' on
  12 Jul by casey@."* The reason + history stay attached so context isn't lost.
- **Tolerance is configurable** per deployment (`ack_reopen_tolerance`, default
  0.25); a team can make it tighter/looser.

## Status model

`open` (default, not acked) · `acknowledged` (expected / accepted, with reason) ·
`wont_fix` (deliberately never acting — e.g. external constraint) · `in_progress`
(being worked, keep visible but badge it) · `done` (fixed; auto-verifies —
disappears when the underlying finding no longer fires).

Optional **expiry** (`snooze_until`): an ack can time-box itself (e.g. 90 days)
so acknowledgements don't silently accumulate into stale forever-mutes. On expiry
it reopens as `open` regardless of magnitude.

## Where it shows in the UI

- **Recommendations tab** + the **landing "Needs attention" list**: acked/wont-fix
  items drop off the default view (both stay actionable). A **status filter**
  ("Open · Acknowledged · In progress · Won't fix") reveals them, each with its
  reason, who/when, and a one-click **Reopen**.
- Auto-reopened items appear at the top of Open with the "worse than when acked"
  badge — the single most valuable signal this feature produces.
- Each row gets a small **Acknowledge ▾** control (status + a short reason +
  optional snooze). In-progress items keep showing but with an amber "in progress"
  pill so they're not re-triaged.

## End-user UX (must be dead simple — no forms, no admin screen)

The whole interaction is inline on the alert/rec row the user is already looking
at. One click to act; one click to undo. No separate page, no modal wizard.

**1. The control.** Every alert/rec row carries a compact **Acknowledge ▾** button
(icon + label). Clicking opens a small popover — not a full form:
  - **Preset reason chips** (one tap sets status + reason together, the common paths):
    - `✓ Expected — no action` → status `acknowledged` (e.g. "growth expected, new sources added")
    - `⏳ Working on it` → status `in_progress`
    - `⛔ Won't fix` → status `wont_fix`
  - An optional **one-line note** field ("new sources added Q3") — free text, not required.
  - An optional **Snooze** toggle: `30 / 90 days` chips (defaults to no expiry).
  - A single **Done** button. That's it — most users just tap a preset chip and it closes.

**2. Instant feedback.** On confirm, the row animates out of the active list and a
**toast** appears: *"Acknowledged — hidden until it worsens >25%. Undo"* (the Undo
reopens it). The toast states the re-alert rule in plain words so the user knows it
won't silently swallow a real change.

**3. Finding acked items again.** A single **filter chip row** at the top of the list:
`Open (N) · Acknowledged (N) · Won't-fix (N) · In-progress (N)`. Clicking a chip shows
those, each with its reason + who/when + a **Reopen** button. No hunting, no separate
audit screen — the audit trail *is* the filtered view.

**4. The safety signal, made obvious.** When an acked item auto-reopens, it appears at
the **top of Open** with a red **"⚠ Worse than when acknowledged"** badge and a plain
sentence: *"Cost is 61% above the $4.2K it was when marked 'expected' on 12 Jul."* This
is the one thing that makes acknowledging safe to do liberally — the user trusts it
because they can see it re-surfaces real change.

**5. Zero-friction defaults.** No login/role gating in-app (the SP identity is recorded
as `updated_by` for the trail); no required fields; sensible defaults (no snooze, 25%
tolerance) so a first-time user succeeds with a single tap. Keyboard-operable and
consistent across every tab (same control everywhere — learn it once).

## Schema (Lakebase / Postgres)

```sql
CREATE TABLE ack_status (
  source         text NOT NULL,       -- 'rec' | 'job' | 'warehouse' | 'anomaly'
  entity_key     text NOT NULL,       -- e.g. 'CMP-002::wh-metering-l' or 'ws123:job456'
  status         text NOT NULL,       -- acknowledged | wont_fix | in_progress | done | open
  reason         text,                -- "expected — new sources added"
  metric_name    text,                -- 'cost_usd_month' | 'est_savings_usd_month' | ...
  metric_at_ack  double precision,    -- snapshot magnitude for smart re-alert
  reopen_tolerance double precision,  -- per-item override; else deployment default
  snooze_until   date,                -- optional time-box
  updated_by     text NOT NULL,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, entity_key)
);
-- plus an ack_history append log (same cols + action) for the audit trail.
```

## Endpoints (all 404 when `features.writeback` is off)

```
GET  /api/acks?source=rec            → { entity_key: {status, reason, metric_at_ack, ...} }
POST /api/acks/{source}/{entity_key} → body {status, reason?, snooze_days?}; snapshots
                                        the current metric server-side (never trust the
                                        client for magnitude), appends to ack_history
DELETE /api/acks/{source}/{entity_key} → reopen (status→open)
```

## How it plugs into the existing flow (no duplication)

- `rec_hub.build_hub()` (and the jobs/warehouse builders) stay pure/derived. A thin
  **overlay** step joins each finding to `ack_status` by key and computes an
  effective status + `reopened` flag using the smart-re-alert rule. This keeps the
  derivation logic clean and the ack state a separate concern.
- The landing "needs attention" list and Recommendations default view filter out
  `status IN (acknowledged, wont_fix)` **unless** `reopened=true`.

## Feature-flag & graceful degradation (non-negotiable)

- Gated on `features.writeback` (default **false**). Off ⇒ **no Lakebase instance
  provisioned**, endpoints 404, the Acknowledge controls + status filter are hidden,
  every finding shows as it does today. Zero errors, zero blank panels — identical
  to current behaviour. Mirrors the AI/Genie optionality contract exactly.
- Works fully with **AI and Genie off** — acknowledge/ignore is pure writeback +
  arithmetic, no model involved. (This is the headline: the most-requested writeback
  feature needs no AI at all.)

## Cost / complexity

- One small always-on Lakebase instance (low tens of $/mo) — **only when the flag is
  on**. Contained build: a thin `data/store.py` (psycopg pool + `ensure_schema()`),
  3 endpoints, the overlay join, and the Acknowledge UI control + status filter.
- Effort: **Medium** (~2–3 days). The stable-key groundwork already exists, which is
  what makes it safe and not a research project.

## Go/No-go

**Recommended GO** as the first `features.writeback` capability, **after your review
of this design.** It directly answers the ask ("acknowledge/ignore so it stops
recurring"), avoids the mute-a-real-problem trap via smart re-alert, needs no AI,
and stays fully optional. Build order: (1) writeback tier + `store.py` + flag, (2)
acknowledge/ignore for recommendations + jobs, (3) extend to warehouses/anomalies,
(4) later: BU-mapping editing + budgets on the same tier.
