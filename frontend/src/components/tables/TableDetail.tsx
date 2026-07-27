import type { TableRow } from "../../types";
import { InsightPill } from "../shared/Pill";
import { fmtPct, fmtBytes } from "../shared/format";
import { InfoTip } from "../shared/InfoTip";

function CodeBlock({ lines }: { lines: string[] }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-border bg-surface/70 p-3 text-[11px] leading-relaxed font-mono text-brand-dark">
      {lines.join("\n")}
    </pre>
  );
}

function StepList({ steps, ordered = true }: { steps: string[]; ordered?: boolean }) {
  const cls = "text-xs text-neutral space-y-1 marker:text-accent";
  return ordered ? (
    <ol className={`list-decimal list-inside ${cls}`}>
      {steps.map((s, i) => (
        <li key={i}>{s}</li>
      ))}
    </ol>
  ) : (
    <ul className={`list-disc list-inside ${cls}`}>
      {steps.map((s, i) => (
        <li key={i}>{s}</li>
      ))}
    </ul>
  );
}

/**
 * Expandable per-table detail. For EXTERNAL tables it shows the conversion
 * path (CTAS / DEEP CLONE) + benefits unlocked. For MANAGED tables without
 * PO/LC it shows the enable-PO recommendation + the DLT known-issue caveat.
 * Both show the estimated-savings working.
 */
export function TableDetail({ row }: { row: TableRow }) {
  const isExternal = row.table_type === "EXTERNAL";
  return (
    <div className="flex flex-col gap-4 text-sm">
      {/* Recommendation header */}
      <div className="flex flex-wrap items-center gap-2">
        <InsightPill type={row.recommendation} />
        <span className="text-neutral">{row.rationale}</span>
      </div>

      {/* Two-column body: left = action/path, right = savings */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="flex flex-col gap-3">
          {isExternal && row.conversion_path && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-1.5">
                Conversion path — external → managed
                <span className="ml-2 pill bg-insight-convert/15 text-insight-convert">
                  recommended: {row.conversion_path.recommended}
                </span>
              </div>
              <CodeBlock lines={row.conversion_path.steps} />
            </div>
          )}

          {row.benefits.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-1.5">
                {isExternal ? "Benefits unlocked" : "Expected benefit"}
              </div>
              <StepList steps={row.benefits} ordered={false} />
            </div>
          )}

          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-1.5">Next steps</div>
            <StepList steps={row.next_steps} />
          </div>

          {row.caveat && (() => {
            // The caveat field carries two kinds of text: a genuine known-issue
            // warning (e.g. the DLT CLUSTER BY AUTO / DBR requirement) vs a plain
            // data-source/method explanation (the live "where this came from"
            // note). Only style the former as a warning; the latter is neutral.
            const isWarning = /\b(caveat|known.issue|requires|not supported|fails|DBR|ES-\d)/i.test(row.caveat!);
            return isWarning ? (
              <div className="rounded-lg border-l-4 border-l-warning bg-warning/10 p-3">
                <div className="text-xs font-semibold text-warning mb-1">Known-issue caveat</div>
                <p className="text-xs text-neutral leading-relaxed">{row.caveat}</p>
              </div>
            ) : (
              <div className="rounded-lg border border-border bg-surface/40 p-3">
                <div className="text-xs font-semibold text-neutral mb-1">Data source &amp; method</div>
                <p className="text-xs text-neutral leading-relaxed">{row.caveat}</p>
              </div>
            );
          })()}
        </div>

        <div className="flex flex-col gap-3">
          {/* Real Delta metadata facts only (DESCRIBE DETAIL via the nightly
              census). No estimated-$ savings breakdown — per-table scan cost
              isn't in system tables. */}
          <div className="rounded-lg border border-border bg-surface/50 p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-neutral mb-2">Table facts</div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              <Stat label="Size" value={row.size_bytes ? fmtBytes(row.size_bytes) : "—"} info="Total storage for this table from DESCRIBE DETAIL." />
              <Stat label="Files" value={row.num_files ? row.num_files.toLocaleString() : "—"} info="Number of data files backing the table." />
              <Stat
                label="Avg file size"
                value={row.num_files ? `${row.avg_file_mb} MB` : "—"}
                warn={!!row.num_files && row.avg_file_mb < 128}
                info="Average size of the table's data files. Files well below the optimal size (around 128 MB) mean more files to open per scan."
              />
              <Stat
                label="Small-file ratio"
                value={row.num_files ? fmtPct(row.small_file_ratio) : "—"}
                warn={!!row.num_files && row.small_file_ratio > 0.4}
                info="Share of data files below the optimal size. High fragmentation slows scans and wastes compute; OPTIMIZE compacts them."
              />
              <Stat
                label="Partitioned"
                value={row.partitioned ? "yes" : "no"}
                info="Whether data is split into partitions on disk. Liquid Clustering is the modern alternative that adapts automatically."
              />
              <Stat
                label="Clustered (LC)"
                value={row.clustered ? "yes" : "no"}
                info="Liquid Clustering: an adaptive data layout replacing partitioning, keeping frequently filtered data together for faster, cheaper scans."
              />
              <Stat
                label="Predictive Opt."
                value={row.po_enabled ? "enabled" : "off"}
                warn={!row.po_enabled && !isExternal}
                info="Predictive Optimization: Databricks automatically runs OPTIMIZE and VACUUM for you, keeping maintenance and cost under control."
              />
            </dl>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  warn = false,
  info,
}: {
  label: string;
  value: string;
  warn?: boolean;
  info?: string;
}) {
  return (
    <>
      <dt className="text-neutral inline-flex items-center gap-1">
        {label}
        {info && <InfoTip text={info} label={`What is ${label}?`} />}
      </dt>
      <dd className={`text-right tabular-nums ${warn ? "text-warning font-medium" : ""}`}>{value}</dd>
    </>
  );
}
