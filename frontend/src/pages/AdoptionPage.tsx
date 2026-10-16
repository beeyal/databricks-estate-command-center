import { useMemo, useState } from "react";
import {
  ResponsiveContainer, ScatterChart, Scatter, XAxis, YAxis, ZAxis,
  CartesianGrid, Tooltip, Cell,
} from "recharts";
import { fetchAdoption } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, Dropdown, MultiDropdown } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { Pill } from "../components/shared/Pill";
import { InfoTip } from "../components/shared/InfoTip";
import { fmtNum, byNamedFirst } from "../components/shared/format";
import { useTeamEnabled } from "../store/appStore";
import type { Adoption, AdoptionUser } from "../types";

// Adoption & Value tab — who uses the platform and how broadly (not what it
// costs). Active users, activity volume, feature/product adoption breadth, and
// the Value Map (usage vs recency, sized by users) separating gold assets from
// archive candidates (inspired by the cost-by-driver value bubble).

const QUADRANT_COLOR: Record<string, string> = {
  "Gold asset": "#F59E0B",
  Standard: "#8B5CF6",
  "Archive candidate": "#C0392B",
};

const tooltipStyle = {
  background: "rgb(var(--color-card))",
  border: "1px solid rgb(var(--color-border))",
  borderRadius: 8,
  fontSize: 12,
} as const;

const TIME_RANGES = [
  { value: "30d", label: "Last 30 days" },
  { value: "7d", label: "Last 7 days" },
  { value: "24h", label: "Last 24 hours" },
];

