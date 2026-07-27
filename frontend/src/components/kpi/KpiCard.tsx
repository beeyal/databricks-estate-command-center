import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { InfoTip } from "../shared/InfoTip";

export type KpiTone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

interface KpiCardProps {
  label: string;
  value: string;
  hint?: string;
  /** Plain-language definition of the metric, surfaced via a "?" tooltip next to the label. */
  info?: string;
  icon?: ReactNode;
  tone?: KpiTone;
  delta?: { text: string; tone: "positive" | "negative" | "neutral" } | null;
}

const BORDER: Record<KpiTone, string> = {
  neutral: "border-l-border",
  accent: "border-l-accent",
  success: "border-l-success",
  warning: "border-l-warning",
  danger: "border-l-danger",
  info: "border-l-info",
};

// Splits "94.2%" → ["", "94.2", "%"] / "$1.23M" → ["$", "1.23", "M"]
function parseValue(v: string): [string, number, string, number] | null {
  const m = v.match(/^([^0-9-]*)(-?[0-9,]+(?:\.[0-9]+)?)(.*)$/);
  if (!m) return null;
  const numStr = m[2].replace(/,/g, "");
  const decimals = (numStr.split(".")[1] ?? "").length;
  return [m[1], parseFloat(numStr), m[3], decimals];
}

function useCountUp(rawValue: string): string {
  const [displayed, setDisplayed] = useState(rawValue);
  const rafRef = useRef<number | null>(null);
  const prevRef = useRef(rawValue);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = rawValue;
    if (rawValue === prev) return;
    const next = parseValue(rawValue);
    const from = parseValue(prev);
    if (!next || !from || next[0] !== from[0] || next[2] !== from[2]) {
      setDisplayed(rawValue);
      return;
    }
    const [prefix, toNum, suffix, decimals] = next;
    const fromNum = from[1];
    const duration = 600;
    const start = performance.now();
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const cur = fromNum + (toNum - fromNum) * eased;
      const shown = decimals ? cur.toFixed(decimals) : Math.round(cur).toLocaleString("en-AU");
      setDisplayed(`${prefix}${shown}${suffix}`);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
      else {
        rafRef.current = null;
        setDisplayed(rawValue);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [rawValue]);

  return displayed;
}

export function KpiCard({ label, value, hint, info, icon, tone = "neutral", delta }: KpiCardProps) {
  const displayedValue = useCountUp(value);
  const toneClass =
    delta?.tone === "positive"
      ? "bg-success/10 text-success"
      : delta?.tone === "negative"
        ? "bg-danger/10 text-danger"
        : "bg-border text-neutral";
  return (
    <div className={`card flex flex-col gap-2 border-l-4 ${BORDER[tone]}`}>
      <div className="text-xs uppercase tracking-wide text-neutral flex items-center gap-1.5">
        {icon && <span className="text-neutral">{icon}</span>}
        {label}
        {info && <InfoTip text={info} label={`What is ${label}?`} />}
      </div>
      <div className="text-2xl font-semibold leading-none tabular-nums">{displayedValue}</div>
      <div className="flex items-center gap-2 min-h-[18px]">
        {delta && <span className={`pill ${toneClass}`}>{delta.text}</span>}
        {hint && <span className="text-xs text-neutral">{hint}</span>}
      </div>
    </div>
  );
}
