import { useEffect, useMemo, useState } from "react";
import { fetchPipelines } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useCurrency, useFeatures, useBuEnabled } from "../store/appStore";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { InfoTip } from "../components/shared/InfoTip";
import { Pill } from "../components/shared/Pill";
import { fmtMoney, fmtPct, fmtNum, byNamedFirst } from "../components/shared/format";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import type { PipelineRow, PipelineReason } from "../types";

const PIPELINE_TYPE_COLOR: Record<string, string> = {
  lakeflow: "bg-accent/15 text-accent",
  dlt: "bg-success/15 text-success",
  mv: "bg-info/15 text-info",
  st: "bg-info/15 text-info",
};

const REASON_STYLE: Record<PipelineReason, string> = {
  failing: "bg-danger/15 text-danger",
  "cost-spike": "bg-warning/15 text-warning",
  "full-refresh-heavy": "bg-warning/15 text-warning",
  continuous: "bg-info/15 text-info",
  "development-mode": "bg-info/15 text-info",
  "deleted-still-billing": "bg-danger/15 text-danger",
};

const REASON_LABEL: Record<PipelineReason, string> = {
  failing: "failing",
  "cost-spike": "cost spike",
  "full-refresh-heavy": "full-refresh heavy",
  continuous: "continuous",
  "development-mode": "development",
  "deleted-still-billing": "deleted, still billing",
};

