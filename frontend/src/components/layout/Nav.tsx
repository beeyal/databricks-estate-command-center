import { useEffect, useState } from "react";
import { useAppStore, useFeatures } from "../../store/appStore";
import type { CurrencyOption, PageId } from "../../types";

// The "Data Quality" tab is only present when features.dqm is on (SPEC §12) —
// it is inserted before Recommendations. All other tabs are always present.
const BASE_TABS: { id: PageId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "access", label: "Access" },
  { id: "workspaces", label: "Workspaces" },
  { id: "jobs", label: "Jobs" },
  { id: "pipelines", label: "Pipelines" },
  { id: "queries", label: "Query Advisor" },
  { id: "warehouses", label: "Warehouses" },
  { id: "clusters", label: "Clusters" },
  { id: "tables", label: "Tables" },
  { id: "governance", label: "Governance" },
  { id: "adoption", label: "Adoption & Value" },
];
const GENIE_TAB: { id: PageId; label: string } = { id: "genie", label: "Genie $" };
const AI_TAB: { id: PageId; label: string } = { id: "ai", label: "AI $" };
const DQM_TAB: { id: PageId; label: string } = { id: "dqm", label: "Data Quality" };
const RECS_TAB: { id: PageId; label: string } = { id: "recommendations", label: "Recommendations" };

function useDarkMode() {
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem("theme");
    if (stored) return stored === "dark";
    return true; // dark default
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);
  return [dark, setDark] as const;
}

function LogoMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <rect width="32" height="32" rx="6" className="fill-surface" />
      <path
        d="M6 20l5-6 4 4 6-8 5 6"
        fill="none"
        stroke="rgb(var(--color-accent))"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// Segmented USD / AUD currency toggle (SPEC §9). Persists via the store.
function CurrencyToggle({ currencies }: { currencies: CurrencyOption[] }) {
  const { currency, setCurrency } = useAppStore();
  const active = currencies.find((c) => c.code === currency) ?? currencies[0];
  return (
    <div className="inline-flex items-center gap-1.5">
      <div className="inline-flex rounded-lg border border-border overflow-hidden" title="List-price USD × FX rate — indicative AUD conversion">
        {currencies.map((c) => (
          <button
            key={c.code}
            type="button"
            aria-pressed={currency === c.code}
            onClick={() => setCurrency(c.code)}
            className={`px-2.5 py-1 text-xs font-medium transition ${
              currency === c.code ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
            }`}
          >
            {c.code}
          </button>
        ))}
      </div>
      {active && active.code !== "USD" && (
        <span className="text-[10px] text-neutral whitespace-nowrap">× {active.rate} FX</span>
      )}
    </div>
  );
}

export function Nav({
  demo,
  mode = "demo",
  currencies,
}: {
  demo: boolean;
  mode?: "demo" | "live";
  currencies: CurrencyOption[];
}) {
  const [dark, setDark] = useDarkMode();
  const { activePage, setActivePage } = useAppStore();
  const customer = useAppStore((s) => s.customer);
  const features = useFeatures();
  const tabs = [...BASE_TABS, GENIE_TAB, AI_TAB, ...(features.dqm ? [DQM_TAB] : []), RECS_TAB];
  // Subtitle: the tagline, plus the configured customer name (FINOPS_CUSTOMER_NAME)
  // once /api/config resolves. Blank customer → just the tagline (no stale name).
  const subtitle = customer
    ? `Databricks-native cost, governance & data-quality console · ${customer}`
    : "Databricks-native cost, governance & data-quality console";
  return (
    <header className="bg-card border-b border-border sticky top-0 z-40">
      <div className="max-w-[1500px] mx-auto px-6 pt-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <LogoMark />
            <div>
              <h1 className="text-base font-semibold leading-tight tracking-tight">Estate Command Center</h1>
              <p className="text-[11px] text-neutral leading-tight">{subtitle}</p>
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <CurrencyToggle currencies={currencies} />
            {/* Live badge intentionally hidden (a customer review — read as "real-time
                cost", caused confusion). Demo badge still shows so demo data is never mistaken
                for real. */}
            {mode !== "live" && demo && (
              <span className="pill bg-warning/15 text-warning uppercase tracking-wide">Demo data</span>
            )}
            <button
              type="button"
              onClick={() => setDark(!dark)}
              title="Toggle theme"
              aria-label="Toggle dark mode"
              className="flex items-center justify-center h-8 w-8 rounded-lg border border-border text-neutral hover:text-brand-dark hover:bg-surface transition"
            >
              {dark ? "☾" : "☀"}
            </button>
          </div>
        </div>
        {/* Plain page-nav, not an ARIA tablist: these switch full pages and there
            are no associated tabpanels / arrow-key roving, so aria-current="page"
            is the honest, correct semantic (a tablist would promise interactions
            that don't exist). */}
        <nav className="flex items-center gap-1 mt-3 -mb-px overflow-x-auto" aria-label="Sections">
          {tabs.map((tab) => {
            const active = activePage === tab.id;
            return (
              <button
                key={tab.id}
                aria-current={active ? "page" : undefined}
                type="button"
                onClick={() => setActivePage(tab.id)}
                className={
                  "px-3.5 py-2 text-sm font-medium whitespace-nowrap border-b-2 transition focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60 focus:outline-none " +
                  (active
                    ? "border-accent text-brand-dark"
                    : "border-transparent text-neutral hover:text-brand-dark")
                }
              >
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
