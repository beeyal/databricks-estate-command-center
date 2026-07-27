import { InfoTip } from "../shared/InfoTip";
import { Metric } from "../shared/StatTile";
import { Pill } from "../shared/Pill";
import { fmtMoney, fmtNum, fmtPct, fmtBytes, fmtSeconds, fmtGb } from "../shared/format";
import { useCurrency } from "../../store/appStore";
import type { WarehouseRow, WarehouseAdvice } from "../../types";

// Human label + tone for each advice recommendation. Tone drives the badge
// colour: green = healthy, amber = a right-sizing/idle nudge, red = active
// perf/cost pressure (spill / queueing).
const ADVICE_META: Record<string, { label: string; cls: string }> = {
  "resize-up": { label: "Size up", cls: "bg-danger/15 text-danger" },
  "add-cluster": { label: "Add clusters", cls: "bg-danger/15 text-danger" },
  "investigate-queries": { label: "Tune queries", cls: "bg-warning/15 text-warning" },
  "downsize-or-autostop": { label: "Right-size down", cls: "bg-warning/15 text-warning" },
  healthy: { label: "Healthy", cls: "bg-success/15 text-success" },
};

/** Actionable, metric-grounded advice for one warehouse — the single lever,
 *  why, and concrete next steps. */
function AdvicePanel({ advice }: { advice: WarehouseAdvice }) {
  const meta = ADVICE_META[advice.recommendation] ?? { label: advice.recommendation, cls: "bg-border/60 text-neutral" };
  return (
    <div className="rounded-lg border border-border bg-surface/40 p-3 flex flex-col gap-2 lg:col-span-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] uppercase tracking-wide text-neutral flex items-center gap-1">
          Recommendation
          <InfoTip text="What to do about this warehouse, derived from its measured metrics (queueing, spill, latency, utilisation) over the selected window. Deterministic — no model. Fixes to active pressure (spill, queueing) rank above right-sizing an idle warehouse." />
        </span>
        <Pill className={meta.cls}>{meta.label}</Pill>
      </div>
      <p className="text-xs leading-relaxed">{advice.rationale}</p>
      {advice.next_steps.length > 0 && (
        <ol className="list-decimal list-inside flex flex-col gap-1 text-xs text-neutral marker:text-neutral/60">
          {advice.next_steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

// 24-bucket hour-of-day query-volume bars. A cheap, readable "when is this
// warehouse busy" view — the peak hours are where queueing bites.
function HourHeatmap({ data }: { data: number[] }) {
  const max = Math.max(1, ...data);
  return (
    <div>
      <div className="flex items-end gap-[2px] h-16">
        {data.map((v, h) => (
          <div
            key={h}
            className="flex-1 bg-accent/70 rounded-sm min-h-[2px]"
            style={{ height: `${(v / max) * 100}%` }}
            title={`${String(h).padStart(2, "0")}:00 — ${v.toLocaleString("en-AU")} queries`}
          />
        ))}
      </div>
      <div className="flex justify-between text-[9px] text-neutral mt-1">
        <span>00:00</span>
        <span>06:00</span>
        <span>12:00</span>
        <span>18:00</span>
        <span>23:00</span>
      </div>
    </div>
  );
}

/** Expanded warehouse row: full operational metrics, hourly volume, top users
 *  and query sources. */
export function WarehouseDetail({ row }: { row: WarehouseRow }) {
  const cur = useCurrency();
  const queueTone = row.queued_ratio >= 0.15 ? "text-danger" : row.queued_ratio >= 0.08 ? "text-warning" : "";
  const spillTone = row.spill_gb >= 25 ? "text-danger" : "";
  return (
    <div className="grid lg:grid-cols-2 gap-5 text-sm">
      {/* Advice first — the "what should I do" answer, spanning both columns
          above the raw metrics that justify it. */}
      {row.advice && <AdvicePanel advice={row.advice} />}

      {/* Left: metrics + hourly volume */}
      <div className="flex flex-col gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">Operational metrics · trailing window</div>
          <div className="grid grid-cols-3 gap-x-4 gap-y-3">
            <Metric label="Runs" value={fmtNum(row.runs, { compact: true })} info="Number of queries this warehouse ran over the selected window." />
            <Metric label="p50 / p95" value={`${fmtSeconds(row.p50_s)} / ${fmtSeconds(row.p95_s)}`} info="Median and 95th-percentile query duration. A large p95 gap means a slow-run tail." />
            <Metric label="Queued" value={fmtPct(row.queued_ratio)} tone={queueTone} info="Share of runtime spent waiting at capacity before executing. High queueing means the warehouse is a bottleneck at peak — size up or add a cluster." />
            <Metric label="Spill" value={fmtGb(row.spill_gb, 1)} tone={spillTone} info="Data spilled from memory to disk. Sustained spill signals the warehouse is undersized for the workload." />
            <Metric label="Bytes read" value={fmtBytes(row.bytes_read)} info="Total data scanned by queries on this warehouse over the window." />
            <Metric label="Users" value={String(row.distinct_users)} info="Distinct users who ran a query on this warehouse." />
            <Metric label="Peak concurrency" value={String(row.peak_concurrency)} info="Highest number of queries running at once — drives how much the warehouse must scale out." />
            <Metric label="Auto-stop" value={row.auto_stop_mins ? `${row.auto_stop_mins}m` : "off"} tone={row.auto_stop_mins === 0 ? "text-warning" : ""} info="Idle timeout before the warehouse stops. 'off' means it keeps billing while idle." />
            <Metric label="Cost / mo" value={fmtMoney(row.cost_usd_month, cur, { compact: true })} info="Estimated monthly list-price cost of this warehouse." />
          </div>
        </div>
        {row.timeline.length > 0 && (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5 flex items-center gap-1">
              Query volume by hour
              <InfoTip text="Queries per hour of day (busiest hours highlight where queueing and sizing pressure occur)." />
            </div>
            <HourHeatmap data={row.timeline} />
          </div>
        )}
      </div>

      {/* Right: top users + sources */}
      <div className="flex flex-col gap-4">
        {row.top_users.length > 0 && (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">Top users by runs</div>
            <div className="flex flex-col gap-1">
              {row.top_users.map((u) => (
                <div key={u.user} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-brand-dark truncate max-w-[220px]">{u.user}</span>
                  <span className="tabular-nums text-neutral">{fmtNum(u.runs, { compact: true })} runs</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5 flex items-center gap-1">
            Query sources
            <InfoTip text="Where the queries came from — SQL editor, notebooks, scheduled jobs, dashboards, or BI tools (Power BI / Tableau / JDBC). Helps tell interactive load from automated." />
          </div>
          {row.top_sources.length > 0 ? (
            <div className="flex flex-col gap-1.5">
              {row.top_sources.map((s) => (
                <div key={s.source} className="flex items-center gap-2 text-xs">
                  <span className="w-28 truncate">{s.source}</span>
                  <div className="flex-1 h-2 rounded-full bg-border/60 overflow-hidden">
                    <div className="h-full bg-info/70" style={{ width: `${s.pct * 100}%` }} />
                  </div>
                  <span className="tabular-nums text-neutral w-10 text-right">{fmtPct(s.pct)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-neutral">Query-source attribution isn't available for this warehouse in the current window.</p>
          )}
        </div>
      </div>
    </div>
  );
}
