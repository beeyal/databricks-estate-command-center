import { DataTable } from "../shared/DataTable";
import type { Column } from "../shared/DataTable";
import { StatusPill } from "../shared/Pill";
import { InfoTip } from "../shared/InfoTip";
import { ProgressBar } from "../shared/ProgressBar";
import { fmtMoney } from "../shared/format";
import { useCurrency } from "../../store/appStore";
import type { Status, TaggingWorkspaceRow } from "../../types";

const BAR_TONE: Record<Status, "success" | "warning" | "danger" | "info"> = {
  Good: "success",
  Warning: "warning",
  Critical: "danger",
  Unknown: "info",
};

export function TaggingTable({ rows }: { rows: TaggingWorkspaceRow[] }) {
  const cur = useCurrency();
  const columns: Column<TaggingWorkspaceRow>[] = [
    {
      key: "workspace",
      header: "Workspace",
      sortValue: (r) => r.workspace,
      render: (r) => (
        <div className="min-w-0">
          <div className="font-medium truncate">{r.workspace}</div>
          <div className="text-[11px] text-neutral truncate">{r.bu}</div>
        </div>
      ),
    },
    {
      key: "spend",
      header: "Spend / mo",
      align: "right",
      sortValue: (r) => r.spend_usd_month,
      render: (r) => <span className="tabular-nums">{fmtMoney(r.spend_usd_month, cur, { compact: true })}</span>,
    },
    {
      key: "tagging",
      header: (
        <span className="inline-flex items-center gap-1">
          Tagging coverage
          <InfoTip text="Share of this workspace's spend on resources that carry cost-attribution tags. Higher means more spend can be traced to a business unit or cost centre." />
        </span>
      ),
      sortValue: (r) => r.tagging_pct,
      render: (r) => (
        <div className="flex items-center gap-2 min-w-[140px]">
          <ProgressBar value={r.tagging_pct} tone={BAR_TONE[r.status]} showPct={false} />
          <span className="text-[11px] tabular-nums text-neutral w-9 text-right">
            {Math.round(r.tagging_pct * 100)}%
          </span>
        </div>
      ),
    },
    {
      key: "untagged",
      header: (
        <span className="inline-flex items-center gap-1">
          Untagged / mo
          <InfoTip text="Dollars per month in this workspace on untagged resources, that is spend times one minus tagging coverage. It cannot be charged back to any business unit." />
        </span>
      ),
      align: "right",
      sortValue: (r) => r.untagged_usd_month,
      render: (r) => (
        <span className="tabular-nums text-danger">{fmtMoney(r.untagged_usd_month, cur, { compact: true })}</span>
      ),
    },
    {
      key: "status",
      header: "Status",
      align: "center",
      sortValue: (r) => r.status,
      render: (r) => <StatusPill status={r.status} />,
    },
  ];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold inline-flex items-center gap-1">
          Tagging coverage by workspace
          <InfoTip text="Per-workspace breakdown of cost-attribution tagging: monthly spend, percent of that spend tagged, and the untagged dollars that cannot be attributed to a business unit." />
        </h3>
        <span className="text-xs text-neutral">untagged spend cannot be attributed to a BU / cost centre</span>
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.workspace}
        initialSort={{ key: "untagged", dir: "desc" }}
      />
    </div>
  );
}
