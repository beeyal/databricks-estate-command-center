interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
}

// Lightweight inline SVG sparkline — no chart lib overhead for table cells.
// Stroke colour is derived from the trend direction (rising spend = red,
// falling = green, flat = grey), so there's no caller-supplied colour.
export function Sparkline({ data, width = 88, height = 26 }: SparklineProps) {
  if (!data || data.length < 2)
    return <span className="text-neutral text-xs" title="Not enough history yet — fewer than two data points in the trend window.">—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const flat = max - min === 0;
  const range = max - min || 1;
  const step = width / (data.length - 1);
  // Draw inside a small vertical margin so the line isn't clipped at the edges,
  // and center a genuinely-flat series mid-height (a single-value/constant trend
  // reads as a flat mid-line, not a line pinned to the bottom).
  const pad = 3;
  const usable = height - pad * 2;
  const y = (v: number) => flat ? height / 2 : height - pad - ((v - min) / range) * usable;
  const points = data
    .map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  const last = data[data.length - 1];
  const first = data[0];
  // Strict >: a genuinely flat trend (last === first, or the all-equal `flat`
  // case) is neutral grey — not the danger-red "rising spend" signal.
  const strokeColor = flat || last === first ? "#676767" : last > first ? "#C0392B" : "#1B8A4A";
  // Trend conveyed by colour alone is invisible to SR + colour-blind users, so
  // give the SVG a text alternative describing the direction + magnitude.
  const dir = flat || last === first ? "flat" : last > first ? "rising" : "falling";
  const pct = first ? Math.round(Math.abs((last - first) / first) * 100) : 0;
  const label = dir === "flat"
    ? "Trend: flat over the window."
    : `Trend: ${dir} ${pct}% over the window.`;
  return (
    <svg width={width} height={height} className="overflow-visible" role="img" aria-label={label}>
      <title>{label}</title>
      <polyline points={points} fill="none" stroke={strokeColor} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle
        cx={((data.length - 1) * step).toFixed(1)}
        cy={y(last).toFixed(1)}
        r={2}
        fill={strokeColor}
      />
    </svg>
  );
}
