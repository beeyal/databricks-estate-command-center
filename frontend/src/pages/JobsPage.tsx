import { useEffect, useMemo, useState } from "react";
import { fetchJobs } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useCurrency, useFeatures, useBuEnabled } from "../store/appStore";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, MultiDropdown, Dropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { InfoTip } from "../components/shared/InfoTip";
import { Pill } from "../components/shared/Pill";
import { Sparkline } from "../components/shared/Sparkline";
import { fmtMoney, fmtPct, fmtNum, byNamedFirst } from "../components/shared/format";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import { JobDetail } from "../components/jobs/JobDetail";
import type { JobRow, JobReason } from "../types";

const COMPUTE_STYLE: Record<string, string> = {
  "All-Purpose": "bg-warning/15 text-warning",
  "Jobs (classic)": "bg-info/15 text-info",
  Serverless: "bg-success/15 text-success",
  DLT: "bg-accent/15 text-accent",
};

const REASON_STYLE: Record<JobReason, string> = {
  "high-setup-queue": "bg-accent/15 text-accent",
  failing: "bg-danger/15 text-danger",
  spiking: "bg-warning/15 text-warning",
  growing: "bg-info/15 text-info",
  "cost-anomaly": "bg-danger/15 text-danger",
};
const REASON_LABEL: Record<JobReason, string> = {
  "high-setup-queue": "high setup+queue",
  failing: "failing",
  spiking: "spiking",
  growing: "growing",
  "cost-anomaly": "cost anomaly",
};

const JOB_TIME_RANGES = [
  { value: "30d", label: "Last 30 days" },
  { value: "7d", label: "Last 7 days" },
  { value: "24h", label: "Last 24 hours" },
];

