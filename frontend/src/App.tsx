import { useEffect, useState, lazy, Suspense } from "react";
import { Nav } from "./components/layout/Nav";
import { useAppStore, useFeatures } from "./store/appStore";
import { fetchConfig } from "./api/client";
import type { AppConfig } from "./types";
import { AskGenieBanner } from "./components/genie/AskGenieBanner";
import { LoadingCard } from "./components/layout/PageShell";

// Pages are code-split (React.lazy) so the initial bundle is just the shell +
// the first tab, not all 14 pages + their charts — each tab's chunk loads only
// when opened. The pages use NAMED exports, so map them to a default for lazy().
const OverviewPage = lazy(() => import("./pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const AccessPage = lazy(() => import("./pages/AccessPage").then((m) => ({ default: m.AccessPage })));
const WorkspacesPage = lazy(() => import("./pages/WorkspacesPage").then((m) => ({ default: m.WorkspacesPage })));
const JobsPage = lazy(() => import("./pages/JobsPage").then((m) => ({ default: m.JobsPage })));
const PipelinesPage = lazy(() => import("./pages/PipelinesPage").then((m) => ({ default: m.PipelinesPage })));
const QueryAdvisorPage = lazy(() => import("./pages/QueryAdvisorPage").then((m) => ({ default: m.QueryAdvisorPage })));
const WarehousesMonitorPage = lazy(() => import("./pages/WarehousesMonitorPage").then((m) => ({ default: m.WarehousesMonitorPage })));
const ClustersPage = lazy(() => import("./pages/ClustersPage").then((m) => ({ default: m.ClustersPage })));
const TablesPage = lazy(() => import("./pages/TablesPage").then((m) => ({ default: m.TablesPage })));
const GovernancePage = lazy(() => import("./pages/GovernancePage").then((m) => ({ default: m.GovernancePage })));
const RecommendationsPage = lazy(() => import("./pages/RecommendationsPage").then((m) => ({ default: m.RecommendationsPage })));
const AdoptionPage = lazy(() => import("./pages/AdoptionPage").then((m) => ({ default: m.AdoptionPage })));
const GeniePage = lazy(() => import("./pages/GeniePage").then((m) => ({ default: m.GeniePage })));
const AiPage = lazy(() => import("./pages/AiPage").then((m) => ({ default: m.AiPage })));
const DqmPage = lazy(() => import("./pages/DqmPage").then((m) => ({ default: m.DqmPage })));

function App() {
  const { activePage, setActivePage, setCurrencies, setFeatures, setBuEnabled, setTeamEnabled, setCustomer, setEffectiveDiscount } = useAppStore();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const features = useFeatures();

  useEffect(() => {
    fetchConfig()
      .then((c) => {
        setConfig(c);
        if (c.currencies?.length) setCurrencies(c.currencies);
        if (c.features) setFeatures(c.features);
        if (typeof c.bu_enabled === "boolean") setBuEnabled(c.bu_enabled);
        if (typeof c.team_enabled === "boolean") setTeamEnabled(c.team_enabled);
        if (c.customer) setCustomer(c.customer);
        if (typeof c.effective_discount === "number") setEffectiveDiscount(c.effective_discount);
      })
      .catch(() => setConfig(null));
  }, [setCurrencies, setFeatures, setBuEnabled, setTeamEnabled, setCustomer, setEffectiveDiscount]);

  // Guard: if DQM is off but the active page is the DQM tab (e.g. after a flag
  // flip), fall back to Overview so no gated page can render.
  useEffect(() => {
    if (!features.dqm && activePage === "dqm") setActivePage("overview");
  }, [features.dqm, activePage, setActivePage]);

  const currencies = useAppStore((s) => s.currencies);

  return (
    <div className="min-h-full bg-surface text-brand-dark">
      <Nav demo={config?.demo ?? true} mode={config?.mode ?? "demo"} currencies={currencies} />
      {/* No live-mode banner — the customer doesn't need it. Demo mode is marked
          by the "Demo data" badge in the nav; live mode shows no chrome. */}
      {/* Live was requested but the app fell back to demo (warehouse/gold not set
          yet) — tell the operator exactly how to finish going live. */}
      {config?.live_setup_note && (
        <div className="border-b border-warning/30 bg-warning/10 px-4 py-2 text-[13px] text-brand-dark">
          <span className="font-semibold text-warning">Finish setup to go live — </span>
          {config.live_setup_note}
        </div>
      )}
      {/* Ask Genie banner — only when features.genie is on (SPEC §11) */}
      {features.genie && <AskGenieBanner />}
      <main>
        <Suspense fallback={<div className="p-6"><LoadingCard label="Loading…" /></div>}>
          {activePage === "overview" && <OverviewPage />}
          {activePage === "access" && <AccessPage />}
          {activePage === "workspaces" && <WorkspacesPage />}
          {activePage === "jobs" && <JobsPage />}
          {activePage === "pipelines" && <PipelinesPage />}
          {activePage === "queries" && <QueryAdvisorPage />}
          {activePage === "warehouses" && <WarehousesMonitorPage />}
          {activePage === "clusters" && <ClustersPage />}
          {activePage === "tables" && <TablesPage />}
          {activePage === "governance" && <GovernancePage />}
          {activePage === "adoption" && <AdoptionPage />}
          {activePage === "genie" && <GeniePage />}
          {activePage === "ai" && <AiPage />}
          {activePage === "recommendations" && <RecommendationsPage />}
          {activePage === "dqm" && features.dqm && <DqmPage />}
        </Suspense>
      </main>
    </div>
  );
}

export default App;
