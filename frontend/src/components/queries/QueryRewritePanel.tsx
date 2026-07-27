import { useState } from "react";
import { postQueryRewrite } from "../../api/client";
import { InfoTip } from "../shared/InfoTip";
import { Pill } from "../shared/Pill";
import { CopyButton } from "../shared/CopyButton";
import type { QueryRewrite, QueryRow, RewriteStatus } from "../../types";

// Per-status badge + one-line meaning. The status is the honesty contract of
// the feature: only "validated" means EXPLAIN bound the rewrite to the real
// schema; "rejected" means we tried and it failed (so we do NOT show it as
// runnable); "unvalidated" means we couldn't check it against a warehouse.
const STATUS_META: Record<RewriteStatus, { label: string; cls: string; note: string }> = {
  validated: {
    label: "EXPLAIN-validated",
    cls: "bg-success/15 text-success",
    note: "EXPLAIN succeeded — the rewrite parses and binds to your schema.",
  },
  unvalidated: {
    label: "Couldn't auto-validate",
    cls: "bg-warning/15 text-warning",
    // Two reasons land here: no warehouse to EXPLAIN against, OR the query uses
    // bind parameters / session state EXPLAIN can't supply. Either way the
    // rewrite IS shown — just labelled not-checked. The exact reason is in
    // validation_detail below.
    note: "A rewrite was generated but couldn't be auto-validated against your schema — review it and run EXPLAIN yourself before using it.",
  },
  rejected: {
    label: "Rejected by EXPLAIN",
    cls: "bg-danger/15 text-danger",
    // Don't assert the CAUSE here — EXPLAIN can reject a rewrite either because
    // it didn't bind to the schema (wrong column/table) OR because it didn't
    // parse (the model produced malformed/truncated SQL). The exact reason is in
    // validation_detail below.
    note: "EXPLAIN couldn't confirm this rewrite is safe to run, so it's not offered as runnable — see why below.",
  },
  unavailable: {
    label: "AI rewrite off",
    cls: "bg-border/60 text-neutral",
    note: "No in-region model endpoint is configured, so no AI rewrite was generated. The deterministic next steps still apply.",
  },
  not_applicable: {
    label: "Not a SELECT",
    cls: "bg-border/60 text-neutral",
    note: "Automated rewrite is offered only for read-only SELECT queries.",
  },
};

function PlanBlock({ label, plan }: { label: string; plan: string }) {
  return (
    <div className="flex-1 min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-neutral mb-1">{label}</div>
      <pre className="text-[11px] bg-surface border border-border rounded-lg p-2 overflow-x-auto whitespace-pre">
        <code className="text-brand-dark">{plan}</code>
      </pre>
    </div>
  );
}

/** On-demand AI rewrite for one query, validated with EXPLAIN against the
 *  customer's own warehouse. Only surfaced for rewrite-type insights. */
export function QueryRewritePanel({ row }: { row: QueryRow }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryRewrite | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const env = await postQueryRewrite(row);
      setResult(env.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const meta = result ? STATUS_META[result.status] : null;
  // Only "validated"/"unvalidated" carry a candidate worth copying; rejected
  // deliberately withholds the SQL so nobody pastes an unbound query.
  const showSql = result && (result.status === "validated" || result.status === "unvalidated") && result.candidate_sql;

  return (
    <div className="rounded-lg border border-border bg-surface/40 p-3 flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-wide text-neutral flex items-center gap-1">
          AI rewrite
          <InfoTip text="Asks your in-region model to rewrite this query to scan less data, then validates the result with EXPLAIN against your own warehouse. A rewrite that doesn't bind to your real schema is rejected, not shown. EXPLAIN proves it runs — not that the results are identical, so review before running." />
        </span>
        <button
          type="button"
          onClick={run}
          disabled={loading}
          className="pill text-[11px] px-2.5 py-0.5 border border-border text-neutral hover:text-brand-dark hover:bg-surface disabled:opacity-50"
        >
          {loading ? "Validating…" : result ? "Regenerate" : "Suggest & validate rewrite"}
        </button>
      </div>

      {error && <div className="text-xs text-danger">{error}</div>}

      {meta && (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            <Pill className={meta.cls}>{meta.label}</Pill>
            <span className="text-[11px] text-neutral">{meta.note}</span>
          </div>

          {result!.explanation && <p className="text-xs leading-relaxed">{result!.explanation}</p>}

          {showSql && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase tracking-wide text-neutral">Rewritten query</span>
                <CopyButton text={result!.candidate_sql ?? ""} title="Copy SQL to clipboard" />
              </div>
              <pre className="text-xs bg-surface border border-border rounded-lg p-3 overflow-x-auto">
                <code className="text-brand-dark whitespace-pre-wrap break-words">{result!.candidate_sql}</code>
              </pre>
            </div>
          )}

          {result!.validation_detail && (
            <div className="text-[11px] text-neutral">{result!.validation_detail}</div>
          )}

          {(result!.plan_before || result!.plan_after) && (
            <div className="flex flex-col sm:flex-row gap-3">
              {result!.plan_before && <PlanBlock label="Plan — before" plan={result!.plan_before} />}
              {result!.plan_after && <PlanBlock label="Plan — after" plan={result!.plan_after} />}
            </div>
          )}

          <div className="text-[11px] text-warning flex gap-1.5 border-t border-border pt-2">
            <span aria-hidden>·</span>
            <span>{result!.caveat}</span>
          </div>
        </>
      )}
    </div>
  );
}
