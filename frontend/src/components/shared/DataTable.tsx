import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { UnifiedAckBar } from "./UnifiedAckBar";
import type { BulkAckItem } from "./BulkAckBar";

export interface Column<T> {
  key: string;
  header: ReactNode;
  // value used for sorting; if omitted the column is not sortable
  sortValue?: (row: T) => number | string;
  // custom cell renderer
  render?: (row: T) => ReactNode;
  // simple accessor when no render is provided
  accessor?: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  className?: string;
  // Responsive visibility. Columns are HIDDEN (not deleted) when the table is
  // narrower than their priority's threshold, to avoid horizontal scroll on a
  // laptop. Hidden columns still appear in the row drill-down (renderExpanded),
  // stay sortable, and their header text is used to label them there. Default
  // "high". "critical" columns (identity + the primary metric + the action
  // column) always show. Thresholds are the table's measured width:
  //   critical → always · high → ≥900px · medium → ≥1100px · low → ≥1320px
  priority?: "critical" | "high" | "medium" | "low";
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  initialSort?: { key: string; dir: "asc" | "desc" };
  // expandable row detail
  renderExpanded?: (row: T) => ReactNode;
  emptyMessage?: string;
  dense?: boolean;
  // Deep-link: when set to a row's key, that row is auto-expanded, scrolled into
  // view, and briefly highlighted (used by the Overview → Recommendations
  // drill-in). onFocusConsumed fires once it's been handled so the caller can
  // clear its one-shot focus state.
  focusKey?: string | number | null;
  onFocusConsumed?: () => void;
  // Bulk acknowledge/ignore. When set, the table renders a NARROW checkbox column
  // (leftmost, ~chevron width — no horizontal-scroll cost) and a single UNIFIED
  // bar above the table (always visible when writeback is on): status filter +
  // selection summary + an always-shown Acknowledge action (disabled until rows
  // are ticked). Tick rows → acknowledge/reopen them in one step. A compact
  // status marker per acked row shows acked/reopened at a glance. The status
  // filter (status/onStatusChange/statusCounts) lives IN this bar, so pages no
  // longer put AckStatusFilter in their FilterBar. Optional — omit for tables
  // with no writeback.
  bulkAck?: {
    source: string;
    metricName: string;
    itemOf: (row: T) => BulkAckItem;          // stable entityKey + current metric (+ acked)
    statusOf?: (row: T) => { status?: string; reopened?: boolean } | undefined;
    scopeNote?: string;
    onChanged: () => void;                     // parent refetches after a bulk write
    status: string;                            // current ack status filter
    onStatusChange: (s: string) => void;
    statusCounts?: Record<string, number>;     // ack_counts from the endpoint
  };
  // When true, the table is showing stale rows while a refetch is in flight
  // (e.g. a filter just changed). Renders a subtle "Updating…" overlay so the
  // user knows something is happening rather than staring at unchanged data.
  updating?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  initialSort,
  renderExpanded,
  emptyMessage = "No rows match the current filters.",
  dense = true,
  focusKey = null,
  onFocusConsumed,
  bulkAck,
  updating = false,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(initialSort ?? null);
  const [expanded, setExpanded] = useState<Set<string | number>>(new Set());
  const [selected, setSelected] = useState<Set<string | number>>(new Set());
  const [highlightKey, setHighlightKey] = useState<string | number | null>(null);
  const rowRefs = useRef<Map<string | number, HTMLTableRowElement>>(new Map());
  const handledFocusRef = useRef<string | number | null>(null);