export function AdoptionPage() {
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState("30d");
  const { data, loading, error } = useApi(
    () => fetchAdoption({ workspace: workspace.length ? workspace.join(",") : "all", time_range: timeRange }),
    [workspace.join(","), timeRange],
  );
  const a = data?.data as Adoption | undefined;
  const teamEnabled = useTeamEnabled();
  const teams = a?.team_rollup ?? [];
  const showTeams = teamEnabled && teams.length > 0;

  // Workspace options from workspaces that actually have adoption activity
  // (an unscoped fetch), so a selection always returns data. Sourcing from the
  // full 4000+ estate meant most picks hit a workspace with no activity.
  const { data: allAdoption } = useApi(() => fetchAdoption({ time_range: timeRange }), [timeRange]);
  const workspaceOptions = useMemo(() => {
    const set = new Set(((allAdoption?.data as Adoption | undefined)?.features ?? []).map((f) => f.workspace).filter(Boolean));
    return Array.from(set)
      .map((w) => ({ value: w, label: w }))
      .sort(byNamedFirst);
  }, [allAdoption]);
  const rangeLabel = timeRange === "7d" ? "7d" : timeRange === "24h" ? "24h" : "30d";

  const userColumns: Column<AdoptionUser>[] = [
    { key: "user", header: "User", sortValue: (u) => u.user, render: (u) => <span className="font-mono text-xs text-brand-dark truncate max-w-[220px] inline-block">{u.user}</span> },
    ...(showTeams ? [{ key: "team", header: "Team", sortValue: (u: AdoptionUser) => u.team ?? "", render: (u: AdoptionUser) => <span className="text-xs text-brand-dark">{u.team || "—"}</span> }] : []),
    { key: "ws", header: "Workspace", sortValue: (u) => u.workspace, render: (u) => <span className="font-mono text-xs text-neutral truncate max-w-[180px] inline-block">{u.workspace}</span> },
    { key: "queries", header: `Queries (${rangeLabel})`, align: "right", sortValue: (u) => u.queries, render: (u) => <span className="tabular-nums">{fmtNum(u.queries)}</span> },
    { key: "qhours", header: `Query-hours (${rangeLabel})`, align: "right", sortValue: (u) => u.query_hours ?? 0, render: (u) => <span className="tabular-nums text-neutral">{fmtNum(u.query_hours ?? 0)}</span> },
    { key: "active", header: (<span className="inline-flex items-center gap-1">Last active<InfoTip text="Days since this user last ran a query. 0 = today." /></span>), align: "right", sortValue: (u) => u.last_active_days, render: (u) => <span className="tabular-nums text-neutral">{u.last_active_days === 0 ? "today" : `${u.last_active_days}d ago`}</span> },
  ];

  return (
    <PageShell
      title="Adoption & Value"
      subtitle="Who actually uses the platform and how broadly — active users, activity, feature adoption, and which data assets earn their keep."
    >
      <FilterBar>
        <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={workspaceOptions} allLabel="All workspaces" />
        <Dropdown label="Time period" value={timeRange} onChange={setTimeRange} options={TIME_RANGES} />
      </FilterBar>

      {loading && !a && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {a && (
        <>
          <KpiRow cols={7}>
            <KpiCard label="Monthly active users" value={fmtNum(a.summary.mau)} tone="accent" info="Distinct users who ran at least one query in the period (from system.query.history in live mode). Scopes to the selected workspace." />
            <KpiCard label="Weekly active" value={fmtNum(a.summary.wau)} tone="info" info="Distinct active users in the last 7 days — the stickiness signal alongside monthly actives." />
            <KpiCard label="Queries" value={fmtNum(a.summary.total_queries_month, { compact: true })} tone="neutral" info="Total queries run in the selected scope + period — the raw activity-volume signal." />
            <KpiCard label="Query-hours" value={fmtNum(a.summary.query_hours_month ?? 0, { compact: true })} tone="info" info="Cumulative query execution time (statement duration summed) in the selected scope + period — the compute-effort measure a platform owner reports to leadership." />
            <KpiCard label="Feature breadth" value={String(a.summary.avg_breadth)} hint={`of ${a.summary.num_products} products`} info="Average number of distinct Databricks products a workspace actively uses. Higher breadth = deeper platform adoption." />
            <KpiCard label="Genie workspaces" value={`${fmtNum(a.summary.genie_adopters)}/${fmtNum(a.summary.num_workspaces)}`} hint="of active workspaces" tone="success" info="Count of WORKSPACES that actively used Genie in the period, out of all workspaces with any billed activity — NOT a share of users. (Monthly active users counts distinct people; this counts workspaces, so the two denominators differ.)" />
            <KpiCard label="AI workspaces" value={`${fmtNum(a.summary.ai_adopters)}/${fmtNum(a.summary.num_workspaces)}`} hint="of active workspaces" tone="success" info="Count of WORKSPACES that used any AI product (Model Serving, Vector Search, AI Functions) in the period, out of all workspaces with any billed activity — NOT a share of users." />
          </KpiRow>

          {/* Value Map — usage vs recency, sized by users */}
          <div className="card flex flex-col gap-3">
            <div className="flex items-baseline justify-between gap-2 flex-wrap">
              <div className="text-sm font-semibold flex items-center gap-1.5">
                Value map — assets by usage & freshness
                <InfoTip text="Each bubble is a data asset. X = days since last update (freshness), Y = queries in the period (usage), size = distinct users. Top-left = gold assets (fresh + heavily used). Bottom-right = archive candidates (stale + unused). Live mode covers UC-governed tables this app can see (from lineage read events + information_schema); it excludes non-UC / federated-only assets and tables outside the app principal's grants." />
              </div>
              <div className="flex items-center gap-3 text-xs text-neutral">
                {Object.entries(QUADRANT_COLOR).map(([q, c]) => (
                  <span key={q} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: c }} />{q}</span>
                ))}
              </div>
            </div>
            <ResponsiveContainer width="100%" height={360}>
              <ScatterChart margin={{ top: 10, right: 24, bottom: 28, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" />
                <XAxis
                  type="number" dataKey="days_since_update" name="Days since update"
                  tick={{ fontSize: 10, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false}
                  label={{ value: "← fresher   ·   days since update   ·   staler →", position: "bottom", offset: 10, fontSize: 11, fill: "rgb(var(--color-neutral))" }}
                />
                <YAxis
                  type="number" dataKey="total_queries" name="Queries"
                  tick={{ fontSize: 10, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false}
                  label={{ value: "queries (usage)", angle: -90, position: "insideLeft", fontSize: 11, fill: "rgb(var(--color-neutral))" }}
                />
                <ZAxis type="number" dataKey="unique_users" range={[60, 600]} name="Users" />
                <Tooltip
                  contentStyle={tooltipStyle}
                  cursor={{ strokeDasharray: "3 3" }}
                  formatter={(v: number, n: string) => [fmtNum(v), n]}
                  labelFormatter={() => ""}
                  content={({ payload }) => {
                    if (!payload || !payload.length) return null;
                    const d = payload[0].payload as Adoption["assets"][number];
                    return (
                      <div style={tooltipStyle as object} className="px-3 py-2">
                        <div className="font-mono text-[11px] text-brand-dark">{d.table}</div>
                        <div className="text-[11px] text-neutral">{d.workspace}</div>
                        <div className="text-xs mt-1" style={{ color: QUADRANT_COLOR[d.quadrant] }}>{d.quadrant}</div>
                        <div className="text-[11px] text-neutral">{fmtNum(d.total_queries)} queries · {d.unique_users} users · updated {d.days_since_update}d ago</div>
                      </div>
                    );
                  }}
                />
                <Scatter data={a.assets} isAnimationActive={false}>
                  {a.assets.map((d, i) => (
                    <Cell key={i} fill={QUADRANT_COLOR[d.quadrant]} fillOpacity={0.8} stroke="white" strokeWidth={1} />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
            <p className="text-[11px] text-neutral">
              <span className="text-warning font-medium">{a.summary.gold_assets} gold assets</span> (fresh + heavily used) ·{" "}
              <span className="text-danger font-medium">{a.summary.archive_candidates} archive candidates</span> (stale + zero queries — reclaim storage by archiving).
            </p>
          </div>

          {/* Feature / product adoption by workspace. Sorted by breadth (most-
              adopted first) and scrollable at a fixed height — the estate can
              have thousands of workspaces, so an unbounded list would run off the
              page. A breadth bar carries the signal even when the per-workspace
              product list isn't available (live mode); Genie/AI badges show the
              two headline surfaces. */}
          {(() => {
            const feats = [...a.features].sort((x, y) => y.breadth - x.breadth);
            return (
              <div className="card flex flex-col gap-3">
                <div className="flex items-baseline justify-between gap-2">
                  <div className="text-sm font-semibold flex items-center gap-1.5">
                    Feature adoption by workspace
                    <InfoTip text="How many distinct Databricks products each workspace actively uses (of the products seen across the estate). Wider bars = broader adoption; short bars are enablement opportunities. Sorted by breadth." />
                  </div>
                  <span className="text-[11px] text-neutral tabular-nums shrink-0">{feats.length} workspaces</span>
                </div>
                <div className="flex flex-col gap-1.5 max-h-80 overflow-y-auto pr-1">
                  {feats.map((f) => (
                    <div key={f.workspace} className="flex items-center gap-3 text-xs">
                      <span className="font-medium text-brand-dark w-52 shrink-0 truncate" title={f.workspace}>{f.workspace}</span>
                      <span className="tabular-nums text-neutral w-10 shrink-0 text-right">{f.breadth}/{a.summary.num_products}</span>
                      <div className="flex-1 h-2 rounded-full bg-border/60 overflow-hidden min-w-[60px]">
                        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, (f.breadth / (a.summary.num_products || 1)) * 100)}%` }} />
                      </div>
                      <div className="flex gap-1 shrink-0 w-24 justify-end">
                        {f.genie && <Pill className="bg-insight-rewrite/15 text-insight-rewrite">Genie</Pill>}
                        {f.ai && <Pill className="bg-info/15 text-info">AI</Pill>}
                      </div>
                    </div>
                  ))}
                  {feats.length === 0 && <span className="text-xs text-neutral">No workspace activity in the selected scope.</span>}
                </div>
              </div>
            );
          })()}

          {/* Activity by team — only when a user->team mapping is configured */}
          {showTeams && (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold flex items-center gap-1.5">
                Activity by team
                <InfoTip text="Active users and queries rolled up by team, from your configured user→team mapping. Users without a mapped team fall under 'Unassigned'." />
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {(() => { const maxQ = Math.max(...teams.map((x) => x.queries), 1); return teams.map((t) => {
                  return (
                    <div key={t.team} className="rounded-lg border border-border bg-surface/40 px-3 py-2">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-sm font-medium text-brand-dark truncate">{t.team}</span>
                        <span className="text-[11px] text-neutral shrink-0">{t.users} {t.users === 1 ? "user" : "users"}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 rounded-full bg-border/60 overflow-hidden">
                        <div className="h-full rounded-full bg-accent" style={{ width: `${(t.queries / maxQ) * 100}%` }} />
                      </div>
                      <div className="mt-1 text-xs tabular-nums text-neutral">{fmtNum(t.queries)} queries · {fmtNum(t.query_hours ?? 0)} query-hrs</div>
                    </div>
                  );
                }); })()}
              </div>
            </div>
          )}

          {/* Top users */}
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold flex items-center gap-1.5">
              Most active users
              <InfoTip text="Users ranked by queries run this period — the platform's power users. Pair with low-adoption workspaces to target enablement." />
            </h3>
            <DataTable columns={userColumns} rows={a.users} rowKey={(u) => u.user} initialSort={{ key: "queries", dir: "desc" }} emptyMessage="No activity in this period." />
          </div>
        </>
      )}
    </PageShell>
  );
}
