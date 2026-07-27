import { InfoTip } from "./InfoTip";

// Consistent "needs further build" marker. Use it anywhere a panel/feature is
// not yet fully wired to live data or depends on work not yet done — the
// tooltip must say exactly what is outstanding so it's honest, not vague.
export function PreviewBadge({ tip, label = "Preview" }: { tip: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1 align-middle">
      <span className="pill bg-warning/15 text-warning uppercase tracking-wide text-[10px] leading-none">{label}</span>
      <InfoTip text={tip} label="What's outstanding?" />
    </span>
  );
}
