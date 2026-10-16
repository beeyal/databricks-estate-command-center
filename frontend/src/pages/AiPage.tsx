import { useMemo, useState } from "react";
import { fetchAiCost } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { DataTable } from "../components/shared/DataTable";
import type { Column } from "../components/shared/DataTable";
import { FilterBar, MultiDropdown, SearchBox } from "../components/shared/FilterBar";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { Pill } from "../components/shared/Pill";
import { InfoTip } from "../components/shared/InfoTip";
import { fmtMoney, fmtNum, fmtPct, byNamedFirst } from "../components/shared/format";
import { useCurrency } from "../store/appStore";
import type { AiEndpoint, AiCaller } from "../types";

// AI $ tab — all AI/GenAI billing products (Model Serving, Foundation Model API,
// Vector Search, Agent Bricks, AI Functions, fine-tuning) by product, endpoint,
// owner and workspace, with cost-optimisation flags. Genie has its own page.
// Deterministic billing attribution (calls no model), so ALWAYS on.

const PRODUCT_COLOR: Record<string, string> = {
  MODEL_SERVING: "bg-[#EC4899]",
  AI_GATEWAY: "bg-[#8B5CF6]",
  VECTOR_SEARCH: "bg-[#6366F1]",
  AGENT_BRICKS: "bg-[#0D9488]",
  AI_FUNCTIONS: "bg-[#F59E0B]",
  FOUNDATION_MODEL_TRAINING: "bg-[#C0392B]",
};
const productColor = (code: string) => PRODUCT_COLOR[code] ?? "bg-info";

