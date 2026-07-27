"""Deterministic, explainable recommendation engine for the Estate Command Center.

Implements SPEC §4: for each query (and, cross-surface, tables) produce a
structured recommendation:

    {
      insight_type,            # cluster | resize | rewrite | vacuum | optimize | ...
      rationale,               # one-line human-readable "why"
      est_savings_usd_month,   # $/mo, labelled "estimated"
      confidence,              # 0..1
      impact,                  # 0..100 (normalised $ × frequency × severity)
      next_steps,              # ordered list of actions
      flags,                   # detected diagnostic flags
      severity,                # 0..1 internal severity used for impact
    }

Design principles
-----------------
- **Deterministic first.** Every field above is computed from query metrics by
  transparent rules — no live model is required. (The live AI *rewrite* path,
  when ai_narration is enabled, lives in data/live.py via propose_rewrite; the
  recommender itself never calls a model.)

The engine is pure and side-effect free so it can run in both demo and live modes.
"""
from __future__ import annotations

import os
import re
from typing import Any

# ---------------------------------------------------------------------------
# LLM model config (SPEC §3.6).
#
# The optional LLM narration path (``narrate``) calls a Foundation Model API
# Claude serving endpoint. The endpoint name is a backend config value; if it is
# unset/blank the narration falls back to the deterministic rule text — the app
# never *requires* a live model.
#
#   FINOPS_LLM_ENDPOINT   — serving-endpoint name (default = FMAPI Claude)
#   FINOPS_LLM_NARRATION  — "1"/"true" to enable narration (default OFF)
# ---------------------------------------------------------------------------
DEFAULT_LLM_ENDPOINT = "databricks-claude-sonnet-4-6"  # FMAPI Claude


def get_llm_endpoint() -> str | None:
    """Return the configured model endpoint, or None if unset.

    Returns None only when the operator explicitly blanks it — in which case
    narration falls back to deterministic rule text.
    """
    endpoint = os.environ.get("FINOPS_LLM_ENDPOINT", DEFAULT_LLM_ENDPOINT).strip()
    return endpoint or None


def narration_enabled() -> bool:
    """Whether LLM narration is switched on. OFF by default."""
    return os.environ.get("FINOPS_LLM_NARRATION", "").strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Rule thresholds (SPEC §4) — kept as named constants so they are auditable.
# ---------------------------------------------------------------------------

PRUNE_LOW = 0.30          # pruning efficiency below this ⇒ candidate for clustering
BYTES_LARGE = 50e9        # "large scan" threshold (50 GB read)
SPILL_HIGH_GB = 20.0      # disk spill above this ⇒ resize candidate
SELECT_STAR_PRUNE = 0.45  # SELECT * with pruning below this ⇒ rewrite candidate
SMALL_FILE_RUNS = 200     # high-frequency small-file scans ⇒ optimize/vacuum

# Savings-model coefficients. All figures are "estimated" and the assumptions
# are surfaced in ``next_steps`` / the UI. Tuned so demo numbers are plausible.
CLUSTER_PRUNE_GAIN = 0.55   # expected fraction of scan cost removed by good pruning
RESIZE_SPILL_FACTOR = 0.60  # fraction of spill-driven cost recovered by right-sizing
REWRITE_PROJECTION = 0.40   # fraction of cost removed by projecting columns + predicates
OPTIMIZE_COMPACT = 0.35     # fraction of scan cost removed by compaction
VACUUM_RECLAIM = 0.15       # fraction of cost tied to stale-version overhead

# Impact-score normalisation (0..100). Impact = f($ savings, frequency, severity).
IMPACT_SAVINGS_CAP = 3500.0  # $/mo that maps to the full savings weight
IMPACT_RUNS_CAP = 3000.0     # run count that maps to the full frequency weight
W_SAVINGS = 40.0
W_FREQUENCY = 30.0
W_SEVERITY = 30.0

