import { InsightPill } from "../shared/Pill";
import type { QueryRow } from "../../types";

/**
 * AI INSIGHT column cell: the insight-type pill + a one-line rationale.
 */
export function QueryInsightCell({ row }: { row: QueryRow }) {
  return (
    <div className="flex flex-col gap-0.5 max-w-[300px]">
      <div className="flex items-center gap-1.5">
        <InsightPill type={row.insight_type} />
      </div>
      <span className="text-[11px] text-neutral line-clamp-1">{row.rationale}</span>
    </div>
  );
}
