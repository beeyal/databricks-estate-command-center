import { InsightPill } from "../shared/Pill";
import { Metric } from "../shared/StatTile";
import { QueryFlags } from "./QueryFlags";
import { QueryRewritePanel } from "./QueryRewritePanel";
import { CopyButton } from "../shared/CopyButton";
import { fmtMoney, fmtBytes, fmtPct, fmtSeconds, fmtGb } from "../shared/format";
import { useCurrency, useFeatures } from "../../store/appStore";
import type { QueryRow } from "../../types";

/** Expanded row: full SQL, full recommendation, estimated $/mo savings, next steps. */
export function QueryDetail({ row }: { row: QueryRow }) {
  const cur = useCurrency();
  const features = useFeatures();
  const spillTone = row.spill_gb >= 20 ? "text-danger" : "";
  const pruneTone = row.pruning_efficiency < 0.3 ? "text-danger" : row.pruning_efficiency < 0.5 ? "text-warning" : "";
  return (
    <div className="grid lg:grid-cols-2 gap-5 text-sm">
      {/* Left: SQL + measured metrics */}
      <div className="flex flex-col gap-3">
        {/* Where the query came from: user + source (SQL editor / Job / Dashboard
            / Notebook / Alert) and the specific job/dashboard id when present. */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-neutral">
          <span>Run by <span className="font-mono text-brand-dark">{row.user || "unknown"}</span></span>
          <span>Source <span className="text-brand-dark">{row.src || "SQL"}</span>{row.src_id ? <span className="font-mono text-neutral"> · {row.src_id}</span> : null}</span>
          <span>On <span className="font-mono text-brand-dark">{row.warehouse}</span></span>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1">
            Full SQL {row.target_table && <span className="normal-case text-neutral/70">· {row.target_table}</span>}
          </div>
          <pre className="bg-surface rounded-lg p-3 text-xs font-mono whitespace-pre-wrap break-words overflow-auto max-h-96 border border-border">
            {row.query_text}
          </pre>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">Statement metrics</div>
          <div className="grid grid-cols-3 gap-x-4 gap-y-3">
            <Metric label="Runs" value={row.runs.toLocaleString("en-AU")} info="Number of times this query ran over the selected time range." />
            <Metric
              label="p50 / p95"
              value={`${fmtSeconds(row.p50_s)} / ${fmtSeconds(row.p95_s)}`}
              info="Median (p50) and 95th-percentile (p95) query duration. p95 shows the slow-run tail; a large gap means inconsistent performance."
            />
            <Metric label="Bytes read" value={fmtBytes(row.bytes_read)} info="Amount of data the query scanned from storage. Large reads with low pruning point to a full-table scan." />
            <Metric
              label="Pruning eff."
              value={fmtPct(row.pruning_efficiency)}
              tone={pruneTone}
              info="Fraction of data skipped by file and data skipping instead of being scanned. Higher is better; low values mean the query reads too much."
            />
            <Metric
              label="Spill"
              value={fmtGb(row.spill_gb, 1)}
              tone={spillTone}
              info="Data spilled from memory to disk during execution. Spill signals the warehouse is undersized or the query is inefficient."
            />
            <Metric
              label="Queued"
              value={fmtPct(row.queued_ratio)}
              info="Share of run time the query spent waiting for warehouse capacity before starting. High queueing means the warehouse is a bottleneck."
            />
            <Metric label="Cost / mo" value={fmtMoney(row.cost_usd, cur)} info="Estimated monthly spend on this query, based on run time and disk spill. Shown in your selected currency." />
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">Flags</div>
          <QueryFlags flags={row.flags} />
        </div>
      </div>

      {/* Right: recommendation */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <InsightPill type={row.insight_type} />
          <span className="text-xs text-neutral">confidence {(row.confidence * 100).toFixed(0)}%</span>
          <span className="text-xs text-neutral">· impact {row.impact}/100</span>
        </div>
        <p className="text-sm leading-relaxed">{row.rationale}</p>

        <div className="card bg-success/5 border-l-4 border-l-success py-2.5">
          <span className="text-[11px] uppercase tracking-wide text-neutral">Estimated savings</span>
          <div className="text-lg font-semibold text-success tabular-nums">{fmtMoney(row.est_savings_usd_month, cur)}/mo</div>
          <span className="text-[11px] text-neutral">estimated — assumes the recommended change is applied</span>
        </div>

        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">Next steps</div>
          <ol className="list-decimal list-inside text-xs text-neutral space-y-1">
            {row.next_steps.map((s, i) => (
              <li key={i}>
                <code className="text-brand-dark">{s}</code>
              </li>
            ))}
          </ol>
        </div>

        {row.apply_sql && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] uppercase tracking-wide text-neutral">Run this</span>
              <CopyButton text={row.apply_sql ?? ""} title="Copy SQL to clipboard" />
            </div>
            <pre className="text-xs bg-surface border border-border rounded-lg p-3 overflow-x-auto">
              <code className="text-brand-dark whitespace-pre">{row.apply_sql}</code>
            </pre>
          </div>
        )}

        {/* AI-proposed, EXPLAIN-validated rewrite — offered for rewrite-type
            insights, where the fix is a SQL change (not a UI/warehouse action).
            Gated on ai_narration: when that feature is off the AI rewrite panel
            is hidden entirely (was a dead/half-feature affordance otherwise). */}
        {features.ai_narration && row.insight_type === "rewrite" && <QueryRewritePanel row={row} />}
      </div>
    </div>
  );
}
