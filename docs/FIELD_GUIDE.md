<div align="center">

# 🛠 Estate Command Center — Internal Field Guide

**Stand up a demo in your own FEVM/workspace, then onboard a customer.**

> **⚠️ Config is now ONE file: `app.yaml`.** `config.yaml`, `customise.yaml`, and
> `deploy.sh` have been removed. Wherever this guide mentions them, the current
> equivalent is an env var in **`app.yaml`** deployed with `databricks bundle deploy`.
> Demo deploy is just `bundle deploy -t dev` (app.yaml ships in demo mode).

*Asset-bundle deploy · nothing compiles · demo mode is the default*

</div>

---

This guide is for the **Databricks field** — SAs and SSAs showcasing the app or
onboarding a customer. It covers the **demo deploy** (what you run to get a live
showcase in minutes) and the **field tooling** (`deploy.sh`, `customise.yaml`,
feature-flag variants).

> [!NOTE]
> **Two other docs:**
> - Full page-by-page reference, architecture, data model → **[README](../README.md)**
> - Deploying **live in a customer's workspace** → **[Customer deployment guide](CUSTOMER_DEPLOYMENT.md)**

---

## 🎬 Deploy in demo mode — the 3-step, foolproof path

**This is what you run to stand up a demo.** Demo mode is the **default**
(`USE_LIVE: "false"`), so there is **nothing to configure** — no warehouse, no grants,
no gold tables, no scheduled jobs. Seeded, deterministic Acme-shaped data is served straight
away and labelled **DEMO DATA** everywhere. The Asset Bundle does the whole thing; the
built `frontend/dist` is committed, so **nothing compiles**.

> [!NOTE]
> **Three steps:** ① get the code → ② `bundle deploy -t dev` → ③ open the app. That's
> it. Grants and the daily job (steps ④–⑤ of the customer guide) are **live-mode only** —
> skip them for a demo.

### ① Get the code

**From your laptop (CLI):**

```bash
brew install databricks                                      # macOS (winget install Databricks.CLI on Windows)
git clone https://github.com/beeyal/databricks-estate-command-center && cd databricks-estate-command-center
databricks auth login --profile <your-profile>                  # browser prompt, once
```

**Or from inside the workspace:** top-right **Create ▾ → Git folder**, paste
`https://github.com/beeyal/databricks-estate-command-center`, **Create**. The code lands at
`/Workspace/Users/<you>/databricks-estate-command-center`.

### ② Deploy the bundle (dev target)

The `dev` target is the default and needs **no config edits** — demo mode is baked into
`app.yaml`'s defaults.

**Laptop:**

```bash
databricks bundle deploy -t dev -p <your-profile>
databricks bundle run   estate -t dev -p <your-profile>
```

**Workspace notebook / web terminal** (`%sh` cell — replace `<you>` with your email):

```sh
%sh
cd /Workspace/Users/<you>/databricks-estate-command-center
databricks bundle deploy -t dev
databricks bundle run   estate -t dev
```

> [!TIP]
> `dev` is `mode: development`, so the app is name-prefixed with your username — deploy as
> many parallel demo copies as you like without clashing. It creates **only the app** (the
> `finops_gold_sync` and `finops_admin_sync` jobs both ship **PAUSED**, so a `dev` demo
> incurs **no job cost** and no live scans).

### ③ Open the app

`bundle run` prints the app URL — open it. You're looking at the full 14-tab experience on
seeded data, Ask Genie streaming canned answers, all flags on. Nothing else to do.

> [!IMPORTANT]
> To later flip **the same deploy** to a customer's live data, follow the
> [Customer deployment guide](CUSTOMER_DEPLOYMENT.md) — set the three `app.yaml` values,
> use `-t customer`, then add the grants and unpause the job. Demo → live is a config
> switch, never a code change.

---

## Demo a specific variant (no Genie / no DQM / no AI)

Edit `app.yaml` before step ② (or flip the env var on the app page afterward and
redeploy). Each toggles independently (genie / dqm / writeback default **on**;
ai_narration's built-in default is off but this repo's config turns it on):

```yaml
FINOPS_FEATURE_GENIE: "false"          # hide the Ask Genie banner
FINOPS_FEATURE_AI_NARRATION: "false"   # drop the LLM rationale on recommendations
FINOPS_FEATURE_DQM: "false"            # hide the Data Quality tab
FINOPS_FEATURE_WRITEBACK: "false"      # strictly read-only: hide acknowledge/ignore controls
```

| Variant | `genie` | `ai_narration` | `dqm` | Tabs | For |
|---|:--:|:--:|:--:|:--:|---|
| **Default (everything on)** | ✅ | ✅ | ✅ | 14 | Full experience. |
| **AI only** | ✅ | ✅ | ❌ | 13 | AI appetite, no Lakehouse Monitoring. |
| **DQM only** | ❌ | ❌ | ✅ | 14 | DQM inventory, no Genie/LLM. |
| **Deterministic core only** | ❌ | ❌ | ❌ | 13 | Most conservative — no Genie, no LLM, no DQM. |

Anything hidden is also inert server-side — its endpoint is absent, not just unrendered.
Full matrix → [Feature flags & deploy variants](../README.md#feature-flags--deploy-variants).

---

## Field tooling — `deploy.sh` + `customise.yaml`

For repeat customer onboarding you can drive the whole config from one file instead of
hand-editing `app.yaml`. **`customise.yaml`** is the single per-customer file;
**`deploy.sh`** reads it, writes the matching env into `app.yaml`, and runs the bundle.

> [!NOTE]
> `deploy.sh` **needs Python** (`pyyaml`) on the machine running it. The plain
> `databricks bundle` commands above need neither Python nor npm — prefer them unless you
> specifically want the `customise.yaml` workflow.

```bash
./deploy.sh --profile <P>                      # demo deploy from customise.yaml
./deploy.sh --profile <P> --no-genie           # feature switches — compose freely
./deploy.sh --profile <P> --no-narration
./deploy.sh --profile <P> --no-ai              # genie + narration off (shortcut)
./deploy.sh --profile <P> --no-dqm
```

The `--no-*` switches just write the same `FINOPS_FEATURE_*` values into `app.yaml`. See
the [Deployment guide](CUSTOMER_DEPLOYMENT.md) for the full `customise.yaml` field list and the
config-precedence rules.

---

## Onboarding a customer (going live)

When it's time to deploy in the **customer's own workspace** against **their** system
tables, hand them — or follow yourself — the
**[Customer deployment guide](CUSTOMER_DEPLOYMENT.md)**. It's the same bundle with the
`customer` target: set three `app.yaml` values, deploy, grant the app's service principal
read access, and unpause the two scheduled jobs. No code changes, ever.

---

<div align="center">

*Product reference → [README](../README.md) · live customer deploy → [Customer guide](CUSTOMER_DEPLOYMENT.md) · full config surface → [DEPLOYMENT.md](DEPLOYMENT.md)*

</div>
