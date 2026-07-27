import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendPoint } from "../../types";
import { fmtMoney } from "../shared/format";
import { InfoTip } from "../shared/InfoTip";
import { useCurrency } from "../../store/appStore";

export function SpendSavingsChart({
  data,
  savingsEstimated = false,
}: {
  data: TrendPoint[];
  // Live mode: the per-week savings series is a modeled estimate (spend ×
  // headroom %), NOT dollars actually captured. When true we label it honestly
  // as "Estimated recoverable" rather than "captured". Demo keeps "captured".
  savingsEstimated?: boolean;
}) {
  const cur = useCurrency();
  const savingsLabel = savingsEstimated ? "Est. recoverable" : "Savings captured";
  const chartData = data.map((d) => ({
    week: d.week.slice(5), // MM-DD
    spend: d.spend_usd * cur.rate,
    savings: d.savings_usd * cur.rate,
  }));
  return (
    <div className="card">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          {savingsEstimated ? "Weekly spend & estimated recoverable" : "Weekly spend and captured savings"}
          <InfoTip text={savingsEstimated
            ? "Weekly spend in dollars, alongside the ESTIMATED recoverable amount each week (weekly spend × a modeled savings-headroom %). This is a modeled estimate of opportunity, not dollars actually captured."
            : "Weekly spend in dollars alongside savings actually captured to date. Captured savings ramp up as teams act on recommendations."} />
        </h3>
        {chartData.length > 0 && <span className="text-xs text-neutral">last {chartData.length} weeks</span>}
      </div>
      {chartData.length === 0 ? (
        <div className="py-12 text-center text-sm text-neutral">No trend data for this period.</div>
      ) : (
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <defs>
            <linearGradient id="spendGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#FF3621" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#FF3621" stopOpacity={0.02} />
            </linearGradient>
            <linearGradient id="savingsGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#1B8A4A" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#1B8A4A" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--color-border))" vertical={false} />
          <XAxis dataKey="week" tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }} axisLine={false} tickLine={false} />
          <YAxis
            tickFormatter={(v) => fmtMoney((v as number) / cur.rate, cur, { compact: true })}
            tick={{ fontSize: 11, fill: "rgb(var(--color-neutral))" }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: "rgb(var(--color-card))",
              border: "1px solid rgb(var(--color-border))",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
            formatter={(value: number, name: string) => [fmtMoney((value as number) / cur.rate, cur), name === "spend" ? "Spend" : savingsLabel]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} formatter={(v) => (v === "spend" ? "Spend" : savingsLabel)} />
          <Area
            type="monotone"
            dataKey="spend"
            name="spend"
            stroke="#FF3621"
            strokeWidth={2}
            fill="url(#spendGrad)"
            connectNulls
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="savings"
            name="savings"
            stroke="#1B8A4A"
            strokeWidth={2}
            fill="url(#savingsGrad)"
            connectNulls
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
      )}
    </div>
  );
}