_INSIGHT_RATIONALE = {
    "cluster": (
        "Low pruning efficiency ({prune:.0%}) on a {gb:.0f} GB scan run {runs:,}× — "
        "apply Liquid Clustering on the filter/join keys so the engine skips files."
    ),
    "resize": (
        "Repeated disk spill ({spill:.0f} GB) exceeds warehouse memory — the engine "
        "re-reads spilled data every run. Resize the warehouse up one tier or split "
        "the workload."
    ),
    "rewrite": (
        "SELECT * / weak predicates reading {gb:.0f} GB at {prune:.0%} pruning — "
        "project only the needed columns and add selective filters."
    ),
    "vacuum": (
        "Stale file versions accumulating on the target table ({runs:,} runs/mo) — "
        "run OPTIMIZE + VACUUM to compact and reclaim, then let Predictive "
        "Optimization maintain it."
    ),
    "optimize": (
        "Excessive small files inflate scan time ({runs:,} runs/mo) — run OPTIMIZE "
        "to compact; enable Predictive Optimization to keep it compacted."
    ),
    "monitor": (
        "No storage-layout or rewrite lever applies here — this query reads little "
        "data ({gb:.1f} GB) or isn't a table scan (e.g. an AI/UDF or DDL statement). "
        "Nothing to OPTIMIZE; keep monitoring cost and run frequency ({runs:,} runs)."
    ),
}

_NEXT_STEPS = {
    "cluster": [
        "Identify the high-cardinality filter/join columns from the query predicates",
        "ALTER TABLE <t> CLUSTER BY (col1, col2)   -- or CLUSTER BY AUTO",
        "Backfill with OPTIMIZE FULL and confirm pruning efficiency improves",
    ],
    "resize": [
        "Open the query profile and confirm the 'bytes spilled to disk' metric",
        "Increase the SQL warehouse size one tier (or enable auto-scaling headroom)",
        "Re-run and verify spill is eliminated; right-size back down if over-provisioned",
    ],
    "rewrite": [
        "Replace SELECT * with the explicit column projection actually consumed",
        "Add selective WHERE predicates on partition/cluster keys",
        "Confirm bytes-read and pruning efficiency improve in the query profile",
    ],
    "vacuum": [
        "Run OPTIMIZE on the target table to compact files",
        "Run VACUUM (retain 7 days) to reclaim stale versions",
        "Enable Predictive Optimization so maintenance stays automatic",
    ],
    "optimize": [
        "Run OPTIMIZE to compact small files (target avg file >= 128 MB)",
        "Consider CLUSTER BY AUTO for ongoing layout maintenance",
        "Enable Predictive Optimization to keep files compacted",
    ],
    "monitor": [
        "No layout/rewrite action — this isn't a large table scan",
        "For AI / UDF queries, review model/endpoint cost on the AI $ tab",
        "Watch run frequency; batch or cache if the volume grows",
    ],
    "convert-to-managed": [
        "Recreate as a UC MANAGED table (CTAS, or DEEP CLONE to preserve history)",
        "Repoint downstream readers/writers to the managed table, then retire the external one",
        "Managed tables unlock Predictive Optimization + auto file management (external ones can't)",
    ],
    "enable-LC": [
        "ALTER TABLE <t> CLUSTER BY (frequent filter/join cols)   -- or CLUSTER BY AUTO",
        "Backfill layout with OPTIMIZE FULL",
        "Confirm scan/pruning improves; a large unclustered table scans far more than it needs",
    ],
    "enable-PO": [
        "ALTER TABLE <t> SET TBLPROPERTIES ('delta.predictiveOptimization' = 'true')  -- or enable at schema/catalog",
        "Let Predictive Optimization run OPTIMIZE/VACUUM automatically",
        "No manual maintenance job needed afterwards",
    ],
}


# ---------------------------------------------------------------------------
# Diagnostics — derive flags from raw statement metrics
# ---------------------------------------------------------------------------

