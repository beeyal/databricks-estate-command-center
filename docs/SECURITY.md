# Security

> Estate Command Center is a field-built, open-source example — **not an official Databricks product**, provided **as-is without warranty**. Review it against your own security requirements before deploying.

## Posture at a glance

| Property | This app |
|---|---|
| **Data access** | **Read-only against your data.** It runs `SELECT` (and `DESCRIBE`/`EXPLAIN`) against `system.*` tables only, and **creates, modifies, and deletes nothing** in your lakehouse data. Its only writes are to its own bookkeeping tables — see the next row. |
| **Writes** | The only writes are the app's **own** bookkeeping tables in schemas you designate — `<gold_schema>.finops_gold*` (nightly precompute), `<ingest_schema>.finops_admin` (admin-API ingest), a small **acknowledge/ignore state table** (`finops_ack_status`) and a **payload cache** (`finops_payload_cache`, so a restart doesn't re-run the heavy scans) in your writeback schema (Delta, `-t customer-delta`) or the bound Lakebase Postgres instance (`-t customer`). Never your data, and never `system.*`. If the app SP can't write to the writeback schema, acknowledge is reported off and the cache stays in memory — nothing else changes. |
| **Authentication** | The app runs as its **own service principal** via the Databricks SDK credential chain (auto-injected `DATABRICKS_CLIENT_ID`/`SECRET` in Databricks Apps). **No personal access tokens, no end-user tokens.** |
| **Authorization** | Least-privilege: the app SP is granted only `USE CATALOG`/`USE SCHEMA`/`SELECT` on the specific `system.*` schemas it reads. **One exception:** on the Delta backend (`-t customer-delta`) the app SP also holds `CREATE`/`MODIFY` on the **single writeback schema** for its own `finops_ack_status` and `finops_payload_cache` tables — nothing else, and never `system.*`. Lakebase-backed (`-t customer`) ⇒ the app SP is read-only on Unity Catalog and writes only to its own schema in the bound Postgres instance. (The two jobs' SPs additionally hold `CREATE`/`MODIFY` on their own gold/ingest schemas.) See the grant matrix in the [Deployment guide](CUSTOMER_DEPLOYMENT.md). |
| **Failure mode** | **Fail-closed.** A missing grant surfaces an explicit "needs SELECT on …" message and never fabricates or silently blanks a value. Optional features degrade gracefully to an honest empty state. |
| **External calls** | **None outside your workspace/region.** No third-party services, no telemetry, no data egress. The optional AI features call **in-region** Databricks endpoints only (see below). |

## Read-only against your data

Every live query that touches **your** data is in [`data/queries.sql`](../data/queries.sql) / the `*_live` builders in [`data/live.py`](../data/live.py) and is a `SELECT`/`DESCRIBE`. The **AI SQL-rewrite** path additionally runs `EXPLAIN` — a read-only guard (`is_read_only_select`) rejects anything that isn't a plain `SELECT`/`WITH` **before** the warehouse is touched, and `EXPLAIN` plans the statement without executing it. The app cannot run `INSERT`/`UPDATE`/`DELETE`/`DROP`/`OPTIMIZE`/`VACUUM` on your lakehouse data or `system.*` — it only *recommends* them as copyable SQL for you to review and run yourself.

The one exception is the app's **own** state: the two serving jobs write the `finops_gold*` / `finops_admin` tables in schemas you designate, and the app upserts its small `finops_ack_status` (acknowledge/ignore) and `finops_payload_cache` (cached payloads) tables in your writeback schema or the bound Lakebase instance. These are the app's bookkeeping, isolated from your data; the payload cache holds the same aggregated cost/usage figures the app already shows.

## Authentication — service principal, not on-behalf-of-user (OBO)

The app authenticates as a **single service principal**, so every user sees the **same account-wide cost view**. This is a deliberate choice for a FinOps tool:

- Cost attribution needs a **consistent, complete estate view** regardless of who is logged in. OBO (running each query as the viewer with *their* Unity Catalog permissions) would fragment the numbers per-user and hide spend a viewer can't see — the opposite of what a cost-control dashboard needs.
- The SP is **read-only and least-privilege**, so the wide view is low-risk.

If your security team mandates per-user attribution on reads, OBO can be added (app `user_authorization` scopes + `x-forwarded-access-token` plumbing), but it is intentionally not the default. Access on the **Access** tab is always shown from the grant graph (`system.information_schema.*_privileges`), so per-principal entitlements are visible regardless of auth mode.

## Data-handling / PII

- The app reads **operational metadata only** — billing usage, query history, table/job/warehouse/cluster metadata, grants, tags, and list prices from `system.*`. It does **not** read the contents of your data tables.
- **Query text** appears on the Query Advisor tab (from `system.query.history`) — this can contain literals. It is displayed to app users who already have the read grants; treat the app's audience accordingly.
- **AI features (optional, independently switchable).** The shipped `app.yaml` turns both on: the LLM SQL-rewrite/narration (`FINOPS_FEATURE_AI_NARRATION`) and Ask Genie (`FINOPS_FEATURE_GENIE`); set either to `"false"` to switch it off (the built-in default for `ai_narration` when the variable is absent is off). When `ai_narration` is on, the rewrite/narration prompt sends the **query's SQL text + its measured metrics** (not table row data) to the configured **in-region** Foundation Model endpoint (`FINOPS_LLM_ENDPOINT`). When `genie` is on, Ask Genie sends the user's question to the configured **in-region** Genie Space. Both stay within your Databricks account/region; there is no third-party egress. With both flags off, the app makes **no model calls at all**.

## Auditability

All app queries run under the app's service principal identity and appear in `system.query.history` / `system.access.audit` under that principal — a clean, attributable trail. The two scheduled jobs (`finops_gold_sync`, `finops_admin_sync`) run under their own (SP) identities.

## Secrets

No secrets in code. Credentials are injected by the Databricks Apps runtime (client-id/secret) or resolved via the SDK credential chain. The repo's `app.yaml` carries only non-secret config (feature flags, warehouse id, schema names, FX rate).

## Reporting an issue

This is an example/field artifact without a formal support channel. If you find a security issue, raise it with whoever maintains your fork/deployment.
