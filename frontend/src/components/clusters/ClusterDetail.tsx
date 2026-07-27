import { InfoTip } from "../shared/InfoTip";
import { Metric } from "../shared/StatTile";
import { fmtMoney, fmtNum, fmtPct } from "../shared/format";
import { useCurrency } from "../../store/appStore";
import type { ClusterRow } from "../../types";

// 24-bucket CPU-by-hour sketch. Low bars across the day = under-utilised.
function CpuByHour({ data }: { data: number[] }) {
  return (
    <div>
      <div className="flex items-end gap-[2px] h-14">
        {data.map((v, h) => (
          <div
            key={h}
            className={`flex-1 rounded-sm min-h-[2px] ${v >= 0.7 ? "bg-danger/70" : v >= 0.4 ? "bg-warning/70" : "bg-success/60"}`}
            style={{ height: `${Math.max(4, v * 100)}%` }}
            title={`${String(h).padStart(2, "0")}:00 — ${(v * 100).toFixed(0)}% CPU`}
          />
        ))}
      </div>
      <div className="flex justify-between text-[9px] text-neutral mt-1">
        <span>00:00</span><span>12:00</span><span>23:00</span>
      </div>
    </div>
  );
}

/** Expanded cluster row: config + utilisation + the specific findings and the
 *  estimated recoverable spend, with an honest "how we estimate" note. */
export function ClusterDetail({ row, win = "30d" }: { row: ClusterRow; win?: string }) {
  const cur = useCurrency();
  const cpuTone = row.cpu_mean < 0.12 ? "text-danger" : row.cpu_mean < 0.2 ? "text-warning" : "";
  const memTone = row.mem_mean >= 0.9 ? "text-danger" : "";
  return (
    <div className="grid lg:grid-cols-2 gap-5 text-sm">
      {/* Left: config + utilisation */}
      <div className="flex flex-col gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">
            Configuration {row.creator && <span className="normal-case text-neutral/70">· {row.creator}</span>}
          </div>
          <div className="grid grid-cols-3 gap-x-4 gap-y-3">
            <Metric label="Node type" value={row.node_type || "—"} info="The worker node instance type — drives cost per node-hour." />
            <Metric label="Workers" value={row.autoscale ? `${row.workers_min}–${row.workers_max} (auto)` : `${row.workers_max} (fixed)`} tone={!row.autoscale && row.workers_max >= 2 ? "text-warning" : ""} info="Worker count. 'fixed' means autoscaling is off, so it pays for peak sizing even at low load." />
            <Metric label="Auto-stop" value={row.auto_stop_mins ? `${row.auto_stop_mins}m` : "off"} tone={row.auto_stop_mins === 0 ? "text-danger" : row.auto_stop_mins >= 120 ? "text-warning" : ""} info="Idle timeout before the cluster terminates. 'off' means it keeps billing while idle — usually the biggest single waste." />
            <Metric label={`Mean CPU (${win})`} value={fmtPct(row.cpu_mean)} tone={cpuTone} info={`Average CPU utilisation across nodes over the last ${win} (from node_timeline). Persistently low CPU means the cluster is larger or longer-lived than the work needs.`} />
            <Metric label="Mean memory" value={fmtPct(row.mem_mean)} tone={memTone} info={`Average memory utilisation over the last ${win}. Sustained >90% risks spill/OOM — a memory-optimised node may help.`} />
            <Metric label={`Idle time (${win})`} value={fmtPct(row.idle_frac)} tone={row.idle_frac >= 0.35 ? "text-warning" : ""} info={`Fraction of uptime the cluster was effectively idle (near-zero CPU) over the last ${win}.`} />
            {row.uptime_hours_month > 0 && <Metric label={`Uptime (${win})`} value={`${fmtNum(row.uptime_hours_month)}h`} info={`Hours the cluster was running over the last ${win}.`} />}
            {row.dbus_month > 0 && <Metric label={`DBUs (${win})`} value={fmtNum(row.dbus_month, { compact: true })} info={`Databricks Units consumed over the last ${win}.`} />}
            <Metric label={`Cost (${win})`} value={fmtMoney(row.cost_usd_month, cur, { compact: true })} info={`Estimated list-price cost of this cluster over the last ${win}.`} />
          </div>
        </div>
        {row.cpu_timeline.length > 0 && (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5 flex items-center gap-1">
              CPU by hour
              <InfoTip text="Mean CPU utilisation per hour of day. Low bars across the day confirm the cluster is under-used." />
            </div>
            <CpuByHour data={row.cpu_timeline} />
          </div>
        )}
      </div>

      {/* Right: findings + estimated savings */}
      <div className="flex flex-col gap-3">
        <div className="card bg-success/5 border-l-4 border-l-success py-2.5">
          <span className="text-[11px] uppercase tracking-wide text-neutral">Estimated recoverable ({win})</span>
          <div className="text-lg font-semibold text-success tabular-nums">{fmtMoney(row.potential_savings_usd_month, cur)}</div>
          <span className="text-[11px] text-neutral">estimated over the last {win} — a single dominant lever, not additive across findings; confirm before acting</span>
        </div>

        <div>
          <div className="text-[11px] uppercase tracking-wide text-neutral mb-1.5">What we observed</div>
          {row.findings.length === 0 ? (
            <p className="text-xs text-neutral">No efficiency findings — this cluster looks right-sized and well-configured.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {row.findings.map((f, i) => (
                <li key={i} className="flex flex-col gap-0.5 border-l-2 border-l-warning pl-2.5">
                  <span className="text-xs font-medium text-brand-dark">{f.label}</span>
                  <span className="text-[11px] text-neutral leading-relaxed">{f.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="text-[11px] text-neutral border-t border-border pt-2">
          Observations from cluster-level telemetry (config, node_timeline CPU/memory, billing). This view does not analyse individual queries — cluster query telemetry isn't in system tables; use the Spark UI for per-query tuning.
        </div>
      </div>
    </div>
  );
}
