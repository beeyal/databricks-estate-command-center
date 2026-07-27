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
import type { WorkspaceMonthPoint } from "../../types";
import { fmtMoney } from "../shared/format";
import { useCurrency } from "../../store/appStore";

// Monthly spend + savings trend for the workspace drill-down.
// Mirrors the Overview SpendSavingsChart styling for a consistent look.
// savingsEstimated (live): the savings series is a modeled estimate, not
// captured $ — label it honestly as "Est. recoverable".
export function WorkspaceTrendChart({
  data,
  savingsEstimated = false,
}: {
  data: WorkspaceMonthPoint[];
  savingsEstimated?: boolean;
}) {
  const cur = useCurrency();
  const savingsLabel = savingsEstimated ? "Est. recoverable" : "Savings captured";
  const chartData = data.map((d) => ({
    month: d.month.slice(2), // YY-MM to keep ticks compact
    spend: d.spend_usd * cur.rate,
    savings: d.savings_usd * cur.rate,
  }));
  if (chartData.length === 0) {
    return <div className="py-10 text-center text-sm text-neutral">No monthly trend for this workspace.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
        <defs>
          <linearGradient id="wsSpendGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#FF3621" stopOpacity={0.35} />
            <stop offset="100%" stopColor="#FF3621" stopOpacity={0.02} />
          </linearGradient>
          <linearGradient id="wsSavingsGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1B8A4A" stopOpacity={0.35} />
            <stop offset="100%" stopColor="#1B8A4A" stopOpacity={0.02} />
          </linearGradient>
        </defs>
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
          contentStyle={{
            background: "rgb(var(--color-card))",
            border: "1px solid rgb(var(--color-border))",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: "rgb(var(--color-brand-dark))" }}
          formatter={(value, name) => [fmtMoney((value as number) / cur.rate, cur), name === "spend" ? "Spend" : savingsLabel]}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} formatter={(v) => (v === "spend" ? "Spend" : savingsLabel)} />
        <Area type="monotone" dataKey="spend" name="spend" stroke="#FF3621" strokeWidth={2} fill="url(#wsSpendGrad)" connectNulls isAnimationActive={false} />
        <Area type="monotone" dataKey="savings" name="savings" stroke="#1B8A4A" strokeWidth={2} fill="url(#wsSavingsGrad)" connectNulls isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
