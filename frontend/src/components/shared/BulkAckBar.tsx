import { useEffect, useRef, useState } from "react";
import { postAck, deleteAck } from "../../api/client";
import type { AckStatus } from "../../types";

// Preset reason chips — one tap sets status + reason together (mirrors the old
// per-row AcknowledgeControl so the interaction language is unchanged).
const PRESETS: { label: string; status: AckStatus; reason: string; icon: string }[] = [
  { label: "Expected — no action", status: "acknowledged", reason: "Expected — no action needed", icon: "✓" },
  { label: "Working on it", status: "in_progress", reason: "Being worked on", icon: "⏳" },
  { label: "Won't fix", status: "wont_fix", reason: "Won't fix — accepted", icon: "⛔" },
];

export interface BulkAckItem {
  entityKey: string;
  metricValue: number;
  /** true when this row is already acknowledged/ignored (drives the Reopen action). */
  acked?: boolean;
}

/**
 * Bulk acknowledge/ignore TOOLBAR — the action lives ABOVE the table (always
 * visible, never scrolls horizontally), so a wide table never hides it. Tick one
 * or many rows in the table's checkbox column, then acknowledge/reopen them all
 * in one step. Source-agnostic: the parent supplies the selected items' stable
 * (entityKey, metricValue) + the shared source/metricName.
 *
 * Renders nothing when nothing is selected (the parent gates on writeback).
 */
export function BulkAckBar({
  source,
  metricName,
  items,
  scopeNote,
  onDone,
  onClear,
}: {
  source: string;
  metricName: string;
  items: BulkAckItem[];
  scopeNote?: string;
  /** Called after a successful bulk write so the parent can refetch + clear selection. */
  onDone: () => void;
  /** Called when the user clears the selection without acting. */
  onClear: () => void;
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

  if (items.length === 0) return null;

  const n = items.length;
  const numAcked = items.filter((i) => i.acked).length;
  const numOpen = n - numAcked;

  async function apply(status: AckStatus, reason: string) {
    setBusy(true);
    try {
      await Promise.all(
        items.map((it) =>
          postAck({
            source, entity_key: it.entityKey, status,
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

  return (
    <div ref={ref} className="relative flex flex-wrap items-center gap-2 rounded-lg border border-accent/40 bg-accent/5 px-3 py-2">
      <span className="text-xs font-medium text-brand-dark">{n} selected</span>
      <div className="flex items-center gap-1.5">
        {numOpen > 0 && (
          <button
            ref={triggerRef}
            type="button"
            onClick={() => setOpen((o) => !o)}
            disabled={busy}
            className="pill text-xs px-3 py-1 bg-accent text-white hover:bg-accent/90 inline-flex items-center gap-1 focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none disabled:opacity-50"
            aria-haspopup="dialog"
            aria-expanded={open}
          >
            Acknowledge {numOpen} ▾
          </button>
        )}
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
        <button
          type="button"
          onClick={onClear}
          disabled={busy}
          className="text-[11px] text-neutral hover:text-brand-dark underline decoration-dotted"
        >
          Clear
        </button>
      </div>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Acknowledge or ignore ${numOpen} selected`}
          className="absolute left-0 top-full z-40 mt-1 w-64 rounded-lg border border-border bg-card p-3 shadow-lg flex flex-col gap-2"
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
