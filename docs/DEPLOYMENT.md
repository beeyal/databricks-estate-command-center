# Deploying Estate Command Center

> [!IMPORTANT]
> **This document has moved.** Deployment now lives in **one place** — there's no
> separate config to reconcile and no shell wrapper (`config.yaml`, `customise.yaml`,
> and `deploy.sh` were removed; everything is env vars in the single, self-documenting
> **`app.yaml`**).
>
> 👉 **[Customer deployment guide → docs/CUSTOMER_DEPLOYMENT.md](CUSTOMER_DEPLOYMENT.md)**
> is the complete, authoritative walkthrough: import the code, set your two values in
> the deploy cell, deploy the Asset Bundle, grant read access, turn on the nightly job — plus
> the exact grants, the `customer` (Lakebase, recommended) vs `customer-delta` (fallback)
> target choice, business-unit/team setup, upgrades and troubleshooting.

## The short version

Download the repo ZIP from GitHub, import it into your workspace, and run the deploy cell from
[CUSTOMER_DEPLOYMENT.md](CUSTOMER_DEPLOYMENT.md) Step ③ in a serverless notebook. The ZIP includes
the official Databricks CLI release, so the workspace never needs to reach GitHub, and nothing runs
on your laptop. (Running `databricks` in a `%sh` cell doesn't work: the CLI that ships on notebook
compute only runs in the web terminal.)

- **Ships in live mode.** It reads the real `system.*` tables as soon as a **warehouse +
  gold schema** are set (in the deploy cell); until then it boots on **demo data with a "finish
  setup to go live" banner** — it never fails to start.
- **One config file: `app.yaml`.** Each value is self-documented (what it does, what blank
  means). The only two required for live are `DATABRICKS_WAREHOUSE_ID` and
  `FINOPS_GOLD_SCHEMA`.
- **Read-only by construction.** The app runs as its own service principal and issues only
  `SELECT` / `DESCRIBE` / read APIs. A missing grant is an honest error, never fabricated data.
- **Writeback backend = the deploy target.** `-t customer` uses a bundle-created Lakebase
  (Postgres) instance (recommended); `-t customer-delta` is the no-Lakebase fallback (a tiny
  Delta table). `-t dev` is your own workspace in demo mode.

For everything else — the full step-by-step, grants, feature-flag matrix, jobs, BU/team,
upgrades, and troubleshooting — see **[CUSTOMER_DEPLOYMENT.md](CUSTOMER_DEPLOYMENT.md)**.
