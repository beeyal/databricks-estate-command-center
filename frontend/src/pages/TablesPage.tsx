import { useMemo, useState } from "react";
import { fetchTables } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, Dropdown, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { InsightPill, Pill } from "../components/shared/Pill";
import { InfoTip } from "../components/shared/InfoTip";
import { fmtBytes, fmtPct, fmtNum } from "../components/shared/format";
import { ackEmptyMessage } from "../components/shared/AckStatusFilter";
import { TableDetail } from "../components/tables/TableDetail";
import { useFeatures } from "../store/appStore";
import type { TableRow, TablesSummary } from "../types";


// A compact yes/no flag chip used for partitioned / clustered / PO / LC.
function Flag({ on, label, warn = false }: { on: boolean; label: string; warn?: boolean }) {
  if (on) {
    return (
      <span className={`pill ${warn ? "bg-warning/15 text-warning" : "bg-success/15 text-success"}`}>{label}</span>
    );
  }
  return <span className="text-neutral text-xs">—</span>;
}

export function TablesPage() {
  const features = useFeatures();
  const [tableType, setTableType] = useState<string[]>([]);
  const [catalog, setCatalog] = useState<string[]>([]);
  const [needsAction, setNeedsAction] = useState("all");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("open");
  const [ackNonce, setAckNonce] = useState(0);
  const { data, loading, error } = useApi(
    () => fetchTables({
      table_type: tableType.length ? tableType.join(",") : "all",
      catalog: catalog.length ? catalog.join(",") : "all",
      needs_action: needsAction,
      search,
      ...(features.writeback ? { status } : {}),
    }),
    [tableType.join(","), catalog.join(","), needsAction, search, status, ackNonce],
  );

  // Catalog filter options — derived from the tab's OWN rows (unfiltered fetch),
  // not a hardcoded demo catalog list (which never matched a customer's real
  // catalog names in live). Stable across other filter changes.
  const { data: allTables } = useApi(() => fetchTables({}), []);
  const catalogOptions = useMemo(() => {
    const set = new Set((allTables?.data ?? []).map((r) => r.catalog).filter(Boolean));
    return Array.from(set)
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }))
      .map((c) => ({ value: c, label: c }));
  }, [allTables]);

  // Prefer the server-computed summary (reconciles to the filtered rows); fall
  // back to client aggregation if it is ever missing.
  const rows = data?.data ?? [];
  const summary: TablesSummary = data?.summary ?? {
    num_tables: rows.length,
    num_managed: rows.filter((r) => r.table_type === "MANAGED").length,
    num_external: rows.filter((r) => r.table_type === "EXTERNAL").length,
    pct_managed: rows.length ? rows.filter((r) => r.table_type === "MANAGED").length / rows.length : 0,
    num_need_optimize: rows.filter((r) => r.needs_optimize).length,
    num_need_clustering: rows.filter((r) => r.needs_clustering).length,
  };

  const columns: Column<TableRow>[] = [
    {
      key: "fqn",
      priority: "critical",
      header: "catalog.schema.table",
      sortValue: (r) => r.fqn,
      render: (r) => (
        <span className="inline-flex items-center gap-1.5 max-w-[340px]">
          <span className="text-xs font-mono truncate" title={r.fqn}>{r.fqn}</span>
          {r.cold && (
            <Pill className="bg-info/15 text-info" >
              <span title={`Large (${r.size_gb.toLocaleString()} GB) + rarely read (${r.days_since_read ?? 0}d since last query) — VACUUM / retention / archive candidate`}>cold</span>
            </Pill>
          )}
        </span>
      ),
    },
    {
      key: "type",
      priority: "medium",
      header: (
        <span className="inline-flex items-center gap-1">
          Type
          <InfoTip text="Managed tables have their storage lifecycle handled by Unity Catalog. External tables point at a location you own and maintain yourself." />
        </span>
      ),
      align: "center",
      sortValue: (r) => r.table_type,
      render: (r) => (
        <Pill className={r.table_type === "MANAGED" ? "bg-success/15 text-success" : "bg-insight-convert/15 text-insight-convert"}>
          {r.table_type}
        </Pill>
      ),
    },
    {
      key: "health",
      priority: "high",
      header: (
        <span className="inline-flex items-center gap-1">
          Health
          <InfoTip text="Table health from freshness (how recently the table was updated) and usage recency (how recently it was queried): Good / Warning / Critical. Hover a row's badge for the specific reason. File hygiene (small-file ratio, OPTIMIZE/VACUUM) and data-quality monitoring are tracked separately (their own columns / tab), not folded in here." />
        </span>
      ),
      align: "center",
      sortValue: (r) => ({ Critical: 0, Warning: 1, Good: 2 }[r.health ?? "Good"]),
      render: (r) => (
        <span title={r.health_reason ?? ""}>
          <Pill className={r.health === "Critical" ? "bg-danger/15 text-danger" : r.health === "Warning" ? "bg-warning/15 text-warning" : "bg-success/15 text-success"}>
            {r.health ?? "Good"}
          </Pill>
        </span>
      ),
    },
    {
      key: "size",
      priority: "critical",
      header: "Size",
      align: "right",
      sortValue: (r) => r.size_bytes,
      // Size comes from the nightly gold census; render "—" (not "0 B") for
      // tables it hasn't sized (views/non-Delta/new) so a real 0 isn't implied.
      render: (r) => <span className="tabular-nums">{r.size_bytes ? fmtBytes(r.size_bytes) : <span className="text-neutral">—</span>}</span>,
    },
    {
      key: "files",
      priority: "medium",
      header: "Files",
      align: "right",
      sortValue: (r) => r.num_files,
      render: (r) => <span className="tabular-nums">{r.num_files ? r.num_files.toLocaleString() : <span className="text-neutral">—</span>}</span>,
    },
    {
      key: "avg",
      priority: "low",
      header: "Avg file",
      align: "right",
      sortValue: (r) => r.avg_file_mb,
      render: (r) => (
        r.num_files
          ? <span className={`tabular-nums ${r.avg_file_mb < 128 ? "text-warning" : "text-neutral"}`}>{r.avg_file_mb} MB</span>
          : <span className="text-neutral">—</span>
      ),
    },
    {
      key: "small",
      priority: "medium",
      header: (
        <span className="inline-flex items-center gap-1">
          Small-file %
          <InfoTip text="Share of data files smaller than the optimal size. High fragmentation slows scans and wastes compute; OPTIMIZE compacts them." />
        </span>
      ),
      align: "right",
      sortValue: (r) => r.small_file_ratio,
      render: (r) => (
        r.num_files
          ? <span className={`tabular-nums ${r.small_file_ratio > 0.4 ? "text-warning" : "text-neutral"}`}>{fmtPct(r.small_file_ratio)}</span>
          : <span className="text-neutral">—</span>
      ),
    },
    {
      key: "part",
      priority: "low",
      header: (
        <span className="inline-flex items-center gap-1">
          Part?
          <InfoTip text="Whether the table is partitioned on disk. Liquid Clustering is the modern alternative that adapts automatically." />
        </span>
      ),
      align: "center",
      sortValue: (r) => (r.partitioned ? 1 : 0),
      render: (r) => <Flag on={r.partitioned} label="P" />,
    },
    {
      key: "clus",
      priority: "low",
      header: (
        <span className="inline-flex items-center gap-1">
          Clus?
          <InfoTip text="Whether the table uses Liquid Clustering, an adaptive data layout that replaces static partitioning to keep scans fast as data grows." />
        </span>
      ),
      align: "center",
      sortValue: (r) => (r.clustered ? 1 : 0),
      render: (r) => <Flag on={r.clustered} label="LC" />,
    },
    {
      key: "po",
      priority: "low",
      header: (
        <span className="inline-flex items-center gap-1">
          PO?
          <InfoTip text="Predictive Optimization: Databricks automatically runs OPTIMIZE and VACUUM for you, so maintenance and cost stay under control without manual jobs." />
        </span>
      ),
      align: "center",
      sortValue: (r) => (r.po_enabled ? 1 : 0),
      render: (r) => <Flag on={r.po_enabled} label="PO" />,
    },
    {
      key: "rec",
      priority: "critical",
      header: (
        <span className="inline-flex items-center gap-1">
          Recommendation
          <InfoTip text="The physical-maintenance action for this table (optimize / cluster / vacuum / convert-to-managed / monitor), derived from its file layout — small-file ratio, size, partitioning, external-vs-managed. This is separate from Health: Health tracks freshness + usage recency, so a table can be 'Good' (fresh and queried) yet still recommend 'optimize' because its files are fragmented. Hover the pill's row for the rationale." />
        </span>
      ),
      sortValue: (r) => r.recommendation,
      render: (r) => (
        <span className="inline-flex items-center gap-1.5" title={r.rationale ?? ""}>
          <InsightPill type={r.recommendation} />
        </span>
      ),
    },
  ];

  return (
    <PageShell
      title="Storage & Tables"
      subtitle="Managed vs external tables, size and file hygiene — from Delta table metadata (DESCRIBE DETAIL)"
    >
      {/* KPIs are real facts only — managed/external split, total measured size,
          and the count needing OPTIMIZE/clustering (from real small-file ratio +
          layout). Estimated-$ KPIs were removed (no per-table scan cost in system
          tables). Gated on `data` so a fresh load doesn't flash a zero-filled
          summary next to the LoadingCard. */}
      {data && (
      <KpiRow cols={4}>
        <KpiCard
          label="Managed vs External"
          value={`${summary.num_managed} / ${summary.num_external}`}
          tone="success"
          hint={`${fmtPct(summary.pct_managed)} managed`}
          info="Count of managed tables (storage lifecycle handled by Unity Catalog) versus external tables (data in a location you own and maintain)."
        />
        <KpiCard
          label="Total size"
          value={fmtBytes(rows.reduce((s, r) => s + (r.size_bytes || 0), 0))}
          tone="info"
          hint={`${fmtNum(rows.filter((r) => r.size_bytes).length)} of ${fmtNum(summary.num_tables)} sized`}
          info="Total measured storage across the tables sized by the nightly census (DESCRIBE DETAIL). Tables the census hasn't sized (views / non-Delta) are excluded."
        />
        <KpiCard
          label="Small-file tables"
          value={String(rows.filter((r) => r.num_files && r.small_file_ratio >= 0.4).length)}
          tone="warning"
          hint="≥40% small files"
          info="Tables where at least 40% of data files are below the healthy size — OPTIMIZE would compact them. Derived from the real file count/size."
        />
        <KpiCard
          label="Need OPTIMIZE / cluster"
          value={`${summary.num_need_optimize} / ${summary.num_need_clustering}`}
          tone="warning"
          hint={`${fmtNum(summary.num_tables)} tables`}
          info="Tables needing OPTIMIZE (compact small files) or clustering (large + unpartitioned/unclustered), from real file stats. No dollar estimate is shown — per-table scan cost isn't in system tables."
        />
      </KpiRow>
      )}

      <FilterBar>
        <MultiDropdown
          label="Type"
          value={tableType}
          onChange={setTableType}
          options={[
            { value: "MANAGED", label: "Managed" },
            { value: "EXTERNAL", label: "External" },
          ]}
          allLabel="All types"
        />
        <MultiDropdown
          label="Catalog"
          value={catalog}
          onChange={setCatalog}
          options={catalogOptions}
          allLabel="All catalogs"
        />
        <Dropdown
          label="Needs action"
          value={needsAction}
          onChange={setNeedsAction}
          options={[
            { value: "all", label: "All tables" },
            { value: "needs-action", label: "Any action" },
            { value: "optimize", label: "Needs OPTIMIZE" },
            { value: "clustering", label: "Needs clustering" },
            { value: "cold", label: "Cold (large + rarely read)" },
          ]}
        />
        <SearchBox value={search} onChange={setSearch} placeholder="Search catalog.schema.table…" />
      </FilterBar>

      {loading && !data && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {data && (
        <DataTable
          columns={columns}
          updating={loading && !!data}
          rows={data.data}
          rowKey={(r) => r.fqn}
          initialSort={{ key: "size", dir: "desc" }}
          emptyMessage={features.writeback ? ackEmptyMessage(status, "tables") : "No tables match the current filters."}
          renderExpanded={(r) => <TableDetail row={r} />}
          bulkAck={features.writeback ? {
            source: "table",
            metricName: "size_gb",
            itemOf: (r) => ({ entityKey: r.fqn, metricValue: r.size_gb, acked: (r.ack_status ?? "open") !== "open" }),
            statusOf: (r) => ({ status: r.ack_status, reopened: r.reopened }),
            onChanged: () => setAckNonce((n) => n + 1),
            status, onStatusChange: setStatus, statusCounts: data?.ack_counts,
          } : undefined}
        />
      )}
    </PageShell>
  );
}
