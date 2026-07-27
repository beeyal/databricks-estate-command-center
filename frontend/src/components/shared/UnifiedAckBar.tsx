import { useEffect, useRef, useState } from "react";
import { postAck, deleteAck } from "../../api/client";
import type { AckStatus } from "../../types";
import type { BulkAckItem } from "./BulkAckBar";

// Preset reason chips — one tap sets status + reason together.
const PRESETS: { label: string; status: AckStatus; reason: string; icon: string }[] = [
  { label: "Expected — no action", status: "acknowledged", reason: "Expected — no action needed", icon: "✓" },
  { label: "Working on it", status: "in_progress", reason: "Being worked on", icon: "⏳" },
  { label: "Won't fix", status: "wont_fix", reason: "Won't fix — accepted", icon: "⛔" },
];

// Status-filter segments (Open / Acknowledged / In progress / Won't fix / All).
const STATUS_OPTIONS = [
  ["open", "Open"],
  ["acknowledged", "Acknowledged"],
  ["in_progress", "In progress"],
  ["wont_fix", "Won't fix"],
  ["all", "All"],
] as const;

/**
 * Unified writeback bar — ONE cohesive, ALWAYS-VISIBLE surface above an alert
 * table (rendered by DataTable when its `bulkAck` config is set + writeback is
 * on). Combines, left→right:
 *   1. the status filter (segmented control with counts),
 *   2. a selection summary ("N selected · Clear") that appears once rows are
 *      ticked, and
 *   3. the acknowledge ACTION — a primary button that is ALWAYS visible but
 *      disabled (with a hint) until rows are selected, so the capability is
 *      discoverable before selecting (fixes the old "action hidden until you
 *      select" confusion). A Reopen button appears when acked rows are selected.
 *
 * Design follows the researched Datadog-style unified action+filter bar.
 */
export function UnifiedAckBar({
  status,
  onStatusChange,
  statusCounts,
  selectedCount,
  numAcked,
  numOpen,
  onClearSelection,
  source,
  metricName,
  items,
  scopeNote,
  onDone,
}: {
  status: string;
  onStatusChange: (s: string) => void;
  statusCounts?: Record<string, number>;
  selectedCount: number;
  numAcked: number;
  numOpen: number;
  onClearSelection: () => void;
  source: string;
  metricName: string;
  items: BulkAckItem[];
  scopeNote?: string;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [snooze, setSnooze] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstPresetRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); triggerRef.current?.focus(); }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    firstPresetRef.current?.focus();
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  // Close the popover if the selection is cleared out from under it.
  useEffect(() => { if (numOpen === 0) setOpen(false); }, [numOpen]);

  async function apply(ackStatus: AckStatus, reason: string) {
    setBusy(true);
    try {
      await Promise.all(
        items.filter((i) => !i.acked).map((it) =>
          postAck({
            source, entity_key: it.entityKey, status: ackStatus,
            reason: note.trim() || reason,
            metric_name: metricName, metric_at_ack: it.metricValue,
            snooze_days: snooze ?? undefined,
          }),
        ),
      );
      setOpen(false); setNote(""); setSnooze(null);
      onDone();
    } finally {
      setBusy(false);
    }
  }

  async function reopen() {
    setBusy(true);
    try {
      await Promise.all(items.filter((i) => i.acked).map((it) => deleteAck(source, it.entityKey)));
      onDone();
    } finally {
      setBusy(false);
    }
  }

  const hasSelection = selectedCount > 0;

  return (
    <div ref={ref} className="relative flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border bg-surface/40 px-3 py-2">
      {/* Status filter (always visible) */}
      <div className="inline-flex items-center gap-1.5">
        <span className="text-xs text-neutral">Status</span>
        <div className="inline-flex rounded-lg border border-border overflow-hidden" role="group" aria-label="Acknowledge status filter">
          {STATUS_OPTIONS.map(([val, label]) => {
            const n = statusCounts?.[val];
            return (
              <button
                key={val}
                type="button"
                onClick={() => onStatusChange(val)}
                aria-pressed={status === val}
                className={`px-2.5 py-1 text-xs font-medium transition focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60 focus:outline-none ${
                  status === val ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
                }`}
              >
                {label}
                {val !== "open" && val !== "all" && n != null ? <span className="opacity-70"> · {n}</span> : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* Divider before the action group (hidden when it would wrap alone) */}
      <span className="h-5 border-l border-border/50 hidden sm:inline-block" aria-hidden />

      {/* Selection summary — appears once rows are ticked */}
      <span aria-live="polite" className="text-xs text-neutral inline-flex items-center gap-2">
        {hasSelection ? (
          <>
            <span className="font-medium text-brand-dark">{selectedCount} selected</span>
            <button
              type="button"
              onClick={onClearSelection}
              className="text-[11px] text-neutral hover:text-brand-dark underline decoration-dotted"
            >
              Clear
            </button>
          </>
        ) : (
          <span className="text-neutral/70">Select rows to acknowledge →</span>
        )}
      </span>

      {/* Action group pushed right on wide screens */}
      <div className="flex items-center gap-1.5 sm:ml-auto">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => numOpen > 0 && setOpen((o) => !o)}
          disabled={numOpen === 0 || busy}
          title={numOpen === 0 ? "Select one or more open rows to acknowledge" : undefined}
          aria-describedby={numOpen === 0 ? "ack-hint" : undefined}
          className="pill text-xs px-3 py-1 bg-accent text-white hover:bg-accent/90 inline-flex items-center gap-1 focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-accent"
          aria-haspopup="dialog"
          aria-expanded={open}
        >
          {numOpen > 0 ? `Acknowledge ${numOpen} ▾` : "Acknowledge"}
        </button>
        <span id="ack-hint" className="sr-only">Select one or more open rows to enable acknowledge.</span>
        {numAcked > 0 && (
          <button
            type="button"
            onClick={reopen}
            disabled={busy}
            className="pill text-xs px-3 py-1 border border-border text-neutral hover:text-brand-dark hover:bg-surface focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none disabled:opacity-50"
          >
            Reopen {numAcked}
          </button>
        )}
      </div>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Acknowledge or ignore ${numOpen} selected`}
          className="absolute right-3 top-full z-40 mt-1 w-64 rounded-lg border border-border bg-card p-3 shadow-lg flex flex-col gap-2"
        >
          <p className="text-[11px] text-neutral">Mark these so they stop surfacing — each re-opens automatically if it gets materially worse.</p>
          {scopeNote && <p className="text-[11px] text-warning">{scopeNote}</p>}
          <div className="flex flex-col gap-1">
            {PRESETS.map((p, i) => (
              <button
                key={p.status}
                ref={i === 0 ? firstPresetRef : undefined}
                type="button"
                onClick={() => apply(p.status, p.reason)}
                disabled={busy}
                className="flex items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-brand-dark hover:bg-surface disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none"
              >
                <span className="w-4 text-center">{p.icon}</span>
                {p.label}
              </button>
            ))}
          </div>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note (applies to all selected)"
            className="w-full rounded border border-border bg-surface px-2 py-1 text-xs text-brand-dark placeholder:text-neutral/60 focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <div className="flex items-center gap-1.5 text-[11px] text-neutral">
            <span>Snooze:</span>
            {[30, 90].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setSnooze(snooze === d ? null : d)}
                className={`pill px-2 py-0.5 border ${snooze === d ? "border-accent text-accent" : "border-border text-neutral hover:text-brand-dark"}`}
              >
                {d}d
              </button>
            ))}
            {snooze && <span className="text-neutral">· reopens after {snooze}d</span>}
          </div>
        </div>
      )}
    </div>
  );
}
