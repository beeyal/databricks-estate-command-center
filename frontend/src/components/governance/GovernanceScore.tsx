import { InfoTip } from "../shared/InfoTip";
import type { GovernanceReport } from "../../types";

// Colour bands for the overall governance score (0-100).
function scoreTone(score: number): { stroke: string; text: string } {
  if (score >= 80) return { stroke: "#1B8A4A", text: "text-success" };
  if (score >= 55) return { stroke: "#F59E0B", text: "text-warning" };
  return { stroke: "#C0392B", text: "text-danger" };
}

// Circular progress gauge rendered with inline SVG (no chart lib for a single ring).
function ScoreRing({ score }: { score: number }) {
  const size = 132;
  const stroke = 10;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const tone = scoreTone(score);
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--color-border))" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone.stroke}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          style={{ transition: "stroke-dashoffset 700ms ease-out" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`text-3xl font-semibold tabular-nums leading-none ${tone.text}`}>{score}</span>
        <span className="text-[10px] uppercase tracking-wide text-neutral mt-1 inline-flex items-center gap-1">
          score / 100
          <InfoTip text="Overall cost-governance health, 0 to 100. Each control scores 1 for Good, 0.5 for Warning, 0 for Critical, then is weighted by importance and rescaled to 100." />
        </span>
      </div>
    </div>
  );
}

export function GovernanceScore({ report }: { report: GovernanceReport }) {
  const tone = scoreTone(report.score);
  return (
    <div className="card flex flex-col sm:flex-row items-center gap-6">
      <ScoreRing score={report.score} />
      <div className="flex-1 w-full">
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold inline-flex items-center gap-1">
            Governance posture
            <InfoTip text={`A composite health index across ${report.num_tiles} cost-governance controls, weighted by importance. Higher means fewer gaps in cost attribution, budgets, storage, compute, access and residency.`} />
          </h3>
          <span className={`pill ${tone.text} bg-border/40 inline-flex items-center gap-1`}>
            Grade {report.grade}
            <InfoTip text="Letter grade mapped from the 0 to 100 score: A is 80 and above, B is 70 to 79, C is 55 to 69, D is 40 to 54, E is below 40." />
          </span>
        </div>
        <p className="text-xs text-neutral mt-0.5">
          Weighted across {report.num_tiles} cost-governance controls. Each control is scored Good / Warning / Critical
          with the specific gap and next action.
        </p>
        <div className="grid grid-cols-3 gap-3 mt-4">
          <SummaryStat label="Good" value={report.counts.Good} cls="text-success" info="Controls that meet target, scoring the full 1 point each toward the governance score." />
          <SummaryStat label="Warning" value={report.counts.Warning} cls="text-warning" info="Controls partially met, scoring half a point each. They have a gap worth closing but are not yet critical." />
          <SummaryStat label="Critical" value={report.counts.Critical} cls="text-danger" info="Controls that fail target, scoring 0 points. These are the biggest drags on the score and need action first." />
        </div>
      </div>
    </div>
  );
}

function SummaryStat({ label, value, cls, info }: { label: string; value: number; cls: string; info?: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-surface/40 px-3 py-2.5">
      <div className={`text-2xl font-semibold tabular-nums leading-none ${cls}`}>{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-neutral mt-1 inline-flex items-center gap-1">
        {label}
        {info && <InfoTip text={info} label={`What is ${label}?`} />}
      </div>
    </div>
  );
}