export function PipelinesPage() {
  const cur = useCurrency();
  const features = useFeatures();
  const buEnabled = useBuEnabled();
  const [pipelineType, setPipelineType] = useState<string[]>([]);
  const [environment, setEnvironment] = useState<string[]>([]);
  const [envTier, setEnvTier] = useState<string[]>([]);
  const [why, setWhy] = useState<string[]>([]);
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [bu, setBu] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("open");
  const [ackNonce, setAckNonce] = useState(0);
  const [showAll, setShowAll] = useState(false);

  const { data, loading, error } = useApi(
    () => fetchPipelines({
      pipeline_type: pipelineType.length ? pipelineType.join(",") : "all",
      environment: environment.length ? environment.join(",") : "all",
      env_tier: envTier.length ? envTier.join(",") : "all",
      why: why.length ? why.join(",") : "all",
      workspace: workspace.length ? workspace.join(",") : "all",
      ...(buEnabled && bu.length ? { bu: bu.join(",") } : {}),
      search,
      limit: showAll ? "0" : "300",
      ...(features.writeback ? { status } : {}),
    }),
    [pipelineType.join(","), environment.join(","), envTier.join(","), why.join(","), workspace.join(","), bu.join(","), search, status, ackNonce, showAll],
  );

  // Fetch distinct values for filter dropdowns
  const { data: allPipelines } = useApi(() => fetchPipelines({}), []);
  const workspaceOptions = useMemo(() => {
    const set = new Set((allPipelines?.data ?? []).map((r) => r.workspace).filter(Boolean));
    return Array.from(set)
      .map((w) => ({ value: w, label: w }))
      .sort(byNamedFirst);
  }, [allPipelines]);
  const typeOptions = useMemo(() => {
    const set = new Set((allPipelines?.data ?? []).map((r) => r.type_label).filter(Boolean));
    return Array.from(set)
      .map((t) => ({ value: t, label: t }))
      .sort();
  }, [allPipelines]);
  const envOptions = useMemo(() => {
    const set = new Set((allPipelines?.data ?? []).map((r) => r.environment).filter(Boolean));
    return Array.from(set)
      .map((e) => ({ value: e, label: e }))
      .sort();
  }, [allPipelines]);
  const envTierOptions = useMemo(() => {
    const set = new Set((allPipelines?.data ?? []).map((r) => r.env_tier).filter(Boolean));
    return Array.from(set)
      .map((t) => ({ value: t, label: t }))
      .sort();
  }, [allPipelines]);
  const buOptions = useMemo(() => {
    const set = new Set((allPipelines?.data ?? []).map((r) => r.bu).filter((b): b is string => !!b && b !== "—"));
    return Array.from(set)
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }))
      .map((b) => ({ value: b, label: b }));
  }, [allPipelines]);

  const rows = data?.data ?? [];
  const summary = data?.summary;
  const message = data?.message;
  const capped = data?.list_capped;
  const caveats = data?.caveats ?? [];
  const note = data?.note;

  useEffect(() => { setShowAll(false); }, [pipelineType.join(","), environment.join(","), envTier.join(","), why.join(","), workspace.join(","), bu.join(","), search, status]);

  const columns: Column<PipelineRow>[] = useMemo(() => [
    { key: "name", priority: "critical", header: "Pipeline", sortValue: (r) => r.name, render: (r) => (
      <div className="flex items-center gap-1.5 max-w-[240px]">
        <div className="min-w-0">
          <div className="font-medium truncate" title={r.name}>{r.name}</div>
          <div className="text-[11px] font-mono text-neutral/70 truncate" title={r.pipeline_id}>{r.pipeline_id}</div>
        </div>
        {r.deleted && <Pill className="bg-danger/15 text-danger">deleted</Pill>}
      </div>
    ) },
    { key: "type", priority: "medium", header: "Type", align: "center", sortValue: (r) => r.type_label, render: (r) => (
      <Pill className={PIPELINE_TYPE_COLOR[r.pipeline_type.toLowerCase()] ?? "bg-border/60 text-neutral"}>{r.type_label}</Pill>
    ) },
    { key: "workspace", priority: "critical", header: "Workspace", sortValue: (r) => r.workspace, render: (r) => (
      <div className="max-w-[200px]">
        <div className="font-medium truncate" title={r.workspace}>{r.workspace}</div>
        {r.env_tier && r.env_tier !== "Unclassified" && <Pill className={`${r.environment === "Non-production" ? "bg-warning/15 text-warning" : "bg-border/60 text-neutral"} text-[10px] mt-1`}>{r.env_tier}</Pill>}
      </div>
    ) },
    { key: "cost", priority: "critical", header: "Cost / mo", align: "right", sortValue: (r) => r.cost_usd_month, render: (r) => <span className="tabular-nums">{fmtMoney(r.cost_usd_month, cur, { compact: true })}</span> },
    { key: "mom", priority: "high", header: "Change", align: "right", sortValue: (r) => r.mom_pct ?? -999, render: (r) => (
      r.mom_pct === null && r.prev_cost_usd_month === 0
        ? <span className="text-xs text-info font-medium">new</span>
        : r.mom_pct === null
        ? <span className="text-xs text-neutral">—</span>
        : <span className={`tabular-nums ${r.mom_pct > 0 ? "text-danger" : "text-success"}`}>{r.mom_pct > 0 ? "↑" : "↓"}{fmtPct(Math.abs(r.mom_pct))}</span>
    ) },
    { key: "updates", priority: "medium", header: "Updates 30d", align: "right", sortValue: (r) => r.updates_30d, render: (r) => <span className="tabular-nums text-neutral">{fmtNum(r.updates_30d)}</span> },
    { key: "fail_rate", priority: "high", header: "Fail %", align: "right", sortValue: (r) => r.fail_rate, render: (r) => (
      <span className={`tabular-nums ${r.fail_rate >= 0.15 ? "text-danger font-medium" : ""}`}>{fmtPct(r.fail_rate)}</span>
    ) },
    { key: "last_update", priority: "high", header: "Last update", sortValue: (r) => r.last_update, render: (r) => (
      <div className="text-xs flex flex-col gap-0.5">
        <span className="text-neutral">{r.last_update || "—"}</span>
        {r.last_result && <Pill className={r.last_result === "COMPLETED" || r.last_result === "SUCCESS" ? "bg-success/15 text-success" : r.last_result === "FAILED" ? "bg-danger/15 text-danger" : "bg-border/60 text-neutral"}>{r.last_result.toLowerCase()}</Pill>}
      </div>
    ) },
    { key: "triggered_by", priority: "medium", header: "Triggered by", sortValue: (r) => r.triggered_by, render: (r) => (
      <span className="text-xs text-neutral">{r.triggered_by || "—"}</span>
    ) },
    { key: "mode", priority: "medium", header: "Mode", align: "center", sortValue: (r) => `${r.continuous}:${r.serverless}`, render: (r) => (
      <div className="flex flex-wrap gap-1">
        {r.continuous && <Pill className="bg-info/15 text-info">Continuous</Pill>}
        {!r.continuous && <Pill className="bg-border/60 text-neutral">Triggered</Pill>}
        {r.serverless && <Pill className="bg-success/15 text-success">Serverless</Pill>}
        {!r.serverless && <Pill className="bg-warning/15 text-warning">Classic</Pill>}
      </div>
    ) },
    { key: "owner", priority: "low", header: "Owner", sortValue: (r) => r.owner, render: (r) => (
      <span className="text-xs text-neutral">{r.owner || "—"}</span>
    ) },
    { key: "why", priority: "critical", sortValue: (r) => r.reasons.length * 1e15 + r.cost_usd_month, header: (
      <span className="inline-flex items-center gap-1">Why
        <InfoTip text="Why this pipeline is worth attention — signals: 'failing' (≥15% of updates failed or rising), 'cost spike' (month ≥2× baseline), 'full-refresh heavy' (frequent full refreshes), 'continuous' (always running, not event-triggered), 'development' (dev/test mode), 'deleted, still billing' (removed but still costing)." />
      </span>
    ), render: (r) => (
      <div className="flex flex-wrap gap-1">
        {r.reasons.map((x) => (
          <Pill key={x} className={REASON_STYLE[x]}>{REASON_LABEL[x]}</Pill>
        ))}
        {r.reasons.length === 0 && <Pill className="bg-success/15 text-success">healthy</Pill>}
      </div>
    ) },
  ], [cur]);

  return (
    <PageShell
      title="Pipelines"
      subtitle="Lakeflow pipelines, DLT, and SQL materialized views / streaming tables. Covers all billed pipeline IDs, including job-triggered pipelines that also appear on the Jobs tab (same dollars — don't double-count)."
    >
      <FilterBar>
        <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={workspaceOptions} allLabel="All workspaces" />
        {buEnabled && buOptions.length > 0 && (
          <MultiDropdown label="Business unit" value={bu} onChange={setBu} options={buOptions} allLabel="All BUs" />
        )}
        <MultiDropdown label="Type" value={pipelineType} onChange={setPipelineType} options={typeOptions} allLabel="All types" />
        <MultiDropdown label="Environment" value={environment} onChange={setEnvironment} options={envOptions} allLabel="All environments" />
        {envTierOptions.length > 0 && (
          <MultiDropdown label="Env tier" value={envTier} onChange={setEnvTier} options={envTierOptions} allLabel="All tiers" />
        )}
        <MultiDropdown label="Why" value={why} onChange={setWhy} options={[
          { value: "failing", label: "Failing" },
          { value: "cost-spike", label: "Cost spike" },
          { value: "full-refresh-heavy", label: "Full-refresh heavy" },
          { value: "continuous", label: "Continuous" },
          { value: "development-mode", label: "Development mode" },
          { value: "deleted-still-billing", label: "Deleted, still billing" },
        ]} allLabel="All pipelines" />
        <SearchBox value={search} onChange={setSearch} placeholder="Search pipeline or workspace…" />
      </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <>
          <KpiRow cols={6}>
            <KpiCard label="Pipelines" value={String(summary?.num_pipelines ?? 0)} tone="neutral" info="Number of pipelines (Lakeflow, DLT, MV/ST with pipeline IDs) matching the current filters." />
            <KpiCard label="Pipeline cost / mo" value={fmtMoney(summary?.total_cost_usd_month ?? 0, cur, { compact: true })} tone="accent" delta={summary?.mom_pct != null && Math.abs(summary.mom_pct) >= 0.005 ? { text: `${summary.mom_pct > 0 ? "+" : "−"}${fmtPct(Math.abs(summary.mom_pct))} MoM`, tone: summary.mom_pct > 0 ? "negative" : "positive" } : undefined} info="Total monthly cost of filtered pipelines (MoM compares this month against last month)." />
            <KpiCard label="MV/ST cost / mo" value={fmtMoney(summary?.mv_st_cost_usd_month ?? 0, cur, { compact: true })} tone="info" hint={`${summary?.num_mv_st ?? 0} tables`} info="Cost attributable to SQL materialized views and streaming tables in the filtered scope." />
            <KpiCard label="Serverless %" value={fmtPct(summary?.serverless_pct ?? 0)} tone="success" info="Share of pipeline compute running on Databricks Serverless (vs Classic/Provisioned)." />
            <KpiCard label="Failing" value={String(summary?.num_failing ?? 0)} tone="danger" hint="≥15% fail rate" info="Pipelines with ≥15% of recent updates failed, or the fail rate is rising." />
            <KpiCard label="Continuous" value={String(summary?.num_continuous ?? 0)} tone="accent" info="Pipelines set to continuous run (not event/trigger-based)." />
          </KpiRow>

          {/* Cost by pipeline type */}
          {summary?.by_type && summary.by_type.length > 0 && (
            <div className="card">
              <h3 className="text-sm font-semibold mb-3">Cost by pipeline type</h3>
              <div className="flex flex-col gap-2">
                {summary.by_type.map((t) => (
                  <div key={t.type} className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-2">
                      <Pill className={PIPELINE_TYPE_COLOR[t.type.toLowerCase()] ?? "bg-border/60 text-neutral"}>{t.type}</Pill>
                      <span className="text-neutral">{t.num_pipelines} pipeline{t.num_pipelines !== 1 ? "s" : ""}</span>
                    </span>
                    <span className="tabular-nums font-medium">{fmtMoney(t.cost_usd_month, cur, { compact: true })}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {message && (
            <div className="card border-l-4 border-l-warning text-sm">
              <span className="text-warning font-medium">Live data unavailable</span>
              <span className="text-neutral ml-2">{message}</span>
            </div>
          )}

          <div className="text-xs text-neutral -mt-1">
            Sort by "Why" to surface cost spikes, failures, and development pipelines still billing.
            {note && <> {note}</>}
          </div>

          {capped && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-neutral">
              <span>
                Showing the top <span className="font-medium text-brand-dark">{capped.shown.toLocaleString("en-AU")}</span> pipelines
                most worth investigating of{" "}
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
              <span className="text-neutral/70">or narrow with the filters.</span>
            </div>
          )}

          <DataTable
            columns={columns}
            updating={loading && !!data}
            rows={rows}
            rowKey={(r) => r.pipeline_id}
            initialSort={{ key: "why", dir: "desc" }}
            emptyMessage={features.writeback ? ackEmptyMessage(status, "pipelines") : "No pipelines match the current filters."}
            bulkAck={features.writeback ? {
              source: "pipeline",
              metricName: "cost_usd_month",
              itemOf: (r) => ({ entityKey: `${r.workspace_id}:${r.pipeline_id}`, metricValue: r.cost_usd_month, acked: (r.ack_status ?? "open") !== "open" }),
              statusOf: (r) => ({ status: r.ack_status, reopened: r.reopened }),
              onChanged: () => setAckNonce((n) => n + 1),
              status, onStatusChange: setStatus, statusCounts: data?.ack_counts,
            } : undefined}
          />

          {/* Data source & method */}
          {caveats.length > 0 && (
            <div className="card flex flex-col gap-2 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-neutral">Data source & method</div>
              <ul className="flex flex-col gap-1 text-[11px] text-neutral">
                {caveats.map((c, i) => (
                  <li key={i} className="flex gap-1.5"><span className="text-warning shrink-0" aria-hidden>·</span><span>{c}</span></li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}
