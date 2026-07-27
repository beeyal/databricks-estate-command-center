import { fetchClassification } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useCurrency } from "../../store/appStore";
import { fmtMoney, fmtNum, fmtPct } from "../shared/format";
import { Pill } from "../shared/Pill";
import { InfoTip } from "../shared/InfoTip";
import { LoadingCard, ErrorCard } from "../layout/PageShell";

// Governed tags coverage + automatic Data Classification (PII detection + scan
// cost). Rendered at the foot of the Governance tab.
export function GovernedTagsClassification() {
  const cur = useCurrency();
  const { data, loading, error } = useApi(() => fetchClassification(), []);
  const c = data?.data;
  // This is a secondary fetch below the already-loaded Governance tiles, so show
  // an explicit loading / error cue rather than a silent blank gap while it
  // resolves (the classification scan can take a moment on a large estate).
  if (loading && !c) return <LoadingCard label="Loading governed tags & classification…" />;
  if (error && !c) {
    // A missing grant on the tag/classification system tables (or a live read
    // that couldn't reach them) isn't an app error — this is an OPTIONAL panel at
    // the foot of Governance, so degrade to a calm notice rather than a red card
    // (mirrors the Data Quality tab). Genuine unexpected errors still surface.
    const needsGrant = /USE SCHEMA|information_schema|classification|column_tags|live read failed/i.test(error);
    return needsGrant ? (
      <div className="card border-l-4 border-l-info flex flex-col gap-1.5 text-sm">
        <div className="font-medium text-brand-dark">Governed tags & classification aren't available yet</div>
        <p className="text-neutral leading-relaxed">
          This panel reads tag &amp; classification metadata from{" "}
          <span className="font-mono">system.information_schema</span>. Grant the app{" "}
          <span className="font-mono">SELECT</span> on that schema (and enable auto-classification if you
          want PII detection) and it fills in automatically. Nothing else on this page is affected.
        </p>
      </div>
    ) : (
      <ErrorCard message={error} />
    );
  }
  if (!c) return null;
  const cls = c.classification;
  const maxCat = Math.max(1, ...cls.categories.map((x) => x.count));

  return (
    <div className="flex flex-col gap-5">
      {/* Governed tags */}
      <div className="card flex flex-col gap-3">
        <div className="text-sm font-semibold flex items-center gap-1.5">
          Governed tags — coverage
          <InfoTip text="Unity Catalog governed tags have a centrally-defined key and allowed values, with policies on who can assign them. They drive discovery, cost attribution and attribute-based access control (ABAC). Coverage = share of objects carrying the tag." />
        </div>
        <div className="flex flex-col gap-3">
          {c.governed_tags.map((t) => (
            <div key={t.tag_key} className="flex flex-col gap-1">
              <div className="flex items-center justify-between text-xs gap-2 flex-wrap">
                <span className="flex items-center gap-1.5">
                  <span className="font-mono text-brand-dark">{t.tag_key}</span>
                  {t.allowed_values.map((v) => (
                    <Pill key={v} className="bg-border/60 text-neutral">{v}</Pill>
                  ))}
                </span>
                <span className="tabular-nums text-neutral shrink-0">
                  {fmtPct(t.coverage_pct, 0)} tagged
                  {t.tag_key === "certification_status" && t.certified != null && (
                    <span className="ml-2"><span className="text-success">{t.certified} Certified</span> · <span className="text-danger">{t.deprecated} Deprecated</span></span>
                  )}
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-border/60 overflow-hidden">
                <div className={`h-full rounded-full ${t.coverage_pct >= 0.7 ? "bg-success" : t.coverage_pct >= 0.5 ? "bg-warning" : "bg-danger"}`} style={{ width: `${t.coverage_pct * 100}%` }} />
              </div>
              {t.note && <div className="text-[11px] text-neutral">{t.note}</div>}
            </div>
          ))}
        </div>
      </div>

      {/* Data Classification (auto) */}
      <div className="card flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-2 flex-wrap">
          <div className="text-sm font-semibold flex items-center gap-1.5">
            Data Classification (auto)
            <InfoTip text="Databricks automatic data classification runs serverless ML scans over your tables, infers each column's semantic type (PII such as name, email, card number), and AUTO-APPLIES classification tags — no manual tagging. Those tags then drive masking and ABAC. It is metered as the DATA_CLASSIFICATION billing line." />
          </div>
          <span className="text-xs text-neutral tabular-nums">
            {cls.catalogs_enabled} catalogs · {fmtMoney(cls.scan_cost_usd_month, cur)}/mo scan cost
          </span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="rounded-lg border border-border bg-surface/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-neutral">Tables scanned</div>
            <div className="text-lg font-semibold tabular-nums">{fmtNum(cls.tables_scanned)}<span className="text-neutral text-xs"> / {fmtNum(cls.tables_total)}</span></div>
            <div className="text-[11px] text-neutral">{fmtPct(cls.coverage_pct, 0)} coverage</div>
          </div>
          <div className="rounded-lg border border-border bg-surface/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-neutral">PII columns</div>
            <div className="text-lg font-semibold tabular-nums text-danger">{fmtNum(cls.pii_columns)}</div>
            <div className="text-[11px] text-neutral">auto-classified (class.*)</div>
          </div>
          <div className="rounded-lg border border-border bg-surface/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-neutral">Sensitive columns</div>
            <div className="text-lg font-semibold tabular-nums text-warning">{fmtNum(cls.sensitive_columns)}</div>
            <div className="text-[11px] text-neutral">auto-tagged</div>
          </div>
          <div className="rounded-lg border border-border bg-surface/40 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-neutral">Scan cost / mo</div>
            <div className="text-lg font-semibold tabular-nums">{fmtMoney(cls.scan_cost_usd_month, cur)}</div>
            <div className="text-[11px] text-neutral">DATA_CLASSIFICATION</div>
          </div>
        </div>

        {/* Detected categories */}
        <div className="flex flex-col gap-1.5">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral">Detected PII / sensitive categories</div>
          {cls.categories.map((cat) => (
            <div key={cat.type} className="flex items-center gap-2 text-xs">
              <span className="w-36 shrink-0">{cat.type}</span>
              <div className="h-1.5 flex-1 rounded-full bg-border/60 overflow-hidden">
                <div className="h-full rounded-full bg-info" style={{ width: `${(cat.count / maxCat) * 100}%` }} />
              </div>
              <span className="tabular-nums text-neutral w-10 text-right">{cat.count}</span>
            </div>
          ))}
        </div>

        {/* Top sensitive tables */}
        <div className="flex flex-col gap-1.5">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral">Most sensitive tables</div>
          {cls.top_sensitive_tables.map((t) => (
            <div key={t.fqn} className="flex items-center justify-between gap-2 text-xs">
              <span className="font-mono text-brand-dark truncate">{t.fqn}</span>
              <span className="flex items-center gap-1.5 shrink-0">
                {t.categories.map((cat) => <Pill key={cat} className="bg-danger/10 text-danger">{cat}</Pill>)}
                <span className="tabular-nums text-neutral">{t.pii_columns} PII cols</span>
                {t.sensitivity && <Pill className="bg-warning/15 text-warning">{t.sensitivity}</Pill>}
              </span>
            </div>
          ))}
        </div>

        {cls.unclassified_high_risk.length > 0 && (
          <div className="text-[11px] text-neutral border-t border-border pt-2">
            <span className="text-warning font-medium">Enable / scan:</span>{" "}
            {cls.unclassified_high_risk.map((u, i) => (
              <span key={u.fqn}>{i > 0 && "; "}<span className="font-mono text-brand-dark">{u.fqn}</span> — {u.reason}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