def detect_flags(q: dict[str, Any]) -> list[str]:
    """Derive diagnostic flags from a query's statement metrics.

    Deterministic and explainable — each flag maps to a measurable condition.
    """
    # Only flags derivable from system.query.history so demo and live match and
    # nothing fabricated is shown. skew / small-file / cross-region are NOT in
    # query.history, so they are not emitted (removed per data-honesty rule).
    flags: list[str] = []
    p95 = q.get("p95_s", 0.0)
    prune = q.get("pruning_efficiency", 1.0)
    spill = q.get("spill_gb", 0.0)
    bytes_read = q.get("bytes_read", 0.0)
    queued = q.get("queued_ratio", 0.0)
    sql = q.get("query_text", "").upper()

    if p95 >= 30:
        flags.append("slow")
    if spill >= SPILL_HIGH_GB:
        flags.append("high-spill")
    if queued >= 0.20:
        flags.append("capacity-bound")
    if prune < PRUNE_LOW and bytes_read >= BYTES_LARGE:
        flags.append("full-scan")
    # SELECT * heuristic reinforces full-scan on wide reads
    if "SELECT *" in sql and bytes_read >= BYTES_LARGE and "full-scan" not in flags:
        flags.append("full-scan")
    return flags


# Human phrasing for a set of flags, used when a rationale needs to name the
# symptoms it's acknowledging (kept in sync with the frontend FLAG_META labels).
_FLAG_PHRASE = {
    "slow": "a slow tail (high p95)",
    "high-spill": "disk spill",
    "capacity-bound": "warehouse queueing",
    "full-scan": "a low-pruning full scan",
}


def _flags_phrase(flags: list[str]) -> str:
    """Join flag labels into a readable clause, e.g. 'warehouse queueing and a
    slow tail (high p95)'. Falls back to the raw flag name for any unknown."""
    labels = [_FLAG_PHRASE.get(f, f) for f in flags]
    if not labels:
        return "no diagnostic issues"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


# ---------------------------------------------------------------------------
# Core rule engine
# ---------------------------------------------------------------------------

def classify(q: dict[str, Any]) -> str:
    """Pick the single best insight_type for a query (SPEC §4 rule order).

    Priority: resize (spill) > cluster (pruning) > rewrite (projection) >
    optimize/vacuum (file layout). The highest-severity, highest-$-leverage
    lever wins so the surfaced recommendation is the one that matters most.
    """
    prune = q.get("pruning_efficiency", 1.0)
    spill = q.get("spill_gb", 0.0)
    bytes_read = q.get("bytes_read", 0.0)
    small_file_ratio = q.get("small_file_ratio", 0.0)
    stale_ratio = q.get("stale_ratio", 0.0)
    sql = q.get("query_text", "").upper()

    # Spill/right-size applies to ANY query regardless of what it scans.
    if spill >= SPILL_HIGH_GB:
        return "resize"

    # A storage-LAYOUT or rewrite lever (cluster / optimize / vacuum / rewrite)
    # only makes sense against a real Delta TABLE SCAN. AI/UDF, document-parse,
    # DDL/utility, and tiny-scan statements have nothing to OPTIMIZE — flagging
    # them "small files → run OPTIMIZE" is nonsensical. Gate those out to
    # "monitor" so we don't recommend a no-op (a customer: ai_parse_document → OPTIMIZE).
    if not _scans_a_table(q):
        return "monitor"

    if prune < PRUNE_LOW and bytes_read >= BYTES_LARGE:
        return "cluster"
    if "SELECT *" in sql and prune < SELECT_STAR_PRUNE:
        return "rewrite"
    # Stale versions dominate small-files when both present (VACUUM is the fix).
    if stale_ratio >= 0.35:
        return "vacuum"
    if small_file_ratio >= 0.40:
        return "optimize"
    if prune < SELECT_STAR_PRUNE and bytes_read >= BYTES_LARGE:
        return "rewrite"
    # DATA HONESTY: no default "optimize". "optimize" means a MEASURED small-file
    # problem (small_file_ratio >= 0.40, handled above) — asserting it on the
    # fallthrough labelled a query "Excessive small files" with zero evidence, and
    # since live has no file-hygiene columns (small_file_ratio is 0.0 there), EVERY
    # otherwise-unmatched live query got the false label + a fabricated savings
    # floor. Fall through to "monitor" (no lever claimed) instead.
    return "monitor"