export function JobsPage() {
  const cur = useCurrency();
  const features = useFeatures();
  const buEnabled = useBuEnabled();
  const [computeType, setComputeType] = useState<string[]>([]);
  const [workload, setWorkload] = useState<string[]>([]);
  const [why, setWhy] = useState<string[]>([]);
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [bu, setBu] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState("30d");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("open");
  const [ackNonce, setAckNonce] = useState(0);
  // The list is capped to the most-actionable jobs by default (payload size);
  // "Load all" flips this so the user can pull the full set on demand. Any
  // filter change resets back to the capped view.
  const [showAll, setShowAll] = useState(false);

  const { data, loading, error } = useApi(
    () => fetchJobs({
      compute_type: computeType.length ? computeType.join(",") : "all",
      workload: workload.length ? workload.join(",") : "all",
      why: why.length ? why.join(",") : "all",
      workspace: workspace.length ? workspace.join(",") : "all",
      ...(buEnabled && bu.length ? { bu: bu.join(",") } : {}),
      time_range: timeRange,
      search,
      limit: showAll ? "0" : "300",
      ...(features.writeback ? { status } : {}),
    }),
    [computeType.join(","), workload.join(","), why.join(","), workspace.join(","), bu.join(","), timeRange, search, status, ackNonce, showAll],
  );

  // Workspace filter options — ONLY workspaces that actually have jobs (derived
  // from a time-range-scoped unfiltered fetch), so picking one always returns
  // rows. Sourcing from the full 4000+ workspace estate meant most selections
  // hit a workspace with no jobs and looked like the filter was broken.
  const { data: allJobs } = useApi(() => fetchJobs({ time_range: timeRange }), [timeRange]);
  const workspaceOptions = useMemo(() => {
    const set = new Set((allJobs?.data ?? []).map((r) => r.workspace).filter(Boolean));
    return Array.from(set)
      .map((w) => ({ value: w, label: w }))
      .sort(byNamedFirst);
  }, [allJobs]);
  const buOptions = useMemo(() => {
    const set = new Set((allJobs?.data ?? []).map((r) => r.bu).filter((b): b is string => !!b && b !== "—"));
    return Array.from(set)
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }))
      .map((b) => ({ value: b, label: b }));
  }, [allJobs]);

  const rows = data?.data ?? [];
  const summary = data?.summary;
  const message = data?.message;
  const capped = data?.list_capped;

  // Reset "show all" whenever the filters change — a fresh query starts capped.
  useEffect(() => { setShowAll(false); }, [computeType.join(","), why.join(","), workspace.join(","), bu.join(","), timeRange, search, status]);

  const columns: Column<JobRow>[] = useMemo(() => [
    { key: "workspace", priority: "critical", header: "Workspace", sortValue: (r) => r.workspace, render: (r) => (
      <div className="max-w-[200px]">
        <div className="font-medium truncate" title={r.workspace}>{r.workspace}</div>
        {r.workspace_id && r.workspace_id !== r.workspace && (
          <div className="text-[11px] font-mono text-neutral/70 truncate">{r.workspace_id}</div>
        )}
      </div>
    ) },
    { key: "job_name", priority: "critical", header: "Job", sortValue: (r) => r.job_name, render: (r) => (
      <div className="flex items-center gap-1.5 max-w-[220px]">
        <span className="font-medium truncate" title={r.job_name}>{r.job_name}</span>
        {r.dlt && <Pill className="bg-accent/15 text-accent">DLT</Pill>}
      </div>
    ) },
    { key: "compute", priority: "medium", header: "Compute", align: "center", sortValue: (r) => r.compute_type, render: (r) => (
      <Pill className={COMPUTE_STYLE[r.compute_type] ?? "bg-border/60 text-neutral"}>{r.compute_type}</Pill>
    ) },
    { key: "workload", priority: "high", header: "Workload", align: "center", sortValue: (r) => r.workload ?? "", render: (r) => (
      r.workload ? <Pill className="bg-accent/10 text-accent">{r.workload}</Pill> : <span className="text-xs text-neutral">—</span>
    ) },
    { key: "runs", priority: "medium", header: `Runs (${timeRange})`, align: "right", sortValue: (r) => r.runs, render: (r) => <span className="tabular-nums">{fmtNum(r.runs, { compact: true })}</span> },
    { key: "fail_rate", priority: "high", header: "Fail rate (30d)", align: "right", sortValue: (r) => r.fail_rate, render: (r) => (
      <span className={`tabular-nums ${r.fail_rate >= 0.15 ? "text-danger font-medium" : ""}`}>
        {fmtPct(r.fail_rate)}{r.fail_rate_rising ? " ↑" : ""}
      </span>
    ) },
    // Failed-run $ (an estimate) is intentionally NOT a table column — it lives in
    // the per-job drill-down (JobDetail "Failed-run $ / mo" StatTile, with tooltip).
    { key: "cost_per_run", priority: "low", header: "Cost/run", align: "right", sortValue: (r) => r.cost_per_run, render: (r) => <span className="tabular-nums text-neutral">{fmtMoney(r.cost_per_run, cur)}</span> },
    { key: "mom", priority: "low", header: "MoM Δ", align: "right", sortValue: (r) => r.mom_cost_pct, render: (r) => (
      Math.abs(r.mom_cost_pct) < 0.005
        ? <span className="text-xs text-neutral">flat</span>
        : <span className={`tabular-nums ${r.mom_cost_pct > 0 ? "text-danger" : "text-success"}`}>{r.mom_cost_pct > 0 ? "↑" : "↓"}{fmtPct(Math.abs(r.mom_cost_pct))}</span>
    ) },
    { key: "trend", priority: "low", header: (<span className="inline-flex items-center gap-1">Dur trend<InfoTip text="Per-week median run-duration sparkline. Colour shows direction: red = trending up (slower/worse), green = trending down (faster/better), grey = flat." /></span>), align: "center", sortValue: (r) => (r.trend?.length ? r.trend[r.trend.length - 1] : 0), render: (r) => <Sparkline data={r.trend} /> },
    { key: "cost", priority: "critical", header: `Cost (${timeRange})`, align: "right", sortValue: (r) => r.cost_usd_month, render: (r) => <span className="tabular-nums">{fmtMoney(r.cost_usd_month, cur, { compact: true })}</span> },
    { key: "why", priority: "critical", sortValue: (r) => r.reasons.length * 1e15 + r.cost_usd_month, header: (
      <span className="inline-flex items-center gap-1">Why
        <InfoTip text="Why this job is worth attention — the signals it tripped: 'failing' (≥15% of runs failed in 30d, or the fail rate is rising), 'cost anomaly' (this month ≥2× the job's own recent baseline), 'growing' (run duration trending up over 30 days), 'spiking' (recent runs ≥2× the job's median duration), 'high setup+queue' (a large share of runtime spent warming up / queued rather than executing). 'growing' and 'spiking' only fire on jobs with enough runs (≥10 in 30d) to be a real trend, not noise — the Dur-trend sparkline plots that same 30-day daily duration so the badge and the chart agree. A job can trip several. Blank means no signal — it's running normally." />
      </span>
    ), render: (r) => (
      <div className="flex flex-wrap gap-1">
        {r.reasons.map((x) => (
          <Pill key={x} className={REASON_STYLE[x]}>{REASON_LABEL[x]}</Pill>
        ))}
        {r.reasons.length === 0 && <Pill className="bg-success/15 text-success">healthy</Pill>}
      </div>
    ) },
  ], [cur, timeRange]);

  return (
    <PageShell
      title="Jobs"
      subtitle="Biggest-cost jobs, failing jobs, run-time spikes, and where each run's time goes"
    >
      {/* FilterBar is always rendered (options come from an independent
          unfiltered fetch) so the user can change filters even during a load or
          after an error — hiding it inside {data && …} trapped them. */}
      <FilterBar>
        <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={workspaceOptions} allLabel="All workspaces" />
        {buEnabled && buOptions.length > 0 && (
          <MultiDropdown label="Business unit" value={bu} onChange={setBu} options={buOptions} allLabel="All BUs" />
        )}
        <MultiDropdown label="Compute" value={computeType} onChange={setComputeType} options={[
          { value: "All-Purpose", label: "All-Purpose (on-demand)" },
          { value: "Jobs (classic)", label: "Jobs (classic)" },
          { value: "Serverless", label: "Serverless" },
          { value: "DLT", label: "DLT" },
        ]} allLabel="All compute" />
        <MultiDropdown label="Workload" value={workload} onChange={setWorkload} options={[
          { value: "Batch", label: "Batch" },
          { value: "Streaming", label: "Streaming" },
          { value: "Interactive / Ad-hoc", label: "Interactive / Ad-hoc" },
          { value: "ML / AI", label: "ML / AI" },
          { value: "Other", label: "Other" },
        ]} allLabel="All workloads" />
        <MultiDropdown label="Why" value={why} onChange={setWhy} options={[
          { value: "high-setup-queue", label: "High setup + queue" },
          { value: "failing", label: "Failing / rising" },
          { value: "spiking", label: "Run-time spike" },
          { value: "growing", label: "Growing over time" },
          { value: "cost-anomaly", label: "Cost anomaly" },
        ]} allLabel="All jobs" />
        <Dropdown label="Time period" value={timeRange} onChange={setTimeRange} options={JOB_TIME_RANGES} />
        <SearchBox value={search} onChange={setSearch} placeholder="Search job or workspace…" />
      </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={5}>
            <KpiCard label="Jobs" value={String(summary?.num_jobs ?? 0)} tone="neutral" info="Number of jobs matching the current filters." />
            <KpiCard label="Failing (30d)" value={String(summary?.num_failing ?? 0)} tone="danger" hint="≥15% or rising" info="Jobs where at least 15% of runs failed (FAILED/TIMED_OUT/CANCELED) in the last 30 days, or the 7-day fail rate is rising above the 30-day rate. This is a fixed 30-day reliability signal — it does NOT change with the time-period selector (which scales the cost/run figures)." />
            <KpiCard label="Cost anomalies (30d)" value={String(summary?.num_cost_anomalies ?? 0)} tone="danger" hint="≥2× baseline" info="Jobs costing ≥2× their own recent monthly baseline this month (and a material absolute jump). A fixed baseline signal — does NOT change with the time-period selector." />
            {/* Failed-run $ moved off the top KPIs (it's an estimate) — it lives as
                the per-job "Failed $" column + drill-down instead. */}
            <KpiCard label="Fastest-growing (MoM)" value={String(summary?.num_growing ?? 0)} tone="warning" hint="cost rising MoM" info="Jobs whose duration/cost is growing month-over-month (≥15% over the trend window). A fixed month-over-month signal — does NOT change with the time-period selector." />
            <KpiCard label={`Total job cost (${timeRange})`} value={fmtMoney(summary?.total_cost_usd_month ?? 0, cur, { compact: true })} tone="info" info="Total list-price cost of the filtered jobs over the selected window (scales with the time-period selector). Live mode attributes cost only to jobs-compute + serverless jobs (all-purpose jobs carry no job-level billing)." />
          </KpiRow>

          {message && (
            <div className="card border-l-4 border-l-warning text-sm">
              <span className="text-warning font-medium">Live data unavailable</span>
              <span className="text-neutral ml-2">{message}</span>
            </div>
          )}

          <div className="text-xs text-neutral -mt-1 flex items-center gap-1.5">
            Fail rate and MoM Δ are the fastest routes to waste; click a row to drill into where each run's time goes (setup + queue vs execution), the cost trend, failure reasons, and the estimated failed-run $.
            <InfoTip text="MoM Δ is the exact month-over-month change in this job's cost. Failed-run $ (an estimate: failed runs × avg cost/run) is in each job's drill-down, not the table. The tab deliberately does not label 'serverless candidates' or estimate serverless savings — serverless jobs DBUs list ~2× classic and net cost frequently rises, so a saving can only be established by an A/B run." />
          </div>

          {/* Capped-list note — calm, informational, with an explicit "Load all"
              opt-in. Distinct from the degradation `message` warning above. */}
          {capped && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-neutral">
              <span>
                Showing the top <span className="font-medium text-brand-dark">{capped.shown.toLocaleString("en-AU")}</span> jobs
                most worth investigating (by watch signals, then cost) of{" "}
                <span className="font-medium text-brand-dark">{capped.total.toLocaleString("en-AU")}</span> matching.
              </span>
              <button
                type="button"
                onClick={() => setShowAll(true)}
                disabled={loading}
                className="pill px-2.5 py-1 border border-border text-neutral hover:text-brand-dark hover:bg-surface focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none disabled:opacity-50"
              >
                {loading ? "Loading…" : `Load all ${capped.total.toLocaleString("en-AU")}`}
              </button>
              <span className="text-neutral/70">or narrow with the filters / search.</span>
            </div>
          )}
          {showAll && !capped && rows.length > 300 && (
            <div className="text-xs text-neutral">
              Showing all <span className="font-medium text-brand-dark">{rows.length.toLocaleString("en-AU")}</span> matching jobs.
            </div>
          )}

          <DataTable
            columns={columns}
            updating={loading && !!data}
            rows={rows}
            rowKey={(r) => r.job_id}
            initialSort={{ key: "why", dir: "desc" }}
            renderExpanded={(r) => <JobDetail job={r} />}
            emptyMessage={features.writeback ? ackEmptyMessage(status, "jobs") : "No jobs match the current filters."}
            bulkAck={features.writeback ? {
              source: "job",
              metricName: "cost_usd_month",
              itemOf: (r) => ({ entityKey: `${r.workspace_id}:${r.job_id}`, metricValue: r.cost_usd_month, acked: (r.ack_status ?? "open") !== "open" }),
              statusOf: (r) => ({ status: r.ack_status, reopened: r.reopened }),
              onChanged: () => setAckNonce((n) => n + 1),
              status, onStatusChange: setStatus, statusCounts: data?.ack_counts,
            } : undefined}
          />
        </>
      )}
    </PageShell>
  );
}
