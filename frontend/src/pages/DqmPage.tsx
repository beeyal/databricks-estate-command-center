import { useState } from "react";
import { fetchDqm } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { StatusPill, Pill } from "../components/shared/Pill";
import { InfoTip } from "../components/shared/InfoTip";
import { fmtMoney, fmtNum, fmtFixed } from "../components/shared/format";
import { useCurrency } from "../store/appStore";
import type { DqmMonitor } from "../types";

// Data Quality Monitoring tab (SPEC §12). Rendered + routed ONLY when
// features.dqm is on (App.tsx / Nav.tsx gate it; /api/dqm 404s otherwise).
// Shows the Lakehouse Monitoring inventory + the DATA_QUALITY_MONITORING cost
// tie-in + a "prune monitors on low-value tables" hint.

const TYPE_OPTIONS = [
  { value: "Snapshot", label: "Snapshot" },
  { value: "TimeSeries", label: "TimeSeries" },
  { value: "Inference", label: "Inference" },
];

const QUALITY_OPTIONS = [
  { value: "Good", label: "Good" },
  { value: "Warning", label: "Warning" },
  { value: "Critical", label: "Critical" },
];

const FRESHNESS_OPTIONS = [
  { value: "Fresh", label: "Fresh" },
  { value: "OK", label: "OK" },
  { value: "Stale", label: "Stale" },
];

function FreshnessPill({ freshness }: { freshness: DqmMonitor["freshness"] }) {
  const cls =
    freshness === "Fresh" ? "bg-success/15 text-success" :
    freshness === "OK" ? "bg-info/15 text-info" : "bg-warning/15 text-warning";
  return <Pill className={cls}>{freshness}</Pill>;
}