# Below this scan size (and with no small-file/stale signal) a query isn't a
# meaningful table scan — a layout lever would be a no-op.
_MIN_SCAN_BYTES = 1e9   # 1 GB
# Statement shapes that never warrant a storage-layout/rewrite rec.
_NON_TABLE_LEAD = re.compile(r"^\s*(?:CREATE|ALTER|DROP|GRANT|REVOKE|SET|USE|SHOW|DESCRIBE|MSCK|REFRESH|CACHE|UNCACHE|COMMENT|EXPLAIN|INSTALL|COPY|PUT|GET)\b", re.IGNORECASE)
# AI / document / UDF functions whose cost is model/endpoint-driven, not layout.
_AI_FUNC = re.compile(r"\bAI_(?:PARSE_DOCUMENT|QUERY|GEN|GENERATE|EXTRACT|CLASSIFY|SIMILARITY|FORECAST|MASK|FIX_GRAMMAR|TRANSLATE|SUMMARIZE|ANALYZE_SENTIMENT)\b|\bVECTOR_SEARCH\b|\bai_\w+\s*\(", re.IGNORECASE)


def _scans_a_table(q: dict[str, Any]) -> bool:
    """True when the statement is a real Delta table scan a layout/rewrite lever
    could actually help. False for AI/UDF, DDL/utility, and tiny-scan queries."""
    sql = q.get("query_text", "") or ""
    if _NON_TABLE_LEAD.match(sql):
        return False
    if _AI_FUNC.search(sql):
        return False
    bytes_read = q.get("bytes_read", 0.0)
    small_file_ratio = q.get("small_file_ratio", 0.0)
    stale_ratio = q.get("stale_ratio", 0.0)
    # A genuine layout problem can exist even on a smallish scan IF the file/stale
    # signals are present; otherwise require a non-trivial scan size.
    if small_file_ratio >= 0.40 or stale_ratio >= 0.35:
        return True
    return bytes_read >= _MIN_SCAN_BYTES


def _severity(q: dict[str, Any]) -> float:
    """0..1 composite severity from pruning gap, spill, and latency."""
    prune = q.get("pruning_efficiency", 1.0)
    spill = q.get("spill_gb", 0.0)
    p95 = q.get("p95_s", 0.0)
    sev = (1 - prune) * 0.5 + min(1.0, spill / 220.0) * 0.3 + min(1.0, p95 / 260.0) * 0.2
    return round(min(1.0, sev), 4)


def estimate_savings(q: dict[str, Any], insight_type: str) -> float:
    """Estimate $/mo savings for the chosen lever.

    Savings ∝ runs × per-run cost × expected efficiency gain (SPEC §4).
    ``cost_usd`` is the observed monthly cost attributed to the statement, so we
    scale it by the lever's expected reduction fraction.
    """
    cost = q.get("cost_usd", 0.0)
    prune = q.get("pruning_efficiency", 1.0)
    spill = q.get("spill_gb", 0.0)

    if insight_type == "cluster":
        # The wider the pruning gap (1 - prune), the more scan cost clustering can
        # recover, capped by the expected best-case prune gain.
        return round(cost * CLUSTER_PRUNE_GAIN * min(1.0, 1 - prune), 2)
    if insight_type == "resize":
        # Recover the re-read cost implied by spill relative to a 220 GB ceiling.
        spill_frac = min(1.0, spill / 220.0)
        return round(cost * RESIZE_SPILL_FACTOR * spill_frac, 2)
    if insight_type == "rewrite":
        return round(cost * REWRITE_PROJECTION * min(1.0, (1 - prune) + 0.3), 2)
    if insight_type == "optimize":
        # Track the REAL measured small-file ratio (no 0.4 floor — the floor
        # fabricated savings for queries with a low/zero measured ratio). "optimize"
        # is only emitted when small_file_ratio >= 0.40 (see classify), so this is
        # always evidence-backed.
        return round(cost * OPTIMIZE_COMPACT * q.get("small_file_ratio", 0.0), 2)
    if insight_type == "vacuum":
        return round(cost * (VACUUM_RECLAIM + OPTIMIZE_COMPACT * 0.3), 2)
    if insight_type == "monitor":
        return 0.0   # no lever → no claimed saving
    return round(cost * 0.2, 2)


