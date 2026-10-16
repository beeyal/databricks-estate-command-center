import { useState } from "react";
import { fetchPrincipals, fetchRisks, fetchAccessTree, fetchGroupTree, fetchPats } from "../api/client";
import { useApi } from "../hooks/useApi";
import { PageShell, LoadingCard, ErrorCard } from "../components/layout/PageShell";
import { KpiCard } from "../components/kpi/KpiCard";
import { KpiRow } from "../components/kpi/KpiRow";
import { ModeToggle } from "../components/access/ModeToggle";
import type { AccessMode } from "../components/access/ModeToggle";
import { DataCentricMode } from "../components/access/DataCentricMode";
import { PrincipalCentricMode } from "../components/access/PrincipalCentricMode";
import { RiskFlags } from "../components/access/RiskFlags";

type Panel = "explorer" | "risks";

export function AccessPage() {
  const [mode, setMode] = useState<AccessMode>("data");
  const [panel, setPanel] = useState<Panel>("explorer");
  const [selectedPrincipal, setSelectedPrincipal] = useState("");

  const principals = useApi(() => fetchPrincipals({}), []);
  const risks = useApi(() => fetchRisks({}), []);
  const tree = useApi(() => fetchAccessTree(), []);
  const groups = useApi(() => fetchGroupTree(), []);
  const pats = useApi(() => fetchPats(), []);

  const pdata = principals.data?.data ?? [];
  const rdata = risks.data?.data ?? [];
  const patData = pats.data?.data ?? [];

  // Selecting a principal anywhere jumps into principal-centric mode.
  function selectPrincipal(p: string) {
    setSelectedPrincipal(p);
    setMode("principal");
    setPanel("explorer");
  }

  const loading = principals.loading || tree.loading || groups.loading || risks.loading || pats.loading;
  // Core loading = only the system-table reads that back the explorer + risks;
  // excludes the slow admin-sync fetches (groups/pats) so the working tree isn't
  // topped by a spurious spinner while those settle/503.
  const coreLoading = principals.loading || tree.loading || risks.loading;
  // Groups + PATs come from the admin-sync job (admin APIs, not system tables),
  // so before that job runs they 503 with an "isn't ingested" message. That is
  // the EXPECTED fail-closed state, not a page error — treat it as a soft
  // "run the daily job" notice (mirrors Governance's pending_admin card) and keep
  // it OUT of the page-level error. Only the core system-table reads
  // (principals / tree / risks) surface as a blocking ErrorCard.
  const isAdminPending = (e: string | null) =>
    !!e && (e.includes("isn't ingested") || e.includes("finops_admin_sync"));
  const adminPending = isAdminPending(groups.error) || isAdminPending(pats.error);
  const groupsHardErr = groups.error && !isAdminPending(groups.error) ? groups.error : null;
  const patsHardErr = pats.error && !isAdminPending(pats.error) ? pats.error : null;
  const err = principals.error || tree.error || risks.error || groupsHardErr || patsHardErr;
  // KPIs read from the five fetches; only show them once the core population
  // (principals) has loaded, so a fresh load doesn't flash real-looking zeros.
  const kpisReady = !!principals.data;

  return (
    <PageShell
      title="Access"
      subtitle="Factual bidirectional grants explorer — who can access a securable, and what a principal can access. No recommendations, just the grant graph."
    >
      {/* What this tab is for (Chris feedback #3 — the tab wasn't self-explanatory). */}
      <div className="card bg-surface/40 border-l-4 border-l-accent">
        <h3 className="text-sm font-semibold mb-1">What this tab shows</h3>
        <p className="text-xs text-neutral max-w-4xl">
          A read-only map of Unity Catalog access across the estate — the real grant graph, not a recommendation engine.
          Use it two ways with the toggle below:
        </p>
        <ul className="text-xs text-neutral mt-2 space-y-1 list-disc pl-5 max-w-4xl">
          <li><span className="font-medium text-brand-dark">Who can access this?</span> — pick a securable (catalog / schema / table) and see every principal that can reach it and how (direct grant vs inherited vs via a group).</li>
          <li><span className="font-medium text-brand-dark">What can this principal access?</span> — pick a user, group or service principal and see everything it can reach.</li>
        </ul>
        <p className="text-xs text-neutral mt-2 max-w-4xl">
          The KPIs below profile the identity population; the <span className="font-medium text-brand-dark">Risk flags</span> surface
          deterministic access risks (over-privilege, admin sprawl, stale tokens) for review. Nothing here changes access —
          it's for audit, least-privilege review and answering "who can see what".
        </p>
      </div>

      {!kpisReady && loading && <LoadingCard label="Loading access graph…" />}
      {err && !principals.data && <ErrorCard message={err} />}
      {kpisReady && (
      <KpiRow cols={6}>
        <KpiCard label="Principals" value={String(pdata.length)} tone="neutral" info="Total identities that access can be granted to: users, groups, and service principals. The complete population of who could hold access." />
        <KpiCard label="Groups" value={String(pdata.filter((p) => p.type === "group").length)} tone="info" info="Named collections of principals. Granting to groups instead of individuals is best practice and keeps access easy to audit." />
        <KpiCard label="Users" value={String(pdata.filter((p) => p.type === "user").length)} tone="neutral" info="Human identities that sign in. Access held directly by a user rather than through a group is harder to track." />
        <KpiCard label="Service principals" value={String(pdata.filter((p) => p.type === "service_principal").length)} tone="accent" info="Non-human identities used by automation, jobs, and applications. They often hold broad, long-lived access, so watch them closely." />
        <KpiCard label="Risk flags" value={String(rdata.length)} tone="danger" hint={`${rdata.filter((r) => r.severity === "critical").length} critical`} info="Count of deterministic access risks detected, such as over-privilege, admin sprawl, or stale tokens. Higher means more to review." />
        <KpiCard label="PAT inventory" value={isAdminPending(pats.error) ? "—" : String(patData.length)} tone="warning" hint={isAdminPending(pats.error) ? "not ingested yet" : `${patData.filter((t) => t.stale).length} stale`} info="Number of personal access tokens (long-lived API credentials) in use. Old or unused tokens widen the attack surface. Ingested by the daily finops_admin_sync job — shows '—' until it runs." />
      </KpiRow>
      )}
      {/* Data-honesty (live): the grant graph comes from
          system.information_schema.table_privileges, which UC row-filters to only
          the securables the APP's service principal can see. So this covers only
          catalogs/schemas the app SP has visibility into — not necessarily the
          whole estate. Say so, and how to widen it, so a partial graph is never
          mistaken for the complete picture. */}
      {principals.data?.mode === "live" && (
        <div className="card border-l-4 border-l-info text-xs text-neutral flex items-start gap-1.5">
          <span className="text-info font-medium shrink-0">Scope</span>
          <span>
            This grant graph reflects only the catalogs &amp; schemas the app's service principal can see
            (Unity Catalog filters <span className="font-mono">system.information_schema</span> per-principal).
            To cover the whole estate, grant the app SP visibility across your catalogs —{" "}
            <span className="font-mono">GRANT BROWSE ON CATALOG &lt;catalog&gt; TO &lt;app-sp&gt;</span>{" "}
            (least-privilege: makes objects visible without data access). Optional — every other tab works from{" "}
            <span className="font-mono">system.*</span> alone.
          </span>
        </div>
      )}

      {/* Admin-sync-backed panels (group membership + PAT inventory) come from the
          daily finops_admin_sync job, not system tables. Before it runs they're
          simply unavailable — show a calm "run the daily job" notice, never a red
          error (the grant/system-table reads that power the rest of the page are
          fine). Mirrors the Governance pending-admin card. */}
      {kpisReady && adminPending && (
        <div className="card border-l-4 border-l-info flex flex-col gap-1 py-3">
          <div className="text-xs text-neutral">
            Group membership and PAT inventory come from admin APIs (not system tables) and
            aren't ingested yet — set <span className="font-mono text-brand-dark">FINOPS_INGEST_SCHEMA</span>{" "}
            (if it isn't already) and run the{" "}
            <span className="font-mono text-brand-dark">finops_admin_sync</span> job once to
            populate them. The grants explorer and risk flags below work without it.
          </div>
        </div>
      )}

      {/* Panel tabs: explorer (bidirectional) vs risk flags */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setPanel("explorer")}
          className={`px-3 py-1.5 text-sm font-medium rounded-lg border transition ${
            panel === "explorer" ? "border-accent text-brand-dark bg-surface" : "border-transparent text-neutral hover:text-brand-dark"
          }`}
        >
          Grants explorer
        </button>
        <button
          type="button"
          onClick={() => setPanel("risks")}
          className={`px-3 py-1.5 text-sm font-medium rounded-lg border transition ${
            panel === "risks" ? "border-accent text-brand-dark bg-surface" : "border-transparent text-neutral hover:text-brand-dark"
          }`}
        >
          Risk flags <span className="pill bg-danger/15 text-danger ml-1">{rdata.length}</span>
        </button>
      </div>

      {panel === "explorer" && (
        <>
          <ModeToggle mode={mode} onChange={setMode} />

          {coreLoading && <LoadingCard />}
          {err && <ErrorCard message={err} />}

          {mode === "data" && tree.data && (
            <DataCentricMode tree={tree.data.data} onSelectPrincipal={selectPrincipal} />
          )}

          {mode === "principal" && principals.data && (
            <PrincipalCentricMode
              principals={pdata}
              groups={groups.data?.data ?? []}
              selected={selectedPrincipal}
              onSelect={setSelectedPrincipal}
              groupsRefreshed={groups.data?.last_refreshed}
              patsRefreshed={pats.data?.last_refreshed}
            />
          )}
        </>
      )}

      {panel === "risks" && <RiskFlags onSelectPrincipal={selectPrincipal} />}
    </PageShell>
  );
}
