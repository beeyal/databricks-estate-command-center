// Subtle "last refreshed" indicator for admin-API-sourced data ingested by the
// daily finops_admin_sync job. Renders nothing when no timestamp is available.
function formatRefreshed(iso: string): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const diffMs = Date.now() - then.getTime();
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return then.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function LastRefreshed({ at }: { at?: string }) {
  if (!at) return null;
  return (
    <span className="text-xs text-neutral" title={new Date(at).toLocaleString()}>
      ↻ Last refreshed {formatRefreshed(at)}
    </span>
  );
}
