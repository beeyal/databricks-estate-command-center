import { create } from "zustand";
import type { CurrencyOption, Features, PageId } from "../types";

// Default flags used before /api/config resolves. Genie + DQM default on;
// ai_narration off — matches the backend default contract (data/config.py).
const DEFAULT_FEATURES: Features = { genie: true, ai_narration: false, dqm: true, writeback: true };

// Dual-currency (SPEC §9). USD is the source-of-truth list price; AUD is an
// indicative conversion at a configurable FX rate. The selection persists in
// localStorage so it survives reloads and applies across every page.
const CURRENCY_KEY = "finops-currency";
const DEFAULT_CURRENCIES: CurrencyOption[] = [
  { code: "USD", symbol: "$", rate: 1.0, label: "USD (list price)" },
  { code: "AUD", symbol: "A$", rate: 1.52, label: "AUD (× FX)" },
];

function initialCurrency(): string {
  try {
    return localStorage.getItem(CURRENCY_KEY) || "USD";
  } catch {
    return "USD";
  }
}

interface AppState {
  activePage: PageId;
  setActivePage: (page: PageId) => void;
  // Optional deep-link target: when Overview's top-recommendations / needs-
  // attention items navigate to the Recommendations tab, they set the clicked
  // rec's id here so that page can scroll to + expand that specific row. Cleared
  // once consumed. Null = normal navigation (no focus).
  focusRecId: string | null;
  gotoRecommendation: (id: string) => void;
  clearFocusRec: () => void;
  // Deep-link to the Recommendations tab scoped to one workspace (used by the
  // Overview "Top savings opportunities" rows, which are workspace-level headroom
  // items with no single rec id). Consumed + cleared by RecommendationsPage.
  focusRecWorkspace: string | null;
  gotoRecommendationsForWorkspace: (ws: string) => void;
  clearFocusRecWorkspace: () => void;
  // Deep-link to the Jobs tab scoped to one workspace (Chris feedback #2d — see
  // a workspace's active jobs). Consumed + cleared by JobsPage.
  focusJobsWorkspace: string | null;
  gotoJobsForWorkspace: (ws: string) => void;
  clearFocusJobsWorkspace: () => void;
  // global filters shared across pages
  workspace: string;
  setWorkspace: (w: string) => void;
  timeRange: string;
  setTimeRange: (t: string) => void;
  // currency toggle (persists)
  currency: string;
  currencies: CurrencyOption[];
  setCurrency: (c: string) => void;
  setCurrencies: (cs: CurrencyOption[]) => void;
  // Customer display label from /api/config (FINOPS_CUSTOMER_NAME). Shown in the
  // header; blank until config resolves so we don't flash a wrong name.
  customer: string;
  setCustomer: (c: string) => void;
  // Effective-cost discount from /api/config (#1/#2f). 0 ⇒ list only.
  effectiveDiscount: number;
  setEffectiveDiscount: (d: number) => void;
  // deploy-time feature flags (SPEC §10) — set once from /api/config
  features: Features;
  setFeatures: (f: Features) => void;
  // Whether the business-unit dimension exists (demo always; live only when a
  // workspace_bu_table is configured). When false the UI hides BU everywhere.
  buEnabled: boolean;
  setBuEnabled: (b: boolean) => void;
  // Whether the user->team dimension exists (Adoption tab).
  teamEnabled: boolean;
  setTeamEnabled: (b: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  activePage: "overview",
  setActivePage: (page) => set({ activePage: page }),
  focusRecId: null,
  gotoRecommendation: (id) => set({ activePage: "recommendations", focusRecId: id }),
  clearFocusRec: () => set({ focusRecId: null }),
  focusRecWorkspace: null,
  gotoRecommendationsForWorkspace: (ws) => set({ activePage: "recommendations", focusRecWorkspace: ws }),
  clearFocusRecWorkspace: () => set({ focusRecWorkspace: null }),
  focusJobsWorkspace: null,
  gotoJobsForWorkspace: (ws) => set({ activePage: "jobs", focusJobsWorkspace: ws }),
  clearFocusJobsWorkspace: () => set({ focusJobsWorkspace: null }),
  workspace: "all",
  setWorkspace: (w) => set({ workspace: w }),
  timeRange: "30d",
  setTimeRange: (t) => set({ timeRange: t }),
  currency: initialCurrency(),
  currencies: DEFAULT_CURRENCIES,
  setCurrency: (c) => {
    try {
      localStorage.setItem(CURRENCY_KEY, c);
    } catch {
      /* ignore */
    }
    set({ currency: c });
  },
  setCurrencies: (cs) => set({ currencies: cs.length ? cs : DEFAULT_CURRENCIES }),
  customer: "",
  setCustomer: (c) => set({ customer: c }),
  effectiveDiscount: 0,
  setEffectiveDiscount: (d) => set({ effectiveDiscount: d }),
  features: DEFAULT_FEATURES,
  setFeatures: (f) => set({ features: f }),
  buEnabled: true,
  setBuEnabled: (b) => set({ buEnabled: b }),
  teamEnabled: true,
  setTeamEnabled: (b) => set({ teamEnabled: b }),
}));

// Convenience hook: the resolved feature flags.
export function useFeatures(): Features {
  return useAppStore((s) => s.features);
}

// Convenience hook: whether to show the business-unit dimension.
export function useBuEnabled(): boolean {
  return useAppStore((s) => s.buEnabled);
}

// Convenience hook: whether to show the user->team dimension.
export function useTeamEnabled(): boolean {
  return useAppStore((s) => s.teamEnabled);
}

// Convenience hook: returns the active currency option + a USD→display formatter.
export function useCurrency() {
  const currency = useAppStore((s) => s.currency);
  const currencies = useAppStore((s) => s.currencies);
  const opt = currencies.find((c) => c.code === currency) ?? currencies[0] ?? DEFAULT_CURRENCIES[0];
  return opt;
}
