import { useCurrency } from "../../store/appStore";
import { useApi } from "../../hooks/useApi";
import { fetchJobDetail } from "../../api/client";
import { InfoTip } from "../shared/InfoTip";
import { StatTile } from "../shared/StatTile";
import { Pill } from "../shared/Pill";
import { Sparkline } from "../shared/Sparkline";
import { fmtMoney, fmtPct, fmtFixed } from "../shared/format";
import type { JobRow } from "../../types";

function fmtDur(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)}h`;
  if (s >= 60) return `${(s / 60).toFixed(1)}m`;
  if (s >= 1) return `${s.toFixed(1)}s`;
  if (s > 0) return `${Math.round(s * 1000)}ms`;   // sub-second → milliseconds
  return "0";
}


// Setup + queue vs execution split — where each run's wall-clock goes (a fact).
function WaitSplit({ job }: { job: JobRow }) {
  const b = job.detail.warmup_breakdown;
  const total = Math.max(1, b.setup_s + b.queue_s + b.exec_s + b.cleanup_s);
  const seg = [
    { label: "Setup / warm-up", s: b.setup_s, color: "#F59E0B" },
    { label: "Queue (wait for compute)", s: b.queue_s, color: "#C0392B" },
    { label: "Execution", s: b.exec_s, color: "#1B8A4A" },
    { label: "Cleanup", s: b.cleanup_s, color: "#8B5CF6" },
  ];
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full border border-border">
        {seg.map((s) => (
          <div key={s.label} title={`${s.label} · ${fmtDur(s.s)}`} style={{ width: `${(s.s / total) * 100}%`, backgroundColor: s.color }} />
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
        {seg.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-xs min-w-0">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: s.color }} />
            <span className="truncate text-brand-dark">{s.label}</span>
            <span className="ml-auto shrink-0 tabular-nums text-neutral">{fmtDur(s.s)}</span>
            <span className="w-10 shrink-0 text-right tabular-nums text-neutral">{fmtPct(s.s / total)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function JobDetail({ job }: { job: JobRow }) {
  const cur = useCurrency();
  const d = job.detail;

  // Recent runs + termination codes AND the duration/cost trend arrays are
  // shipped inline in demo, but the live list endpoint leaves them empty — the
  // per-run detail isn't bulk-scannable, and the trend arrays are dropped from
  // the list payload for size (fetched per-job here instead). Fetch on expand.
  // `loading` lets us show "loading" rather than a misleading empty-state.
  const needsFetch =
    d.recent_runs.length === 0 &&
    d.termination_codes.length === 0 &&
    d.duration_trend.length === 0 &&
    d.cost_trend.length === 0;
  const { data: drill, loading: drillLoading, error: drillError } = useApi(
    () => (needsFetch ? fetchJobDetail(job.workspace_id, job.job_id) : Promise.resolve(null)),
    [job.workspace_id, job.job_id, needsFetch],
  );
  // If the lazy per-job fetch FAILED (live 503), the panel would otherwise fall
  // through to reassuring empty states ("No material failures", "Not enough
  // history") — which reads as fact when the data actually didn't load. Surface
  // a small honest note instead.
  const drillFailed = needsFetch && !!drillError && !drill;
  const recentRuns = d.recent_runs.length ? d.recent_runs : (drill?.data?.recent_runs ?? []);
  const terminationCodes = d.termination_codes.length ? d.termination_codes : (drill?.data?.termination_codes ?? []);
  // Trends: inline (demo) if present, else from the lazy fetch (live).
  const durTrend = (d.duration_trend.length ? d.duration_trend : (drill?.data?.duration_trend ?? [])).map((p) => p.avg_s);
  const costTrend = (d.cost_trend.length ? d.cost_trend : (drill?.data?.cost_trend ?? [])).map((p) => p.cost_usd);

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-neutral">{job.job_name}</span>
        <Pill className="bg-border/60 text-neutral">{job.compute_type}</Pill>
        {job.dlt && <Pill className="bg-info/15 text-info">DLT</Pill>}
        {job.high_setup_queue && <Pill className="bg-accent/15 text-accent">high setup+queue</Pill>}
        <span className="ml-auto text-xs text-neutral">{job.workspace} · job_id {job.job_id}</span>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-4 gap-2">
        <StatTile label="Cost / mo" value={fmtMoney(job.cost_usd_month, cur, { compact: true })} tone="accent" info="Monthly list-price cost attributed to this job (DBUs consumed × effective list rate). Live mode: only jobs-compute + serverless jobs carry job-attributed billing." />
        <StatTile label="Cost / run" value={fmtMoney(job.cost_per_run, cur)} info="Monthly cost ÷ runs — the average list-price cost of a single run." />
        <StatTile label="MoM cost Δ" value={Math.abs(job.mom_cost_pct) < 0.005 ? "flat" : `${job.mom_cost_pct > 0 ? "+" : "−"}${fmtPct(Math.abs(job.mom_cost_pct))}`} tone={job.mom_cost_pct > 0.005 ? "danger" : job.mom_cost_pct < -0.005 ? "success" : "neutral"} info="Month-over-month change in this job's cost (exact, from the 6-month cost trend)." />
        <StatTile label="Failed-run $ / mo" value={fmtMoney(job.failed_run_cost_usd_month, cur, { compact: true })} tone="danger" info="Estimated cost of failed runs this month (failed runs × avg cost/run). An estimate — actual waste is at or below this since failed runs often die mid-execution." />
        <StatTile label="Runs (30d)" value={job.runs.toLocaleString("en-AU")} info="Terminal job runs in the last 30 days." />
        <StatTile label="Fail rate" value={fmtPct(job.fail_rate)} tone={job.fail_rate >= 0.15 ? "danger" : "neutral"} info="Share of runs that FAILED / TIMED_OUT / CANCELED over 30 days. Red at or above 15%." />
        <StatTile label="Avg duration" value={fmtDur(job.avg_duration_s)} info="Average wall-clock per run: setup + queue + execution + cleanup." />
        <StatTile label="Setup+queue share" value={fmtPct(job.setup_queue_ratio)} tone={job.setup_queue_ratio >= 0.35 ? "warning" : "neutral"} info="Share of wall-clock spent on cluster setup + queue (starting up / waiting for compute) rather than executing. An observed fact from the run timeline — not a migration recommendation." />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Wait vs exec split */}
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <div className="text-sm font-semibold mb-3 flex items-center gap-1.5">
            Where the wall-clock goes
            <InfoTip text="Average run split into setup/warm-up, queue (waiting for compute), execution, and cleanup — measured from the run timeline. A large setup + queue slice is a fact worth investigating, surfaced without any migration or savings claim." />
          </div>
          <WaitSplit job={job} />
        </div>

        {/* Duration + cost trend */}
        <div className="rounded-lg border border-border bg-surface/30 p-4 flex flex-col gap-3">
          <div>
            <div className="text-xs uppercase tracking-wide text-neutral mb-1 flex items-center gap-1.5">
              Median run duration — recent weeks
              <InfoTip text="Per-week median run duration (from job_run_timeline). A rising line means the job is getting slower; a late jump means a recent run-time spike vs its own baseline." />
            </div>
            {durTrend.length >= 2 ? (
              <div className="flex items-center gap-3">
                <Sparkline data={durTrend} width={180} height={34} />
                <span className="text-xs text-neutral">
                  spike {fmtFixed(job.spike_ratio, 1, "×")} · growth {fmtPct(job.growth_pct)}
                </span>
              </div>
            ) : drillLoading ? (
              <span className="text-xs text-neutral">Loading duration trend…</span>
            ) : drillFailed ? (
              <span className="text-xs text-warning">Couldn't load run detail — retry.</span>
            ) : (
              <span className="text-xs text-neutral">Not enough history — this job has runs in fewer than two distinct weeks.</span>
            )}
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-neutral mb-1 flex items-center gap-1.5">
              Cost — last 6 months
              <InfoTip text="Monthly list-price cost for this job over time. Useful for catching jobs growing in cost." />
            </div>
            {costTrend.length >= 2 ? (
              <Sparkline data={costTrend} width={180} height={34} />
            ) : drillLoading ? (
              <span className="text-xs text-neutral">Loading cost trend…</span>
            ) : drillFailed ? (
              <span className="text-xs text-warning">Couldn't load run detail — retry.</span>
            ) : (
              <span className="text-xs text-neutral">Not enough history — under two months of billed cost for this job.</span>
            )}
          </div>
        </div>
      </div>

      {/* Recent runs + termination codes */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
            Recent runs
            <InfoTip text="The most recent terminal runs with per-run duration and warm-up wait ratio. Failed states are flagged." />
          </div>
          {recentRuns.length ? (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-neutral text-[10px] uppercase tracking-wide">
                  <th className="text-left font-semibold py-1">Run</th>
                  <th className="text-left font-semibold py-1">Result</th>
                  <th className="text-right font-semibold py-1">Duration</th>
                  <th className="text-right font-semibold py-1" title="Duration change vs the previous run">Δ vs prev</th>
                  <th className="text-right font-semibold py-1">Wait</th>
                </tr>
              </thead>
              <tbody>
                {recentRuns.map((r) => {
                  const failed = r.result_state !== "SUCCEEDED";
                  const dd = r.duration_delta_pct ?? 0;
                  return (
                    <tr key={r.run_id} className="border-t border-border/50">
                      <td className="py-1 text-neutral">{r.date}</td>
                      <td className="py-1">
                        <span className={`pill ${failed ? "bg-danger/15 text-danger" : "bg-success/15 text-success"}`}>{r.result_state}</span>
                        {r.regressed && <span className="ml-1 pill bg-danger/15 text-danger" title="Regressed from SUCCEEDED on the previous run">▼ regressed</span>}
                      </td>
                      <td className="py-1 text-right tabular-nums">{fmtDur(r.duration_s)}</td>
                      <td className="py-1 text-right tabular-nums">
                        {Math.abs(dd) < 0.02
                          ? <span className="text-neutral">—</span>
                          : <span className={dd > 0 ? "text-danger" : "text-success"}>{dd > 0 ? "↑" : "↓"}{fmtPct(Math.abs(dd))}</span>}
                      </td>
                      <td className="py-1 text-right tabular-nums text-neutral">{fmtPct(r.wait_ratio)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : drillLoading ? (
            <span className="text-xs text-neutral">Loading recent runs…</span>
          ) : drillFailed ? (
            <span className="text-xs text-warning">Couldn't load run detail — retry.</span>
          ) : (
            <span className="text-xs text-neutral">No terminal runs recorded for this job in the retention window.</span>
          )}
        </div>

        <div className="rounded-lg border border-border bg-surface/30 p-4">
          <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
            Failure reasons (termination codes)
            <InfoTip text="Top termination codes across failed runs — the fastest route to a root cause for a failing or rising-failure job." />
          </div>
          {terminationCodes.length ? (
            <div className="flex flex-col gap-1.5">
              {(() => { const max = Math.max(1, ...terminationCodes.map((x) => x.count)); return terminationCodes.map((t) => {
                return (
                  <div key={t.code} className="flex items-center gap-2 text-xs">
                    <div className="flex-1 min-w-0">
                      <div className="truncate font-mono text-brand-dark">{t.code}</div>
                      <div className="mt-1 h-1 w-full rounded-full bg-border/60 overflow-hidden">
                        <div className="h-full rounded-full bg-danger" style={{ width: `${(t.count / max) * 100}%` }} />
                      </div>
                    </div>
                    <span className="w-8 shrink-0 text-right tabular-nums text-neutral">{t.count}</span>
                  </div>
                );
              }); })()}
            </div>
          ) : drillLoading ? (
            <span className="text-xs text-neutral">Loading failure reasons…</span>
          ) : drillFailed ? (
            <span className="text-xs text-warning">Couldn't load run detail — retry.</span>
          ) : (
            <span className="text-xs text-neutral">No material failures for this job.</span>
          )}
        </div>
      </div>
    </div>
  );
}