export function DqmPage() {
  const cur = useCurrency();
  const [monitorType, setMonitorType] = useState<string[]>([]);
  const [quality, setQuality] = useState<string[]>([]);
  const [freshness, setFreshness] = useState<string[]>([]);
  const [search, setSearch] = useState("");

  const { data, loading, error } = useApi(
    () => fetchDqm({
      monitor_type: monitorType.length ? monitorType.join(",") : "all",
      quality: quality.length ? quality.join(",") : "all",
      freshness: freshness.length ? freshness.join(",") : "all",
      search,
    }),
    [monitorType.join(","), quality.join(","), freshness.join(","), search],
  );

  const monitors = data?.data ?? [];
  const summary = data?.summary;
  const byWs = data?.by_workspace ?? [];
  const prune = data?.prune_candidates ?? [];

  const columns: Column<DqmMonitor>[] = [
    {
      key: "fqn",
      header: (
        <span className="inline-flex items-center gap-1">
          Monitored table
          <InfoTip text="The fully qualified catalog.schema.table name that has an active Lakehouse Monitoring monitor, shown with the workspace it lives in." />
        </span>
      ),
      sortValue: (m) => m.fqn,
      render: (m) => (
        <div className="flex flex-col gap-0.5 max-w-[280px]">
          <span className="font-mono text-xs text-brand-dark truncate">{m.fqn}</span>
          <span className="text-[11px] text-neutral">{m.workspace}</span>
        </div>
      ),
    },
    {
      key: "monitor_type",
      header: "Type",
      align: "center",
      sortValue: (m) => m.monitor_type,
      render: (m) => <Pill className="bg-border/60 text-neutral">{m.monitor_type}</Pill>,
    },
    {
      key: "quality_status",
      header: (
        <span className="inline-flex items-center gap-1">
          Quality
          <InfoTip text="The monitor's overall quality status. Good, Warning, or Critical is derived from data drift, null rates, and detected anomalies against expected thresholds." />
        </span>
      ),
      align: "center",
      sortValue: (m) => ({ Good: 0, Warning: 1, Critical: 2 }[m.quality_status]),
      render: (m) => <StatusPill status={m.quality_status} />,
    },
    {
      key: "drift",
      header: (
        <span className="inline-flex items-center gap-1">
          Drift (PSI/KS)
          <InfoTip text="How far the data has shifted from its baseline. PSI is Population Stability Index and KS is the Kolmogorov Smirnov statistic. Higher values mean more drift." />
        </span>
      ),
      align: "right",
      sortValue: (m) => m.drift_psi,
      render: (m) => (
        <span className={`tabular-nums text-xs ${m.drift_psi >= 0.25 ? "text-danger" : m.drift_psi >= 0.1 ? "text-warning" : "text-neutral"}`}>
          {fmtFixed(m.drift_psi, 2)} / {fmtFixed(m.drift_ks, 2)}
        </span>
      ),
    },
    {
      key: "null_pct",
      header: (
        <span className="inline-flex items-center gap-1">
          Null %
          <InfoTip text="Share of values in the monitored columns that are null, as a percentage. A rising null rate often signals a broken or incomplete upstream data feed." />
        </span>
      ),
      align: "right",
      sortValue: (m) => m.null_pct,
      render: (m) => <span className="tabular-nums text-xs text-neutral">{fmtFixed(m.null_pct, 1, "%")}</span>,
    },
    {
      key: "anomalies",
      header: (
        <span className="inline-flex items-center gap-1">
          Anomalies
          <InfoTip text="Number of data points the monitor flagged as anomalous in its latest run, meaning they fell outside expected ranges." />
        </span>
      ),
      align: "right",
      sortValue: (m) => m.anomalies,
      render: (m) => <span className={`tabular-nums text-xs ${m.anomalies > 0 ? "text-warning" : "text-neutral"}`}>{m.anomalies}</span>,
    },
    {
      key: "num_metrics",
      header: (
        <span className="inline-flex items-center gap-1">
          #Metrics
          <InfoTip text="Number of quality metrics the monitor computes on each refresh, such as null rate, drift, and count. More metrics means more compute per refresh." />
        </span>
      ),
      align: "right",
      sortValue: (m) => m.num_metrics,
      render: (m) => <span className="tabular-nums text-xs text-neutral">{m.num_metrics}</span>,
    },
    {
      key: "freshness",
      header: (
        <span className="inline-flex items-center gap-1">
          Freshness
          <InfoTip text="Whether the monitor refreshed on schedule. Fresh and OK are within the expected window; Stale means it has not refreshed in time so its metrics may be outdated." />
        </span>
      ),
      align: "center",
      sortValue: (m) => ({ Fresh: 0, OK: 1, Stale: 2 }[m.freshness]),
      render: (m) => (
        <div className="flex flex-col items-center gap-0.5">
          <FreshnessPill freshness={m.freshness} />
          <span className="text-[10px] text-neutral">{m.last_refresh_hours}h ago</span>
        </div>
      ),
    },
    {
      key: "alerts",
      header: (
        <span className="inline-flex items-center gap-1">
          Alerts
          <InfoTip text="Number of alert rules set on this monitor, and whether any are currently firing because a quality or drift threshold was breached." />
        </span>
      ),
      align: "center",
      sortValue: (m) => (m.alert_status === "TRIGGERED" ? 1 : 0),
      render: (m) => (
        <span className={`pill ${m.alert_status === "TRIGGERED" ? "bg-danger/15 text-danger" : "bg-border/60 text-neutral"}`}>
          {m.alerts} {m.alert_status === "TRIGGERED" ? "firing" : "set"}
        </span>
      ),
    },
    {
      key: "cost",
      header: (
        <span className="inline-flex items-center gap-1">
          Cost / mo
          <InfoTip text="Estimated monthly cost in dollars to refresh this monitor, billed to the DATA_QUALITY_MONITORING line. A prune tag marks low-value monitors worth pausing." />
        </span>
      ),
      align: "right",
      sortValue: (m) => m.cost_usd_month,
      render: (m) => (
        <div className="flex flex-col items-end">
          <span className="tabular-nums text-xs">{fmtMoney(m.cost_usd_month, cur)}</span>
          {m.prune_candidate && <span className="pill bg-warning/15 text-warning text-[10px]">prune?</span>}
        </div>
      ),
    },
  ];

  return (
    <PageShell
      title="Data Quality"
      subtitle="Lakehouse Monitoring inventory + the DATA_QUALITY_MONITORING cost tie-in — freshness, drift, and which monitors drive spend"
    >
      {loading && !data && <LoadingCard />}
      {/* A missing DQM-schema grant (or DQM not enabled on the metastore) isn't an
          app error — show a calm, informative state, not a red error card. Other
          errors still surface loudly. */}
      {error && (/data.?quality|data_quality_monitoring|USE SCHEMA|enable it/i.test(error) ? (
        <div className="card border-l-4 border-l-info flex flex-col gap-1.5 text-sm">
          <div className="font-medium text-brand-dark">Data Quality Monitoring isn't available yet</div>
          <p className="text-neutral leading-relaxed">
            This tab reads Lakehouse Monitoring results from <span className="font-mono">system.data_quality_monitoring</span>,
            which either isn't enabled on this metastore or the app hasn't been granted read access to it. Once an admin
            enables it and grants the app <span className="font-mono">SELECT</span> on that schema, monitors, drift and the
            DQM cost tie-in appear here automatically. Nothing else in the app is affected.
          </p>
        </div>
      ) : (
        <ErrorCard message={error} />
      ))}

      {summary && (
        <KpiRow cols={6}>
          <KpiCard label="Monitors" value={String(summary.num_monitors)} tone="accent" hint="Lakehouse Monitoring" info="Count of tables with an active Lakehouse Monitoring monitor across your estate. More monitors means broader data quality coverage and more refresh cost." />
          <KpiCard label="Critical / Warning" value={`${summary.num_critical} / ${summary.num_warning}`} tone="danger" hint="drift or nulls" info="Monitors whose quality check failed. Critical or Warning is driven by high data drift, high null rates, or detected anomalies and flags data you should inspect." />
          <KpiCard label="Stale refreshes" value={String(summary.num_stale)} tone="warning" hint="past cadence" info="Count of monitors that have not refreshed within their expected schedule window, so their quality metrics may be out of date." />
          <KpiCard label="Alerts firing" value={String(summary.num_alerts_triggered)} tone="danger" info="Number of monitors with an alert currently triggered on their quality or drift metrics, meaning a threshold has been breached and needs attention." />
          <KpiCard
            label="DQM cost / mo"
            value={fmtMoney(summary.dqm_cost_usd_month, cur, { compact: true })}
            tone="info"
            hint="DATA_QUALITY_MONITORING"
            info="Estimated monthly spend on the DATA_QUALITY_MONITORING billing line, in dollars. This is what refreshing all your monitors costs each month."
          />
          <KpiCard
            label="Prune savings / mo"
            value={fmtMoney(summary.prune_savings_usd_month, cur, { compact: true })}
            tone="success"
            hint={`${summary.num_prune_candidates} low-value monitors`}
            info="Estimated dollars per month you could reclaim by pausing monitors on low-value tables that are stale or quiet and surface nothing actionable."
          />
        </KpiRow>
      )}

      <FilterBar>
        <MultiDropdown label="Type" value={monitorType} onChange={setMonitorType} options={TYPE_OPTIONS} allLabel="All monitor types" />
        <MultiDropdown label="Quality" value={quality} onChange={setQuality} options={QUALITY_OPTIONS} allLabel="All quality" />
        <MultiDropdown label="Freshness" value={freshness} onChange={setFreshness} options={FRESHNESS_OPTIONS} allLabel="All freshness" />
        <SearchBox value={search} onChange={setSearch} placeholder="Search table or workspace…" />
      </FilterBar>

      {data && (
        <DataTable
          columns={columns}
          rows={monitors}
          rowKey={(m) => `${m.workspace}|${m.fqn}`}
          initialSort={{ key: "cost", dir: "desc" }}
          emptyMessage="No monitors match the current filters."
          renderExpanded={(m) => (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <div className="flex flex-col gap-1.5">
                <div className="text-[11px] uppercase tracking-wide text-neutral">Metric tables (auto-generated)</div>
                <span className="font-mono text-neutral">{m.profile_metrics_table}</span>
                <span className="font-mono text-neutral">{m.drift_metrics_table}</span>
                {m.dashboard_url && (
                  <a href={m.dashboard_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline w-fit">
                    Open monitor dashboard →
                  </a>
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <div className="text-[11px] uppercase tracking-wide text-neutral">Schedule &amp; cost</div>
                <span className="text-neutral">Refresh: <span className="text-brand-dark">{m.schedule_label}</span> <span className="font-mono">({m.schedule})</span></span>
                <span className="text-neutral">Last refresh: <span className="text-brand-dark">{m.last_refresh_hours}h ago</span> · {m.freshness}</span>
                <span className="text-neutral">Refresh cost: <span className="text-brand-dark tabular-nums">{fmtMoney(m.cost_usd_month, cur)}/mo</span> · {fmtNum(m.dbus_month)} DBUs · value: {m.value}</span>
                {m.prune_candidate && (
                  <span className="text-warning">Low-value + {m.freshness === "Stale" ? "stale" : "quiet"} — candidate to prune to save {fmtMoney(m.cost_usd_month, cur)}/mo.</span>
                )}
              </div>
            </div>
          )}
        />
      )}

      {/* FinOps tie-in: which workspaces drive the DATA_QUALITY_MONITORING cost.
          Gated on `data` so the "spend looks well-targeted" prune verdict + the
          cost-by-workspace card don't render a confident (false) all-clear while
          nothing has loaded or the tab is in its unavailable/error state. */}
      {data && (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card flex flex-col gap-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral">
            DATA_QUALITY_MONITORING cost by workspace
          </div>
          <div className="flex flex-col gap-2.5">
            {(() => { const max = Math.max(1, ...byWs.map((x) => x.cost_usd_month)); return byWs.map((w) => {
              return (
                <div key={w.workspace} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono truncate mr-2">{w.workspace}</span>
                    <span className="tabular-nums text-neutral shrink-0">
                      {fmtMoney(w.cost_usd_month, cur, { compact: true })} · {w.num_monitors} monitors
                    </span>
                  </div>
                  <div className="h-1.5 rounded-full bg-border/60 overflow-hidden">
                    <div className="h-full rounded-full bg-info" style={{ width: `${(w.cost_usd_month / max) * 100}%` }} />
                  </div>
                </div>
              );
            }); })()}
          </div>
        </div>

        <div className="card border-l-4 border-l-warning flex flex-col gap-2 py-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-neutral">
            Prune monitors on low-value tables
          </div>
          {prune.length === 0 ? (
            <p className="text-xs text-neutral">No low-value or stale monitors detected — spend looks well-targeted.</p>
          ) : (
            <>
              <p className="text-xs text-neutral">
                These monitors sit on low-value tables and are stale or quiet (no anomalies/alerts) — paying for
                monitoring that surfaces nothing actionable. Pausing them reclaims{" "}
                {fmtMoney(summary?.prune_savings_usd_month ?? 0, cur, { compact: true })}/mo.
              </p>
              <ul className="flex flex-col gap-1 text-xs">
                {prune.map((p) => (
                  <li key={p.fqn} className="flex items-center justify-between gap-2">
                    <span className="font-mono truncate text-brand-dark">{p.fqn}</span>
                    <span className="tabular-nums text-neutral shrink-0">
                      {fmtMoney(p.cost_usd_month, cur)}/mo · {p.freshness}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {data?.caveat && <p className="text-[11px] text-neutral border-t border-border pt-2">{data.caveat}</p>}
        </div>
      </div>
      )}
    </PageShell>
  );
}