  // Consume a deep-link focusKey: expand + scroll to + highlight that row once
  // it's present in the current rows (waits for data / filters to include it).
  // A ref guards against re-handling the same key, and calling onFocusConsumed
  // (which flips focusKey→null) here is safe because the fade timer lives in its
  // OWN effect below — so clearing focusKey can't cancel the highlight timer.
  useEffect(() => {
    if (focusKey == null) { handledFocusRef.current = null; return; }
    if (handledFocusRef.current === focusKey) return;      // already handled this key
    if (!rows.some((r) => rowKey(r) === focusKey)) return; // not in the list (yet / filtered out)
    handledFocusRef.current = focusKey;
    setExpanded((prev) => new Set(prev).add(focusKey));
    setHighlightKey(focusKey);
    // Defer the scroll to the next frame so the (possibly just-rendered) row ref
    // exists before we scroll to it — otherwise the scroll silently no-ops.
    const raf = requestAnimationFrame(() => {
      rowRefs.current.get(focusKey)?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    onFocusConsumed?.();  // one-shot: clear the caller's focus state now that we've handled it
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, rows]);

  // Fade the highlight 2.6s after it's set — in its OWN effect keyed on
  // highlightKey so it isn't cancelled by the focusKey→null re-render above.
  useEffect(() => {
    if (highlightKey == null) return;
    const t = setTimeout(() => setHighlightKey(null), 2600);
    return () => clearTimeout(t);
  }, [highlightKey]);
  // Measure the scroll container's visible width so an expanded detail row can be
  // pinned to exactly that (sticky left-0) — a wide table then never forces you to
  // scroll right to read the drill-down.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewW, setViewW] = useState<number>(0);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setViewW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Dev guard: an initialSort pointing at a missing/unsortable column silently
  // renders unsorted (a real bug that shipped once). Warn so it's caught early.
  useEffect(() => {
    if (import.meta.env?.DEV && initialSort) {
      const col = columns.find((c) => c.key === initialSort.key);
      if (!col?.sortValue) {
        // eslint-disable-next-line no-console
        console.warn(`DataTable: initialSort key "${initialSort.key}" has no sortable column — table renders unsorted.`);
      }
    }
    // columns/initialSort are stable per render for a given table.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Responsive column visibility — hide low-priority columns when the table is
  // too narrow to fit them (they remain in the row drill-down). viewW is the
  // measured scroll-container width (ResizeObserver below). Before the first
  // measurement (viewW === 0) show everything, so SSR/first paint isn't degraded.
  const PRIORITY_MIN: Record<string, number> = { critical: 0, high: 900, medium: 1100, low: 1320 };
  const visibleColumns = useMemo(() => {
    if (!viewW) return columns;
    return columns.filter((c) => viewW >= (PRIORITY_MIN[c.priority ?? "high"] ?? 900));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, viewW]);
  // Columns hidden at the current width — surfaced in the drill-down so nothing
  // is lost. Empty when everything fits.
  const hiddenColumns = useMemo(
    () => columns.filter((c) => !visibleColumns.includes(c)),
    [columns, visibleColumns],
  );

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = col.sortValue!(a);
      const vb = col.sortValue!(b);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }, [rows, sort, columns]);

  function toggleSort(key: string) {
    const col = columns.find((c) => c.key === key);
    if (!col?.sortValue) return;
    setSort((prev) => {
      if (prev?.key !== key) return { key, dir: "desc" };
      if (prev.dir === "desc") return { key, dir: "asc" };
      return null;
    });
  }

  function toggleExpand(k: string | number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  function toggleSelect(k: string | number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  // Prune selections that are no longer in the current rows (e.g. after a filter
  // change or a refetch), so the toolbar never acts on stale keys.
  useEffect(() => {
    if (!bulkAck || selected.size === 0) return;
    const present = new Set(sorted.map((r) => rowKey(r)));
    let changed = false;
    const next = new Set<string | number>();
    selected.forEach((k) => { if (present.has(k)) next.add(k); else changed = true; });
    if (changed) setSelected(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const clearSelection = () => setSelected(new Set());

  // Materialise the selected rows' bulk-ack items (stable key + current metric).
  const selectedItems: BulkAckItem[] = bulkAck
    ? sorted.filter((r) => selected.has(rowKey(r))).map((r) => bulkAck.itemOf(r))
    : [];

  const numAckedSel = selectedItems.filter((i) => i.acked).length;
  const numOpenSel = selectedItems.length - numAckedSel;

  const allVisibleSelected = bulkAck && sorted.length > 0 && sorted.every((r) => selected.has(rowKey(r)));
  const someVisibleSelected = bulkAck && sorted.some((r) => selected.has(rowKey(r)));

  function toggleSelectAll() {
    if (allVisibleSelected) clearSelection();
    else setSelected(new Set(sorted.map((r) => rowKey(r))));
  }

  const pad = dense ? "px-3 py-2" : "px-4 py-3";

  return (
    <div className="flex flex-col gap-2">
      {/* Unified writeback bar — status filter + selection + always-visible
          acknowledge action, ABOVE the table so it never scrolls horizontally. */}
      {bulkAck && (
        <UnifiedAckBar
          status={bulkAck.status}
          onStatusChange={bulkAck.onStatusChange}
          statusCounts={bulkAck.statusCounts}
          selectedCount={selectedItems.length}
          numAcked={numAckedSel}
          numOpen={numOpenSel}
          onClearSelection={clearSelection}
          source={bulkAck.source}
          metricName={bulkAck.metricName}
          items={selectedItems}
          scopeNote={bulkAck.scopeNote}
          onDone={() => { clearSelection(); bulkAck.onChanged(); }}
        />
      )}
      <div className="card p-0 overflow-hidden relative">
      {/* Refetch-in-flight overlay: keeps the stale rows visible but dimmed with
          an "Updating…" chip so a filter change reads as "working", not frozen. */}
      {updating && (
        <div className="absolute inset-0 z-20 bg-card/50 backdrop-blur-[1px] flex items-start justify-center pointer-events-none" aria-live="polite">
          <span className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-neutral shadow-sm">
            <span className="live-dot" />Updating…
          </span>
        </div>
      )}
      <div ref={scrollRef} className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-border bg-surface/60">
              {bulkAck && (
                <th className="w-8 pl-3">
                  <input
                    type="checkbox"
                    checked={!!allVisibleSelected}
                    ref={(el) => { if (el) el.indeterminate = !allVisibleSelected && !!someVisibleSelected; }}
                    onChange={toggleSelectAll}
                    aria-label="Select all rows"
                    className="h-3.5 w-3.5 cursor-pointer accent-accent"
                  />
                </th>
              )}
              {renderExpanded && <th className="w-8" />}
              {visibleColumns.map((col) => {
                const active = sort?.key === col.key;
                const alignCls = col.align === "right" ? "text-right" : col.align === "center" ? "text-center" : "text-left";
                return (
                  <th
                    key={col.key}
                    onClick={() => toggleSort(col.key)}
                    // Keyboard affordance for sortable headers (Enter/Space), plus
                    // aria-sort so the sort state is announced. Non-sortable
                    // headers get no tabIndex/role.
                    tabIndex={col.sortValue ? 0 : undefined}
                    role={col.sortValue ? "button" : undefined}
                    aria-sort={active ? (sort!.dir === "desc" ? "descending" : "ascending") : undefined}
                    onKeyDown={
                      col.sortValue
                        ? (e) => {
                            // Only sort when the header CELL itself is focused —
                            // ignore Enter/Space bubbled up from a nested control
                            // (e.g. an InfoTip "?") so it doesn't also toggle sort.
                            if (e.target !== e.currentTarget) return;
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              toggleSort(col.key);
                            }
                          }
                        : undefined
                    }
                    className={`${pad} ${alignCls} text-[11px] font-semibold uppercase tracking-wide text-neutral whitespace-nowrap ${
                      col.sortValue ? "cursor-pointer select-none hover:text-brand-dark focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none" : ""
                    } ${col.className ?? ""}`}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.header}
                      {col.sortValue && (
                        <span className="text-[9px] leading-none text-neutral/70" aria-hidden>
                          {active ? (sort!.dir === "desc" ? "▼" : "▲") : "↕"}
                        </span>
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + (renderExpanded ? 1 : 0) + (bulkAck ? 1 : 0)} className="px-4 py-10 text-center text-neutral">
                  {emptyMessage}
                </td>
              </tr>
            )}
            {sorted.map((row) => {
              const k = rowKey(row);
              const isExpanded = expanded.has(k);
              return (
                <Fragment key={k}>
                  <tr
                    ref={(el) => { if (el) rowRefs.current.set(k, el); else rowRefs.current.delete(k); }}
                    className={`border-b border-border/60 transition ${highlightKey === k ? "bg-accent/10 ring-2 ring-inset ring-accent/50" : "hover:bg-surface/50"} ${renderExpanded ? "cursor-pointer focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60 focus:outline-none" : ""}`}
                    onClick={renderExpanded ? () => toggleExpand(k) : undefined}
                    // Keyboard-operable drill-down: the whole row acts as a
                    // button so Enter/Space toggles the detail (was mouse-only).
                    role={renderExpanded ? "button" : undefined}
                    tabIndex={renderExpanded ? 0 : undefined}
                    aria-expanded={renderExpanded ? isExpanded : undefined}
                    onKeyDown={
                      renderExpanded
                        ? (e) => {
                            // Only toggle when the ROW itself is focused — ignore
                            // Enter/Space bubbled from a nested control (ack button,
                            // InfoTip, CopyButton) so activating those doesn't also
                            // expand/collapse the row.
                            if (e.target !== e.currentTarget) return;
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              toggleExpand(k);
                            }
                          }
                        : undefined
                    }
                  >
                    {bulkAck && (
                      // Narrow selection checkbox (leftmost). Clicking it must NOT
                      // toggle the row's expand, so stop propagation.
                      <td className="w-8 pl-3" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selected.has(k)}
                          onChange={() => toggleSelect(k)}
                          aria-label="Select row"
                          className="h-3.5 w-3.5 cursor-pointer accent-accent"
                        />
                      </td>
                    )}
                    {renderExpanded && (
                      <td className={`${pad} text-neutral text-xs whitespace-nowrap`}>
                        <span aria-hidden>{isExpanded ? "▾" : "▸"}</span>
                        {/* Compact acked/reopened marker so status reads at a glance
                            without a wide column. Amber = re-opened. */}
                        {(() => {
                          const st = bulkAck?.statusOf?.(row);
                          if (!st || (!st.reopened && (!st.status || st.status === "open"))) return null;
                          return (
                            <span
                              className={`ml-1 inline-block h-1.5 w-1.5 rounded-full align-middle ${st.reopened ? "bg-warning" : "bg-neutral/50"}`}
                              title={st.reopened ? "Re-opened — worse since acknowledged" : `Acknowledged (${st.status})`}
                            />
                          );
                        })()}
                      </td>
                    )}
                    {visibleColumns.map((col) => {
                      const alignCls = col.align === "right" ? "text-right" : col.align === "center" ? "text-center" : "text-left";
                      return (
                        <td key={col.key} className={`${pad} ${alignCls} ${col.className ?? ""}`}>
                          {col.render ? col.render(row) : col.accessor ? col.accessor(row) : null}
                        </td>
                      );
                    })}
                  </tr>
                  {renderExpanded && isExpanded && (
                    <tr className="bg-surface/40">
                      <td colSpan={visibleColumns.length + (renderExpanded ? 1 : 0) + (bulkAck ? 1 : 0)} className="p-0">
                        {/* Pin the detail to the scroll container's VISIBLE width
                            (measured), sticky left-0, so a wide table never forces
                            you to scroll right to read the drill-down. */}
                        <div
                          className="sticky left-0 px-4 py-4 flex flex-col gap-4"
                          style={viewW ? { width: viewW } : undefined}
                        >
                          {/* Columns hidden at this width are surfaced here so no
                              data is lost when the table narrows. */}
                          {hiddenColumns.length > 0 && (
                            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1.5 text-xs border-b border-border/60 pb-3">
                              {hiddenColumns.map((col) => (
                                <div key={col.key} className="flex flex-col gap-0.5 min-w-0">
                                  <dt className="text-[10px] uppercase tracking-wide text-neutral/70 truncate">{col.header}</dt>
                                  <dd className="text-brand-dark">{col.render ? col.render(row) : col.accessor ? col.accessor(row) : null}</dd>
                                </div>
                              ))}
                            </dl>
                          )}
                          {renderExpanded(row)}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      </div>
    </div>
  );
}
