import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CostDrivers, DriverMoM, DriverSlice, SkuRow } from "../../types";
import { fmtMoney, fmtNum, fmtPct } from "../shared/format";
import { InfoTip } from "../shared/InfoTip";
import { useCurrency } from "../../store/appStore";

// Stable colour per billing_origin_product driver (design-system palette).
export const DRIVER_COLORS: Record<string, string> = {
  SQL: "#0D9488",
  JOBS: "#1B8A4A",
  INTERACTIVE: "#F59E0B",
  DLT: "#3B82F6",
  ALL_PURPOSE: "#EAB308",
  MODEL_SERVING: "#EC4899",
  AI_GATEWAY: "#8B5CF6",
  VECTOR_SEARCH: "#6366F1",
  PREDICTIVE_OPTIMIZATION: "#84CC16",
  LAKEBASE: "#14B8A6",
  APPS: "#22D3EE",
  DATA_QUALITY_MONITORING: "#A78BFA",
};

const FALLBACK = ["#0D9488", "#3B82F6", "#8B5CF6", "#F59E0B", "#C0392B", "#EC4899", "#84CC16"];
// Unmapped drivers (new/rare billing_origin_product values) get a fallback keyed
// on a stable hash of the CODE — so the same driver keeps one colour across every
// chart. (Was keyed on a call-site index that was never passed, collapsing all
// unmapped drivers to FALLBACK[0].)
function driverColor(code: string): string {
  if (DRIVER_COLORS[code]) return DRIVER_COLORS[code];
  let h = 0;
  for (let i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i)) | 0;
  return FALLBACK[Math.abs(h) % FALLBACK.length];
}

const tooltipStyle = {
  background: "rgb(var(--color-card))",
  border: "1px solid rgb(var(--color-border))",
  borderRadius: 8,
  fontSize: 12,
} as const;

