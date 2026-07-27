"""Governed tags + automatic Data Classification — the governance/data-management
demo surface.

Governed tags: Unity Catalog tags whose KEY and ALLOWED VALUES are defined
centrally (account-level) with assignment policies — they drive discovery, cost
attribution and ABAC. We show coverage per governed tag.

Auto Data Classification: a serverless capability that scans UC tables, uses ML
to infer each column's semantic type (PII etc.) and AUTO-APPLIES classification
tags — no manual tagging. It is metered (the DATA_CLASSIFICATION billing line),
so we show both the coverage AND its FinOps footprint.

Demo (seeded) module. Live mode would read the tag system tables
(system.information_schema.*_tags), the classification result tags, and the
DATA_CLASSIFICATION line in system.billing.usage.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

# Governed tags: centrally-defined key + allowed values + how widely applied.
_GOVERNED_TAGS: list[dict[str, Any]] = [
    {"tag_key": "certification_status", "allowed_values": ["Certified", "Deprecated", "Draft"],
     "coverage_pct": 0.61, "certified": 88, "deprecated": 14, "draft": 31,
     "note": "Drives the 'Certified' badge in discovery; Deprecated warns consumers off."},
    {"tag_key": "data_domain", "allowed_values": ["Markets", "Metering", "Gas", "Finance", "Shared"],
     "coverage_pct": 0.74, "note": "Powers Genie scoping + cost attribution by domain."},
    {"tag_key": "cost_center", "allowed_values": ["CC-100", "CC-200", "CC-300", "CC-400"],
     "coverage_pct": 0.52, "note": "Chargeback key — untagged objects can't be charged back."},
    {"tag_key": "sensitivity", "allowed_values": ["Public", "Internal", "Confidential", "Restricted"],
     "coverage_pct": 0.69, "note": "Feeds ABAC row/column policies; set by classification below where possible."},
]

# Auto Data Classification results.
_CLASSIFICATION: dict[str, Any] = {
    "enabled": True,
    "catalogs_enabled": 4,
    "tables_scanned": 1240,
    "tables_total": 1605,
    "columns_scanned": 18420,
    "pii_columns": 342,
    "sensitive_columns": 517,
    "coverage_pct": 1240 / 1605,
    "scan_cost_usd_month": 84.20,      # the DATA_CLASSIFICATION billing line
    "last_scan": "2026-07-08",
    # Detected semantic categories (auto-applied as tags).
    "categories": [
        {"type": "Person name", "count": 120},
        {"type": "Email address", "count": 88},
        {"type": "Postal address", "count": 60},
        {"type": "Phone number", "count": 44},
        {"type": "National ID / TFN", "count": 18},
        {"type": "Bank / card number", "count": 12},
    ],
    # Tables with the most sensitive columns detected.
    "top_sensitive_tables": [
        {"fqn": "acme_prod.billing.customer_invoices", "pii_columns": 9, "categories": ["Person name", "Email address", "Bank / card number"], "sensitivity": "Restricted"},
        {"fqn": "acme_metering.silver.meter_reads_interval", "pii_columns": 5, "categories": ["National ID / TFN", "Postal address"], "sensitivity": "Confidential"},
        {"fqn": "acme_markets.gold.trade_positions", "pii_columns": 3, "categories": ["Person name", "Email address"], "sensitivity": "Confidential"},
        {"fqn": "acme_finance.reporting.reporting_exec_summary", "pii_columns": 2, "categories": ["Person name"], "sensitivity": "Internal"},
    ],
    # Tables NOT yet classified that look risky (candidates to enable/scan).
    "unclassified_high_risk": [
        {"fqn": "acme_prod.scada.sensor_raw", "reason": "In a production catalog, never scanned — enable auto-classification"},
        {"fqn": "acme_shared.sandbox.adhoc_2025", "reason": "Ad-hoc copy, likely contains copied PII — scan or archive"},
    ],
}


@lru_cache(maxsize=1)
def get_classification() -> dict[str, Any]:
    tags = []
    for t in _GOVERNED_TAGS:
        tags.append({**t, "coverage_pct": round(t["coverage_pct"], 3)})
    return {
        "governed_tags": tags,
        "classification": {**_CLASSIFICATION, "coverage_pct": round(_CLASSIFICATION["coverage_pct"], 3)},
        "summary": {
            "num_governed_tags": len(tags),
            "avg_tag_coverage": round(sum(t["coverage_pct"] for t in tags) / len(tags), 3),
            "pii_columns": _CLASSIFICATION["pii_columns"],
            "classification_coverage_pct": round(_CLASSIFICATION["coverage_pct"], 3),
            "scan_cost_usd_month": _CLASSIFICATION["scan_cost_usd_month"],
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_classification()["summary"], indent=2))