export function AiPage() {
  const cur = useCurrency();
  const [workspace, setWorkspace] = useState<string[]>([]);
  const [search, setSearch] = useState("");

  // Fetch the estate ONCE and filter client-side (mirrors the Genie page). The
  // old approach sent the workspace to the API and derived the dropdown from the
  // scoped response — so multi-select never matched (server did an exact `==` on
  // a comma-joined string) and picking one workspace collapsed the option list.
  const { data, loading, error } = useApi(() => fetchAiCost({}), []);
  const ai = data?.data;

  const wsOptions = useMemo(
    () =>
      (ai?.by_workspace ?? [])
        .map((w) => ({ value: w.workspace, label: w.workspace }))
        .sort(byNamedFirst),
    [ai],
  );

  const endpoints = useMemo(() => {
    let e = ai?.endpoints ?? [];
    if (workspace.length) e = e.filter((x) => workspace.includes(x.workspace));
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      e = e.filter((x) => x.name.toLowerCase().includes(q) || x.owner.toLowerCase().includes(q) || x.product_label.toLowerCase().includes(q));
    }
    return e;
  }, [ai, workspace.join(","), search]);

  const maxProduct = Math.max(1, ...(ai?.by_product ?? []).map((p) => p.list_usd));

  const endpointColumns: Column<AiEndpoint>[] = [
    { key: "name", header: "Endpoint / workload", sortValue: (e) => e.name, render: (e) => (
      <div className="flex flex-col gap-0.5 max-w-[240px]">
        <span className="font-mono text-xs text-brand-dark truncate">{e.name}</span>
        <span className="text-[11px] text-neutral">{e.workspace}</span>
      </div>
    ) },
    { key: "product", header: (<span className="inline-flex items-center gap-1">Product<InfoTip text="The AI billing product: Model Serving, Foundation Model API (pay-per-token LLMs), Vector Search, Agent Bricks, AI Functions, or fine-tuning/training." /></span>), sortValue: (e) => e.product_label, render: (e) => (
      <span className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 rounded-sm ${productColor(e.product)}`} />{e.product_label}</span>
    ) },
    { key: "owner", header: "Owner", sortValue: (e) => e.owner, render: (e) => <span className="font-mono text-xs text-neutral truncate max-w-[170px] inline-block">{e.owner}</span> },
    { key: "mode", header: (<span className="inline-flex items-center gap-1">Mode<InfoTip text="How it bills: Pay-per-token (per request), Provisioned throughput (reserved capacity, bills 24x7), Serverless, Standard, or Job. GPU endpoints cost the most." /></span>), align: "center", sortValue: (e) => e.mode ?? "", render: (e) => (
      <span className="inline-flex items-center gap-1">
        <Pill className="bg-border/60 text-neutral">{e.mode ?? "—"}</Pill>
        {e.gpu === true && <Pill className="bg-danger/15 text-danger">GPU</Pill>}
      </span>
    ) },
    { key: "dbus", header: "DBUs", align: "right", sortValue: (e) => e.dbus_month, render: (e) => <span className="tabular-nums text-neutral">{fmtNum(e.dbus_month)}</span> },
    { key: "usd", header: (<span className="inline-flex items-center gap-1">$ / mo<InfoTip text="Endpoint cost this month at list price (DBUs times effective list rate)." /></span>), align: "right", sortValue: (e) => e.list_usd_month, render: (e) => <span className="tabular-nums">{fmtMoney(e.list_usd_month, cur)}</span> },
  ];

  const userColumns: Column<NonNullable<typeof ai>["by_user"][number]>[] = [
    { key: "user", header: "Owner", sortValue: (u) => u.user, render: (u) => <span className="font-mono text-xs text-brand-dark truncate max-w-[220px] inline-block">{u.user}</span> },
    { key: "top", header: "Top product", sortValue: (u) => u.top_product ?? "", render: (u) => <span className="text-xs">{u.top_product ?? "—"}</span> },
    { key: "eps", header: "Endpoints", align: "center", sortValue: (u) => u.endpoints, render: (u) => <span className="tabular-nums">{u.endpoints}</span> },
    { key: "usd", header: "$ / mo", align: "right", sortValue: (u) => u.list_usd, render: (u) => <span className="tabular-nums">{fmtMoney(u.list_usd, cur)}</span> },
  ];

  const callerColumns: Column<AiCaller>[] = [
    { key: "requester", header: "Caller", sortValue: (c) => c.requester, render: (c) => (
      <span className="font-mono text-xs text-brand-dark truncate max-w-[240px] inline-block flex items-center gap-1.5">
        {c.requester}
        {c.is_service_principal && <Pill className="bg-info/15 text-info text-[10px]">service principal</Pill>}
      </span>
    ) },
    { key: "requests", header: "Requests", align: "right", sortValue: (c) => c.requests, render: (c) => <span className="tabular-nums text-neutral">{fmtNum(c.requests)}</span> },
    { key: "tokens", header: "Tokens", align: "right", sortValue: (c) => c.tokens, render: (c) => <span className="tabular-nums text-neutral">{fmtNum(c.tokens, { compact: true })}</span> },
    { key: "cost", header: "Est. cost share / mo", align: "right", sortValue: (c) => c.est_usd_month, render: (c) => <span className="tabular-nums">{fmtMoney(c.est_usd_month, cur, { compact: true })}</span> },
    { key: "endpoints", header: "Top endpoints", sortValue: (c) => c.num_endpoints, render: (c) => (
      <div className="flex flex-col gap-0.5 text-xs">
        {c.endpoints.slice(0, 3).map((e) => <span key={e} className="text-neutral truncate">{e}</span>)}
        {c.endpoints.length > 3 && <span className="text-neutral">+ {c.endpoints.length - 3} more</span>}
      </div>
    ) },
  ];

  return (
    <PageShell
      title="AI spend"
      subtitle="All AI billing products — Model Serving, Foundation Model API, Vector Search, Agent Bricks, AI Functions, fine-tuning — by product, endpoint, owner and workspace. Figures are list price. (Genie has its own tab.)"
    >
      {loading && !ai && <LoadingCard />}
      {error && <ErrorCard message={error} />}
      {ai && (
        <>
          <FilterBar>
            <MultiDropdown label="Workspace" value={workspace} onChange={setWorkspace} options={wsOptions} allLabel="All workspaces" />
            <SearchBox value={search} onChange={setSearch} placeholder="Filter by endpoint, owner or product…" />
          </FilterBar>

          <div className="text-xs text-neutral flex flex-col gap-1.5">
            <p>Genie (Spaces and Genie Code) is billed separately — see the Genie $ tab. The Databricks Assistant isn't metered.</p>
          </div>

          <KpiRow cols={ai.flags.length > 0 ? 6 : 5}>
            <KpiCard label="AI $ / mo" value={fmtMoney(ai.summary.total_list_usd_month, cur, { compact: true })} tone="accent"
              hint={ai.total_platform_spend_usd_month ? `${fmtPct(ai.summary.total_list_usd_month / ai.total_platform_spend_usd_month, 1)} of total spend` : "list price"}
              info="Total AI spend this month across all AI billing products (the numerator excludes Genie, which has its own page). The hint shows it as a share of TOTAL platform spend this month (that denominator includes every product — all AI and Genie included), so it's directional context, not an exact ratio." />
            <KpiCard label="GPU $ / mo" value={fmtMoney(ai.summary.gpu_usd_month, cur, { compact: true })} tone="danger" info="Spend on GPU-backed endpoints (model serving / training). GPUs are the most expensive AI compute — the first place to look for savings." />
            {/* Flagged-savings only shown when there are real flags — the flag
                heuristics need scale-to-zero, which live billing doesn't expose,
                so we don't surface a hollow $0 estimate. */}
            {ai.flags.length > 0 && (
              <KpiCard label="Flagged savings" value={fmtMoney(ai.summary.flagged_savings_usd_month, cur, { compact: true })} tone="success" info="Estimated monthly $ recoverable from the cost-optimisation flags below." />
            )}
            <KpiCard label="Endpoints" value={String(ai.summary.num_endpoints)} tone="info" info="Number of AI endpoints / workloads with spend this month (serving endpoints, vector indexes, agents, AI-function jobs, training runs)." />
            <KpiCard label="AI users" value={String(ai.summary.distinct_users)} info="Distinct owners of AI endpoints/workloads in the selected scope." />
            <KpiCard label="Products" value={String(ai.summary.num_products)} info="Distinct AI billing products in use (Model Serving, Foundation Model API, Vector Search, Agent Bricks, AI Functions, fine-tuning)." />
          </KpiRow>

          {/* Spend by AI product */}
          <div className="card flex flex-col gap-3">
            <div className="text-sm font-semibold flex items-center gap-1.5">
              Spend by AI product
              <InfoTip text="AI spend grouped by billing_origin_product. Model Serving = hosted models; Foundation Model API = pay-per-token LLMs; Vector Search = index compute; Agent Bricks = agent workloads; AI Functions = ai_* SQL functions; Fine-tuning = model training runs." />
            </div>
            <div className="flex flex-col gap-2.5">
              {ai.by_product.map((p) => (
                <div key={p.code} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-1.5"><span className={`h-2.5 w-2.5 rounded-sm ${productColor(p.code)}`} />{p.label}</span>
                    <span className="tabular-nums text-neutral">{fmtMoney(p.list_usd, cur, { compact: true })} · {p.endpoints} ep · {fmtPct(p.pct, 1)}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-border/60 overflow-hidden">
                    <div className={`h-full rounded-full ${productColor(p.code)}`} style={{ width: `${(p.list_usd / maxProduct) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Cost-optimisation flags */}
          {ai.flags.length > 0 && (
            <div className="card border-l-4 border-l-warning flex flex-col gap-2 py-3">
              <div className="text-sm font-semibold flex items-center gap-1.5">
                Cost-optimisation opportunities
                <InfoTip text="AI endpoints worth investigating: always-on GPU endpoints, low-traffic provisioned endpoints billing 24x7, and similar. Estimated monthly saving shown per item." />
              </div>
              <div className="flex flex-col gap-1.5">
                {ai.flags.map((f) => (
                  <div key={f.endpoint} className="flex items-start justify-between gap-3 text-xs">
                    <span className="flex flex-col">
                      <span className="font-mono text-brand-dark">{f.endpoint} <span className="text-neutral">· {f.workspace}</span></span>
                      <span className="text-neutral">{f.issue}</span>
                    </span>
                    <span className="tabular-nums text-success shrink-0">~{fmtMoney(f.saving_usd_month, cur)}/mo</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Endpoints */}
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold flex items-center gap-1.5">
              AI endpoints & workloads
              <InfoTip text="One line per endpoint × workspace × owner with spend (so the same endpoint used in several workspaces appears once per workspace — that's why this list is longer than the distinct-endpoint KPI). Shows product, owner, billing mode, GPU, scale-to-zero, DBUs and $/mo. Sort by $ to find the biggest line items." />
              {endpoints.length > 150 && <span className="text-xs font-normal text-neutral">top 150 of {endpoints.length} line items by $</span>}
            </h3>
            <DataTable columns={endpointColumns} rows={endpoints.slice(0, 150)} rowKey={(e) => `${e.workspace}|${e.name}`} initialSort={{ key: "usd", dir: "desc" }} emptyMessage="No AI spend for this scope." />
          </div>

          {/* Top AI users */}
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold flex items-center gap-1.5">
              Top AI owners
              <InfoTip text="Owners ranked by AI spend across their endpoints — who is driving AI cost and which product dominates for them." />
            </h3>
            <DataTable columns={userColumns} rows={ai.by_user} rowKey={(u) => u.user} initialSort={{ key: "usd", dir: "desc" }} emptyMessage="No AI owners for this scope." />
          </div>

          {/* Who is calling */}
          {ai.callers && ai.callers.length > 0 && (
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold flex items-center gap-1.5">
                Who is calling
                <InfoTip text="Callers of AI endpoints ranked by token usage. Cost is an estimate allocated by token share. Comes from system.serving.endpoint_usage; includes shared pay-per-token endpoints that billing attributes to the endpoint owner." />
              </h3>
              {ai.callers_note && (
                <div className="card border-l-4 border-l-info text-xs text-neutral px-3 py-2">
                  <p>{ai.callers_note}</p>
                </div>
              )}
              <DataTable columns={callerColumns} rows={ai.callers} rowKey={(c) => c.requester} initialSort={{ key: "cost", dir: "desc" }} emptyMessage="No callers data available." />
            </div>
          )}

          {/* Caveats */}
          <div className="card flex flex-col gap-2 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-neutral">How to read this</div>
            <ul className="flex flex-col gap-1 text-[11px] text-neutral">
              {ai.caveats.map((c, i) => (
                <li key={i} className="flex gap-1.5"><span className="text-warning shrink-0" aria-hidden>·</span><span>{c}</span></li>
              ))}
            </ul>
          </div>
        </>
      )}
    </PageShell>
  );
}