// -----------------------------------------------------------------------------
// Cost by cost-driver (product) — horizontal bar
// -----------------------------------------------------------------------------
export function DriverBar({ drivers, title = "Cost by cost-driver (product)" }: { drivers: DriverSlice[]; title?: string }) {
  const cur = useCurrency();
  const rows = [...drivers].sort((a, b) => b.spend_usd_month - a.spend_usd_month);
  const data = rows.map((d) => ({ ...d, display: d.spend_usd_month * cur.rate }));
  return (
    <div className="rounded-lg border border-border bg-surface/30 p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-sm font-semibold flex items-center gap-1.5">
          {title}
          <InfoTip text="Monthly spend broken down by cost driver, the Databricks product that generated it (SQL, jobs, model serving, and so on)." />
        </div>
        <span className="text-[11px] text-neutral">billing_origin_product</span>
      </div>
      <ResponsiveContainer width="100%" height={Math.max(200, data.length * 26)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v) => fmtMoney((v as number) / cur.rate, cur, { compact: true })}
            tick={{ fontSize: 10, fill: "rgb(var(--color-neutral))" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={140}
            interval={0}
            tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
            formatter={(v: number, _n, p: { payload?: DriverSlice }) => [
              `${fmtMoney((v as number) / cur.rate, cur)} · ${fmtPct(p.payload?.pct_of_total ?? 0, 1)}`,
              "spend / mo",
            ]}
          />
          <Bar dataKey="display" radius={[0, 3, 3, 0]}>
            {data.map((d) => (
              <Cell key={d.driver} fill={driverColor(d.driver)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Monthly trend by top-5 drivers — multi-line
// -----------------------------------------------------------------------------
export function DriverTrendChart({
  trend,
  title = "Monthly trend — top 5 drivers",
}: {
  trend: CostDrivers["trend"];
  title?: string;
}) {
  const cur = useCurrency();
  const top5 = trend.series.slice(0, 5);
  const months = trend.months;
  // pivot to one row per month, one key per driver
  const data = months.map((m, i) => {
    const row: Record<string, number | string> = { month: m.slice(2) };
    top5.forEach((s) => {
      row[s.label] = (s.points[i]?.spend_usd ?? 0) * cur.rate;
    });
    return row;
  });
  return (
    <div className="rounded-lg border border-border bg-surface/30 p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-sm font-semibold flex items-center gap-1.5">
          {title}
          <InfoTip text="Monthly spend over time for the five largest cost drivers (products). Use it to spot which products are trending up before they become a problem." />
        </div>
        <span className="text-[11px] text-neutral">last {months.length} months</span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <YAxis
            tickFormatter={(v) => fmtMoney((v as number) / cur.rate, cur, { compact: true })}
            tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
            formatter={(v: number, n: string) => [fmtMoney((v as number) / cur.rate, cur), n]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {top5.map((s) => (
            <Line
              key={s.driver}
              type="monotone"
              dataKey={s.label}
              stroke={driverColor(s.driver)}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Driver change vs prior 30 days (%) — MoM spike detection
// -----------------------------------------------------------------------------
export function MoMChangeBar({
  mom,
  title = "Driver change vs prior 30 days (%)",
}: {
  mom: DriverMoM[];
  title?: string;
}) {
  const cur = useCurrency();
  // Show every active driver, biggest movers first — no truncation, so labels
  // like Vector Search are never dropped off the axis.
  // A NEW driver (prev $0 → spend this month) has mom_pct=null (infinite growth);
  // show it as a capped +100% spike bar and badge it "new" in the label, rather
  // than dropping it or printing NaN. Sort by $ delta so new/large movers rank up.
  const NEW_PCT = 100;
  const rows = [...mom]
    .filter((m) => m.spend_usd_month > 0)
    .sort((a, b) => (b.delta_usd ?? 0) - (a.delta_usd ?? 0));
  const data = rows.map((m) => ({
    ...m,
    pct: m.mom_pct == null ? NEW_PCT : Math.round(m.mom_pct * 100),
    isNew: m.mom_pct == null || m.is_new === true,
    label: (m.mom_pct == null || m.is_new === true) ? `${m.label} (new)` : m.label,
  }));
  return (
    <div className="rounded-lg border border-border bg-surface/30 p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-sm font-semibold flex items-center gap-1.5">
          {title}
          <InfoTip text="Percentage change in each driver's spend versus the prior 30 days. A jump of 25 percent or more is treated as a cost spike worth investigating." />
        </div>
        <span className="text-[11px] text-neutral">spike detection</span>
      </div>
      <ResponsiveContainer width="100%" height={Math.max(180, data.length * 24)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => `${v}%`} tick={{ fontSize: 10, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="label" width={140} interval={0} tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
            formatter={(v: number, _n, p: { payload?: DriverMoM & { isNew?: boolean } }) => [
              p.payload?.isNew
                ? `NEW this period · ${fmtMoney((p.payload?.delta_usd ?? 0), cur)}`
                : `${v > 0 ? "+" : ""}${v}% MoM · ${fmtMoney((p.payload?.delta_usd ?? 0), cur)} Δ`,
              "change vs prior 30d",
            ]}
          />
          <Bar dataKey="pct" radius={[0, 3, 3, 0]}>
            {data.map((d) => (
              <Cell key={d.driver} fill={d.pct >= 25 ? "#C0392B" : d.pct >= 0 ? "#F59E0B" : "#1B8A4A"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="mt-2 text-[11px] text-neutral">
        Drivers up ≥ 25% MoM fire a cost-spike recommendation (investigate + attribute).
      </p>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Cost by SKU (stacked bar, coloured by driver) — per workspace or estate
// -----------------------------------------------------------------------------
export function SkuStackedBar({ skus, title = "Cost by SKU (stacked by driver)" }: { skus: SkuRow[]; title?: string }) {
  const cur = useCurrency();
  // group SKUs by driver → one stacked bar with each driver as a segment
  const byDriver: Record<string, { driver: string; label: string; total: number }> = {};
  skus.forEach((s) => {
    const g = byDriver[s.driver] ?? { driver: s.driver, label: s.driver_label, total: 0 };
    g.total += s.total_cost;
    byDriver[s.driver] = g;
  });
  const drivers = Object.values(byDriver).sort((a, b) => b.total - a.total);
  // Key the row by driver CODE, not label: two codes sharing a display label
  // would otherwise overwrite each other's segment. The label is shown via the
  // Bar's `name` (tooltip/legend).
  const row: Record<string, number | string> = { name: "Estate" };
  drivers.forEach((d) => (row[d.driver] = d.total * cur.rate));
  return (
    <div className="rounded-lg border border-border bg-surface/30 p-4">
      <div className="text-sm font-semibold mb-2">{title}</div>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={[row]} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }} stackOffset="none">
          <XAxis
            type="number"
            tickFormatter={(v) => fmtMoney((v as number) / cur.rate, cur, { compact: true })}
            tick={{ fontSize: 10, fill: "rgb(var(--color-neutral))" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis type="category" dataKey="name" width={56} tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
            formatter={(v: number, n: string) => [fmtMoney((v as number) / cur.rate, cur), n]}
          />
          {drivers.map((d) => (
            <Bar key={d.driver} dataKey={d.driver} name={d.label} stackId="sku" fill={driverColor(d.driver)} radius={0} />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
        {drivers.map((d) => (
          <div key={d.driver} className="flex items-center gap-2 text-[11px] min-w-0">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: driverColor(d.driver) }} />
            <span className="truncate text-brand-dark">{d.label}</span>
            <span className="ml-auto shrink-0 tabular-nums text-neutral">{fmtMoney(d.total, cur, { compact: true })}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------------
// DBU by SKU — bar
// -----------------------------------------------------------------------------
export function DbuBySkuBar({ dbu }: { dbu: CostDrivers["dbu_by_sku"] }) {
  const rows = [...dbu].sort((a, b) => b.dbus_month - a.dbus_month).slice(0, 12);
  const data = rows.map((r) => ({ ...r, short: r.sku.replace(/_AUSTRALIA_EAST$/, "").replace(/^PREMIUM_/, "") }));
  return (
    <div className="rounded-lg border border-border bg-surface/30 p-4">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-sm font-semibold">DBU consumption by SKU</div>
        <span className="text-[11px] text-neutral">DBUs / mo</span>
      </div>
      <ResponsiveContainer width="100%" height={Math.max(200, data.length * 24)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" horizontal={false} />
          <XAxis type="number" tickFormatter={(v) => fmtNum(v as number, { compact: true })} tick={{ fontSize: 10, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="short" width={210} interval={0} tick={{ fontSize: 9, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
            formatter={(v: number) => [`${fmtNum(v as number)} DBUs`, "monthly"]}
          />
          <Bar dataKey="dbus_month" radius={[0, 3, 3, 0]}>
            {data.map((d) => (
              <Cell key={d.sku} fill={driverColor(d.driver)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// -----------------------------------------------------------------------------
// SKU-level breakdown table (total_cost + pct_of_total)
// -----------------------------------------------------------------------------
export function SkuBreakdownTable({ skus }: { skus: SkuRow[] }) {
  const cur = useCurrency();
  const rows = [...skus].sort((a, b) => b.total_cost - a.total_cost);
  return (
    <div className="rounded-lg border border-border bg-surface/30 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-border bg-surface/60 text-[11px] uppercase tracking-wide text-neutral">
              <th className="px-3 py-2 text-left">SKU (sku_name)</th>
              <th className="px-3 py-2 text-left">Driver</th>
              <th className="px-3 py-2 text-right">Total cost / mo</th>
              <th className="px-3 py-2 text-right">DBUs / mo</th>
              <th className="px-3 py-2 text-right">% of total</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.sku} className="border-b border-border/60 hover:bg-surface/50">
                <td className="px-3 py-2 font-mono text-[11px]">
                  {r.sku}
                </td>
                <td className="px-3 py-2 text-xs text-neutral">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: driverColor(r.driver) }} />
                    {r.driver_label}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(r.total_cost, cur)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-neutral">{r.dbus_month ? fmtNum(r.dbus_month) : "—"}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  <span className="inline-flex items-center gap-2 justify-end">
                    <span className="h-1.5 w-16 rounded-full bg-border/60 overflow-hidden hidden sm:inline-block">
                      <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.min(100, r.pct_of_total * 100)}%` }} />
                    </span>
                    {fmtPct(r.pct_of_total, 1)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
