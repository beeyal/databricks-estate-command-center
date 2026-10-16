"""Workload classification (Chris feedback #2e / #5).

Chris's operational lens is not the Databricks product view (Jobs / All-Purpose /
SQL) but the *workload type*: batch, streaming, interactive / ad-hoc, ML/AI. This
module maps the signals available from system tables + metadata — the billing
product/SKU, and job/cluster names + tags — onto those workload buckets, so cost
can be re-expressed the way AEMO thinks about it.

It is deliberately a transparent heuristic (no model): a keyword/rule map, config-
overridable, with an honest "Other" fallback for anything it can't place. Two
entry points:

  classify_product(product)         — bucket a billing_origin_product cost-driver
  classify_job(name, product=None)  — bucket a job/pipeline from its name (+ its
                                      compute product as a fallback signal)

Buckets: Batch, Streaming, Interactive / Ad-hoc, ML / AI, Other.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

BATCH = "Batch"
STREAMING = "Streaming"
INTERACTIVE = "Interactive / Ad-hoc"
ML_AI = "ML / AI"
OTHER = "Other"

WORKLOADS = [BATCH, STREAMING, INTERACTIVE, ML_AI, OTHER]

# billing_origin_product (the app's cost-driver names) → workload bucket. Covers
# the demo _PRODUCTS + the common live billing_origin_product values.
_PRODUCT_MAP: dict[str, str] = {
    "serverless jobs": BATCH,
    "jobs classic": BATCH,
    "jobs": BATCH,
    "dlt / lakeflow": STREAMING,
    "dlt": STREAMING,
    "lakeflow": STREAMING,
    "delta live tables": STREAMING,
    "serverless sql": INTERACTIVE,
    "sql warehouse (classic)": INTERACTIVE,
    "sql": INTERACTIVE,
    "all-purpose compute": INTERACTIVE,
    "all-purpose": INTERACTIVE,
    "interactive": INTERACTIVE,
    # Bare job compute-type fallbacks (used by classify_job when the name matches
    # nothing): a job on jobs/serverless compute is batch by default; all-purpose
    # jobs are interactive. These never affect the Overview lens, which keys off
    # full product labels ("Serverless SQL", "Serverless Jobs", …).
    "jobs (classic)": BATCH,
    "serverless": BATCH,
    "model serving": ML_AI,
    "foundation model apis": ML_AI,
    "ai gateway": ML_AI,
    "vector search": ML_AI,
    "mosaic ai": ML_AI,
    # Platform/other services — no single workload; classified Other so they never
    # masquerade as one of the operational workload types.
    "apps": OTHER,
    "lakebase": OTHER,
    "predictive optimization": OTHER,
    "data quality monitoring": OTHER,
}

# Job/pipeline NAME keyword → workload (checked in order; first hit wins). Names
# and tags are the richest operational signal AEMO has.
_NAME_RULES: list[tuple[str, str]] = [
    (r"stream|kafka|kinesis|cdc|continuous|realtime|real-time|event", STREAMING),
    (r"train|training|ml[-_]|model|forecast|predict|inference|feature[-_]?store|embedding", ML_AI),
    (r"batch|etl|elt|ingest|load|nightly|daily|hourly|settlement|backfill|sync|export|report|refresh|aggregate|bronze|silver|gold", BATCH),
    (r"adhoc|ad-hoc|notebook|explore|scratch|interactive|sandbox|query", INTERACTIVE),
]


@lru_cache(maxsize=1)
def _name_rules() -> list[tuple[re.Pattern[str], str]]:
    """Parsed name rules, with an optional config prepend (FINOPS_WORKLOAD_RULES =
    ``regex:Bucket;…``) so a customer can steer classification for their naming."""
    rules: list[tuple[re.Pattern[str], str]] = []
    raw = os.environ.get("FINOPS_WORKLOAD_RULES") or ""
    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        pat, bucket = part.rsplit(":", 1)
        pat, bucket = pat.strip(), bucket.strip()
        if pat and bucket in WORKLOADS:
            try:
                rules.append((re.compile(pat, re.IGNORECASE), bucket))
            except re.error:
                pass
    for pat, bucket in _NAME_RULES:
        rules.append((re.compile(pat, re.IGNORECASE), bucket))
    return rules


def classify_product(product: str) -> str:
    """Bucket a billing_origin_product cost-driver into a workload type."""
    return _PRODUCT_MAP.get((product or "").strip().lower(), OTHER)


def classify_job(name: str, product: str | None = None) -> str:
    """Bucket a job / pipeline by its name; fall back to its compute product."""
    for rx, bucket in _name_rules():
        if rx.search(name or ""):
            return bucket
    if product:
        p = classify_product(product)
        if p != OTHER:
            return p
    return OTHER


def reset_cache() -> None:
    _name_rules.cache_clear()
