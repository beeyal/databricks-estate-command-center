// Section-level "needs further build" notes. Attach <PreviewBadge tip={...}/>
// next to the specific section heading that isn't fully wired — NOT the whole
// tab. Keep each note specific about what's outstanding.
export const PREVIEW_NOTES = {
  recommendations_feed:
    "In live mode the plays are triggered by real system-table estate metrics (billing, query.history, tables, privileges, tags/classification), but the savings figures remain modeled estimates from the recommendation catalog — not measured recovery. Some signals aren't in system tables and are handled honestly: BU collapses to workspace (billing has no BU dimension), idle-warehouse plays don't fire (idle % isn't in billing), per-table hygiene savings are deferred to drill-down, and admin-API governance controls (budgets, cluster policy, PATs, alerts) stay Preview and are excluded from this feed. In demo mode the feed is illustrative.",
  // NOTE: the Access PAT/groups, Governance admin-API tiles, and Tables file-
  // hygiene panels each carry their OWN live/demo/"run finops_admin_sync" state
  // in-place, so a generic "sample" Preview badge there would be inaccurate in
  // live — those keys were removed rather than mis-applied.
} as const;