# ---------------------------------------------------------------------------
# Runnable SQL + savings-basis derivation (copy-paste-and-run apply statements)
# ---------------------------------------------------------------------------

# Best-effort column inference never invents names — when the query text carries
# no filter/join/group-by predicate we fall back to this explicit placeholder so
# the operator knows to fill it in rather than trusting a guessed column.
_CLUSTER_COL_PLACEHOLDER = "<pick filter/join columns>"

_COL = r"[A-Za-z_][A-Za-z0-9_]*"
# SQL tokens that can sit where a column would but are not real columns.
_SQL_NOISE = {"current_date", "current_timestamp", "now", "null", "true", "false"}


def _table_from_sql(sql: str) -> str | None:
    """Extract the first fully-qualified ``catalog.schema.table`` from a
    statement (FROM / INTO / UPDATE / TABLE), or None. Used only as a fallback
    when the row carries no ``target_table`` (e.g. the live provider leaves it
    blank and we recover it from the statement text)."""
    if not sql:
        return None
    m = re.search(
        rf"\b(?:FROM|INTO|UPDATE|TABLE)\s+({_COL}(?:\.{_COL}){{1,2}})",
        sql,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def _infer_predicate_columns(sql: str, limit: int = 4) -> list[str]:
    """Best-effort filter/join/group-by columns from a statement, de-duplicated
    and stripped of table-alias qualifiers (``m.nmi`` → ``nmi``). Returns an
    empty list when nothing is inferable — callers substitute a placeholder
    rather than fabricate a column name."""
    if not sql:
        return []
    cols: list[str] = []

    def _add(name: str) -> None:
        name = name.split(".")[-1].strip()
        if name and name.lower() not in _SQL_NOISE and name not in cols:
            cols.append(name)

    # WHERE / AND / OR predicates: <col> <op> ...
    for mo in re.finditer(
        rf"(?:WHERE|AND|OR)\s+({_COL}(?:\.{_COL})?)\s*(?:=|<|>|<=|>=|<>|!=|\bLIKE\b|\bIN\b)",
        sql,
        re.IGNORECASE,
    ):
        _add(mo.group(1))
    # JOIN ... ON <col> = <col>
    for mo in re.finditer(
        rf"\bON\s+({_COL}(?:\.{_COL})?)\s*=\s*({_COL}(?:\.{_COL})?)",
        sql,
        re.IGNORECASE,
    ):
        _add(mo.group(1))
        _add(mo.group(2))
    # GROUP BY <col>, <col>, ...
    gm = re.search(
        r"\bGROUP\s+BY\s+(.+?)(?:\bORDER\b|\bLIMIT\b|\bHAVING\b|$)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if gm:
        for part in gm.group(1).split(","):
            _add(part)
    return cols[:limit]


def apply_sql_for_query(q: dict[str, Any], insight_type: str) -> str | None:
    """Return the exact runnable statement for a query-level rec, or None when
    there is genuinely no single statement (resize is a warehouse UI action;
    rewrite needs the operator's intended column projection).

    Uses real identifiers off the row — ``target_table`` (falling back to the
    table parsed out of ``query_text``) and predicate columns inferred from the
    statement. Never invents a column name.
    """
    fqn = q.get("target_table") or _table_from_sql(q.get("query_text", ""))
    if insight_type == "cluster":
        if not fqn:
            return None
        cols = _infer_predicate_columns(q.get("query_text", ""))
        col_expr = ", ".join(cols) if cols else _CLUSTER_COL_PLACEHOLDER
        return f"ALTER TABLE {fqn} CLUSTER BY ({col_expr});"
    if insight_type == "optimize":
        return f"OPTIMIZE {fqn};" if fqn else None
    if insight_type == "vacuum":
        return f"VACUUM {fqn} RETAIN 168 HOURS;" if fqn else None
    # rewrite: the needed column projection is exactly what the SELECT * hides —
    # not derivable without operator intent, so keep the guidance in next_steps.
    # resize: warehouse sizing is a UI action, not SQL.
    return None


def savings_basis_for_query(q: dict[str, Any], insight_type: str) -> str:
    """One-sentence, honest derivation of ``est_savings_usd_month`` for a query
    rec — mirrors :func:`estimate_savings` and reuses its coefficients."""
    cost = q.get("cost_usd", 0.0)
    prune = q.get("pruning_efficiency", 1.0)
    spill = q.get("spill_gb", 0.0)
    small = q.get("small_file_ratio", 0.5)
    if insight_type == "monitor":
        return "no storage-layout or rewrite lever applies — no saving claimed"
    if insight_type == "cluster":
        return (
            f"observed monthly cost ${cost:,.0f} x {CLUSTER_PRUNE_GAIN} prune-gain "
            f"x pruning gap ({1 - prune:.0%})"
        )
    if insight_type == "resize":
        return (
            f"observed monthly cost ${cost:,.0f} x {RESIZE_SPILL_FACTOR} spill-recovery "
            f"x spill ceiling ({spill:.0f} GB / 220)"
        )
    if insight_type == "rewrite":
        return (
            f"observed monthly cost ${cost:,.0f} x {REWRITE_PROJECTION} projection-gain "
            f"x min(1, pruning gap + 0.3)"
        )
    if insight_type == "optimize":
        return (
            f"observed monthly cost ${cost:,.0f} x {OPTIMIZE_COMPACT} compaction-gain "
            f"x small-file ratio ({max(0.4, small):.2f}, floor 0.40)"
        )
    if insight_type == "vacuum":
        return (
            f"observed monthly cost ${cost:,.0f} x ({VACUUM_RECLAIM} reclaim + "
            f"{OPTIMIZE_COMPACT} x 0.3 compaction)"
        )
    return f"observed monthly cost ${cost:,.0f} x 0.20 default lever"


def impact_score(est_savings: float, runs: int, severity: float) -> int:
    """0..100 = normalised($ savings × frequency × severity)."""
    savings_term = min(1.0, est_savings / IMPACT_SAVINGS_CAP) * W_SAVINGS
    freq_term = min(1.0, runs / IMPACT_RUNS_CAP) * W_FREQUENCY
    sev_term = severity * W_SEVERITY
    return int(round(min(100.0, savings_term + freq_term + sev_term)))


def _confidence(q: dict[str, Any], insight_type: str, severity: float) -> float:
    """Confidence rises with signal strength and run count (more evidence)."""
    runs = q.get("runs", 0)
    base = 0.55 + 0.30 * severity + 0.10 * min(1.0, runs / 1000.0)
    # Pure SQL rewrites are slightly less certain (depends on query intent).
    if insight_type == "rewrite":
        base -= 0.05
    return round(min(0.97, max(0.5, base)), 2)


def recommend_query(q: dict[str, Any]) -> dict[str, Any]:
    """Produce the full recommendation object for one query row.

    Pure function of the query's statement metrics. Merges the recommendation
    fields back onto a copy of the input so the API can return an enriched row.
    """
    insight_type = classify(q)
    flags = detect_flags(q)
    severity = _severity(q)
    est_savings = estimate_savings(q, insight_type)
    runs = int(q.get("runs", 0))
    impact = impact_score(est_savings, runs, severity)

    rationale = _INSIGHT_RATIONALE[insight_type].format(
        prune=q.get("pruning_efficiency", 0.0),
        gb=q.get("bytes_read", 0.0) / 1e9,
        runs=runs,
        spill=q.get("spill_gb", 0.0),
    )
    # "monitor" WITH flags looked self-contradictory ("flag raised, but nothing
    # to do"). It isn't: the flags are runtime symptoms (queueing, a slow
    # AI/UDF statement) with no per-QUERY storage/rewrite lever — the fix lives
    # at the WAREHOUSE level (size/concurrency), which the Warehouse Monitor
    # covers, not in the SQL. Say that explicitly instead of the generic
    # "reads little data" line, which contradicted a raised flag.
    if insight_type == "monitor" and flags:
        rationale = (
            f"Flagged {_flags_phrase(flags)}, but there's no per-query "
            "storage-layout or rewrite fix — it's a warehouse sizing/concurrency "
            "symptom (or an AI/UDF statement), so the lever is on the Warehouse "
            "Monitor, not this query. Keep monitoring."
        )

    return {
        "insight_type": insight_type,
        "insight_rationale": rationale,
        "rationale": rationale,
        "est_savings_usd_month": est_savings,
        "savings_basis": savings_basis_for_query(q, insight_type),
        "confidence": _confidence(q, insight_type, severity),
        "impact": impact,
        "severity": severity,
        "flags": flags,
        "next_steps": list(_NEXT_STEPS[insight_type]),
        "apply_sql": apply_sql_for_query(q, insight_type),
    }


def enrich_query(q: dict[str, Any]) -> dict[str, Any]:
    """Return the query row merged with its recommendation fields."""
    return {**q, **recommend_query(q)}


# ---------------------------------------------------------------------------
# Optional LLM narration (never required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=== recommender self-check ===")
    sample = {
        "query_text": "SELECT * FROM acme_metering.silver.meter_reads_interval WHERE read_date > '2026-06-01'",
        "target_table": "acme_metering.silver.meter_reads_interval",
        "runs": 2400,
        "p95_s": 44.0,
        "bytes_read": 620e9,
        "pruning_efficiency": 0.08,
        "spill_gb": 3.0,
        "cost_usd": 4100.0,
    }
    rec = recommend_query(sample)
    print(json.dumps(rec, indent=2, default=str))

    # --- apply_sql / savings_basis coverage across every lever -----------------
    print("\n=== apply_sql per insight_type ===")
    cases = [
        ("cluster", {"target_table": "acme_metering.silver.meter_reads_interval",
                     "query_text": "SELECT nmi, SUM(kwh) FROM acme_metering.silver.meter_reads_interval GROUP BY nmi"}),
        ("optimize", {"target_table": "acme_prod.scada.sensor_raw", "query_text": ""}),
        ("vacuum", {"target_table": "acme_gas.silver.pipeline_flow_hourly", "query_text": ""}),
        ("rewrite", {"target_table": "acme_finance.reporting.reporting_exec_summary",
                     "query_text": "SELECT * FROM acme_finance.reporting.reporting_exec_summary"}),
        ("resize", {"target_table": "acme_metering.bronze.nem12_raw", "query_text": ""}),
        # target_table missing → recovered from the statement text
        ("cluster", {"query_text": "SELECT customer_id, invoice_total FROM acme_prod.billing.customer_invoices WHERE region = 'VIC'"}),
    ]
    for insight, q in cases:
        print(f"  {insight:9s} -> apply_sql={apply_sql_for_query(q, insight)!r}")
        print(f"            basis={savings_basis_for_query({**q, 'cost_usd': 1000.0}, insight)!r}")

    # every rec must carry both new fields
    assert "apply_sql" in rec and "savings_basis" in rec
    assert isinstance(rec["savings_basis"], str) and rec["savings_basis"]
    assert rec["apply_sql"] == "ALTER TABLE acme_metering.silver.meter_reads_interval CLUSTER BY (read_date);", rec["apply_sql"]
    assert apply_sql_for_query({"target_table": "c.s.t", "query_text": ""}, "resize") is None
    print("\nself-check OK")
