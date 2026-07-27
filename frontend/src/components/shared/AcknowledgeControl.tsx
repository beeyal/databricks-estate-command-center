import { useEffect, useRef, useState } from "react";
import { postAck, deleteAck } from "../../api/client";
import { useFeatures } from "../../store/appStore";
import type { AckStatus } from "../../types";

// Preset reason chips — one tap sets status + reason together (the common paths).
// Keeps the interaction to a single click for most users.
const PRESETS: { label: string; status: AckStatus; reason: string; icon: string }[] = [
  { label: "Expected — no action", status: "acknowledged", reason: "Expected — no action needed", icon: "✓" },
  { label: "Working on it", status: "in_progress", reason: "Being worked on", icon: "⏳" },
  { label: "Won't fix", status: "wont_fix", reason: "Won't fix — accepted", icon: "⛔" },
];

const STATUS_PILL: Record<string, { label: string; cls: string }> = {
  acknowledged: { label: "Acknowledged", cls: "bg-neutral/15 text-neutral" },
  in_progress: { label: "In progress", cls: "bg-warning/15 text-warning" },
  wont_fix: { label: "Won't fix", cls: "bg-neutral/15 text-neutral" },
};

/**
 * Inline acknowledge/ignore control for ANY alert/rec row. One click opens a
 * small popover (preset reason chips + optional note + optional snooze); one
 * click acts. Source-agnostic — pass the stable (source, entityKey) + the
 * current magnitude metric so the backend can snapshot it for smart re-alert.
 *
 * Renders nothing when the writeback feature is off (so a no-writeback
 * deployment shows no dead control). `onChanged` lets the parent refetch.
 */
export function AcknowledgeControl({
  source,
  entityKey,
  metricName,
  metricValue,
  currentStatus = "open",
  reopened = false,
  reopenReason,
  ackReason,
  scopeNote,
  onChanged,
}: {
  source: string;
  entityKey: string;
  metricName: string;
  metricValue: number;
  currentStatus?: AckStatus;
  reopened?: boolean;
  reopenReason?: string;
  ackReason?: string;
  /** Optional clarifying line shown in the popover — e.g. to make it explicit
   *  that acking covers a GROUP of rows, not just the clicked one. */
  scopeNote?: string;
  onChanged?: () => void;
}) {
  const features = useFeatures();
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
    // Escape closes the popover and returns focus to the trigger, so a
    // keyboard user is never stranded inside an open dialog (a11y).
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); triggerRef.current?.focus(); }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    // Move focus into the dialog (first preset) on open.
    firstPresetRef.current?.focus();
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (!features.writeback) return null;

  const acked = currentStatus !== "open";

  async function apply(status: AckStatus, reason: string) {
    setBusy(true);
    try {
      await postAck({
        source, entity_key: entityKey, status,
        reason: note.trim() || reason,
        metric_name: metricName, metric_at_ack: metricValue,
        snooze_days: snooze ?? undefined,
      });
      setOpen(false); setNote(""); setSnooze(null);
      onChanged?.();
    } finally {
      setBusy(false);
    }
  }

  async function reopen() {
    setBusy(true);
    try {
      await deleteAck(source, entityKey);
      setOpen(false);
      onChanged?.();
    } finally {
      setBusy(false);
    }
  }

  // Acknowledged/in-progress/won't-fix: show a status pill + a Reopen action.
  if (acked && !reopened) {
    const meta = STATUS_PILL[currentStatus] ?? STATUS_PILL.acknowledged;
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className={`pill text-[10px] ${meta.cls}`} title={ackReason || ""}>{meta.label}</span>
        <button type="button" onClick={reopen} disabled={busy}
          className="text-[11px] text-neutral hover:text-brand-dark underline decoration-dotted">
          Reopen
        </button>
      </span>
    );
  }

  return (
    <div ref={ref} className="relative inline-flex items-center gap-1.5">
      {reopened && (
        <span className="pill text-[10px] bg-danger/15 text-danger" title={reopenReason || "worse than when acknowledged"}>
          ⚠ Re-opened
        </span>
      )}
      <button
        ref={triggerRef}
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className="pill text-[11px] px-2.5 py-1 border border-border text-neutral hover:text-brand-dark hover:bg-surface inline-flex items-center gap-1 focus-visible:ring-2 focus-visible:ring-accent/60 focus:outline-none"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        Acknowledge ▾
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Acknowledge or ignore this alert"
          className="absolute left-0 top-full z-40 mt-1 w-64 rounded-lg border border-border bg-card p-3 shadow-lg flex flex-col gap-2"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="text-[11px] text-neutral">Mark this so it stops surfacing — it re-opens automatically if it gets materially worse.</p>
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
            placeholder="Optional note (e.g. new sources added)"
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
