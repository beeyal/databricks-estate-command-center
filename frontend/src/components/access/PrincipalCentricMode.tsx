import { useMemo, useState } from "react";
import { fetchPrincipalAccess } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { LoadingCard, ErrorCard } from "../layout/PageShell";
import { DataTable } from "../shared/DataTable";
import type { Column } from "../shared/DataTable";
import { FilterBar, Dropdown, SearchBox } from "../shared/FilterBar";
import type { EffectiveGrant, GroupNode, Principal } from "../../types";
import { PrivilegePill, ViaPill, PrincipalTypePill, SecurableTypePill, EntitlementPill } from "./AccessPills";
import { GroupTree } from "./GroupTree";
import { InfoTip } from "../shared/InfoTip";
import { LastRefreshed } from "../shared/LastRefreshed";

// "What can this principal access?" — pick a group / user / SP and see all
// entitlements + workspace assignments + object grants, resolved through
// (nested) group membership.
export function PrincipalCentricMode({
  principals,
  groups,
  selected,
  onSelect,
  groupsRefreshed,
  patsRefreshed,
}: {
  principals: Principal[];
  groups: GroupNode[];
  selected: string;
  onSelect: (p: string) => void;
  groupsRefreshed?: string;
  patsRefreshed?: string;
}) {
  const [ptype, setPtype] = useState("all");
  const [search, setSearch] = useState("");
  const [privilege, setPrivilege] = useState("all");
  const [via, setVia] = useState("all");

  const filteredPrincipals = useMemo(() => {
    let list = principals;
    if (ptype !== "all") list = list.filter((p) => p.type === ptype);
    if (search) {
      const s = search.toLowerCase();
      list = list.filter((p) => p.principal.toLowerCase().includes(s));
    }
    return list;
  }, [principals, ptype, search]);

  const activePrincipal = selected || filteredPrincipals[0]?.principal || "";
  const { data, loading, error } = useApi(() => fetchPrincipalAccess(activePrincipal), [activePrincipal]);
  const access = data?.data;

  let grants: EffectiveGrant[] = access?.grants ?? [];
  if (privilege !== "all") grants = grants.filter((g) => g.privilege === privilege);
  if (via === "direct") grants = grants.filter((g) => g.granted_via === "direct");
  if (via === "group") grants = grants.filter((g) => g.granted_via === "group");

  const grantCols: Column<EffectiveGrant>[] = [
    {
      key: "sec",
      header: (
        <>
          Securable <InfoTip text="The Unity Catalog object the grant applies to: a catalog, schema, table, volume, or function." />
        </>
      ),
      sortValue: (r) => r.securable,
      render: (r) => <span className="font-mono text-xs">{r.securable}</span>,
    },
    { key: "stype", header: "Type", align: "center", sortValue: (r) => r.securable_type, render: (r) => <SecurableTypePill type={r.securable_type} /> },
    {
      key: "priv",
      header: (
        <>
          Privilege <InfoTip text="A specific right the principal holds on the object, such as SELECT, MODIFY, or USE CATALOG. Write privileges are shown in red." />
        </>
      ),
      align: "center",
      sortValue: (r) => r.privilege,
      render: (r) => <PrivilegePill privilege={r.privilege} />,
    },
    {
      key: "via",
      header: (
        <>
          Held via <InfoTip text="How the principal holds this privilege: granted directly to them, or inherited through a group they belong to." />
        </>
      ),
      align: "center",
      sortValue: (r) => r.granted_via,
      render: (r) => <ViaPill via={r.granted_via} sourceGroup={r.source_group} />,
    },
    {
      key: "inh",
      header: (
        <>
          Scope <InfoTip text="Whether the privilege is set on this exact object, or inherited down from a parent object such as its catalog or schema." />
        </>
      ),
      align: "center",
      sortValue: (r) => (r.inherited ? 1 : 0),
      render: (r) => (
        <span className={`pill ${r.inherited ? "bg-info/15 text-info" : "bg-success/15 text-success"}`}>{r.inherited ? "inherited down" : "on object"}</span>
      ),
    },
  ];

  const privOptions = useMemo(() => {
    const set = new Set((access?.grants ?? []).map((g) => g.privilege));
    return [{ value: "all", label: "All privileges" }, ...[...set].sort().map((p) => ({ value: p, label: p }))];
  }, [access]);

  const groupRoots = useMemo(() => (access ? access.member_of : []), [access]);

  return (
    <div className="flex flex-col gap-4">
      <FilterBar>
        <Dropdown
          label="Type"
          value={ptype}
          onChange={setPtype}
          options={[
            { value: "all", label: "All" },
            { value: "group", label: "Group" },
            { value: "user", label: "User" },
            { value: "service_principal", label: "Service principal" },
          ]}
        />
        <Dropdown
          label="Principal"
          value={activePrincipal}
          onChange={onSelect}
          options={filteredPrincipals.map((p) => ({ value: p.principal, label: p.principal }))}
        />
        <Dropdown label="Privilege" value={privilege} onChange={setPrivilege} options={privOptions} />
        <Dropdown
          label="Held via"
          value={via}
          onChange={setVia}
          options={[
            { value: "all", label: "All" },
            { value: "direct", label: "Direct" },
            { value: "group", label: "Via group" },
          ]}
        />
        <SearchBox value={search} onChange={setSearch} placeholder="Filter principal list…" />
      </FilterBar>

      {loading && <LoadingCard />}
      {error && <ErrorCard message={error} />}

      {access && (
        <>
          <div className="flex items-center gap-3 flex-wrap">
            <span className="font-mono text-sm text-brand-dark">{access.principal}</span>
            <PrincipalTypePill type={access.type} />
            <span className="pill bg-info/15 text-info">{access.source}</span>
            <span className={`pill ${access.status === "active" ? "bg-success/15 text-success" : "bg-border/60 text-neutral"}`}>{access.status}</span>
            <span className="text-xs text-neutral">{access.num_grants} object grants · {access.member_of.length} groups · {access.pats.length} PATs</span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Entitlements */}
            <div className="card">
              <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
                Entitlements
                <InfoTip text="Workspace-level capabilities the principal has, such as workspace access, Databricks SQL access, or permission to create clusters." />
              </div>
              {access.entitlements.length === 0 && <p className="text-xs text-neutral">No entitlements.</p>}
              <div className="flex flex-col gap-1.5">
                {access.entitlements.map((e) => (
                  <div key={e.entitlement} className="flex items-center gap-2">
                    <EntitlementPill>{e.entitlement}</EntitlementPill>
                    <ViaPill via={e.via} sourceGroup={e.source_group} />
                  </div>
                ))}
              </div>
            </div>

            {/* Workspace assignments */}
            <div className="card">
              <div className="text-xs uppercase tracking-wide text-neutral mb-2">Workspace assignments</div>
              {access.workspaces.length === 0 && <p className="text-xs text-neutral">No workspace assignments.</p>}
              <div className="flex flex-col gap-1.5">
                {access.workspaces.map((w) => (
                  <div key={w.workspace} className="flex items-center gap-2 text-sm">
                    <span className="font-mono text-xs">{w.workspace}</span>
                    <span className={`pill ${w.permission === "ADMIN" ? "bg-danger/15 text-danger" : "bg-border/60 text-neutral"}`}>{w.permission}</span>
                    {w.source_group && <span className="text-[11px] text-neutral">via {w.source_group}</span>}
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Group membership (nested, expandable) */}
          {groupRoots.length > 0 && (
            <div className="card">
              <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
                Effective group membership (nested)
                <LastRefreshed at={groupsRefreshed} />
              </div>
              <GroupTree groups={groups} roots={groupRoots} onSelectPrincipal={onSelect} />
            </div>
          )}

          {/* PAT inventory */}
          {access.pats.length > 0 && (
            <div className="card border-l-4 border-l-warning">
              <div className="text-xs uppercase tracking-wide text-neutral mb-2 flex items-center gap-1.5">
                Personal access tokens
                <InfoTip text="Long-lived API credentials (PATs) issued to this principal. Old or unused tokens are a security risk and should be rotated or revoked." />
                <LastRefreshed at={patsRefreshed} />
              </div>
              <div className="flex flex-col gap-1.5">
                {access.pats.map((t) => (
                  <div key={t.id} className="flex items-center gap-2 text-sm">
                    <span className="font-mono text-xs">{t.comment}</span>
                    <span className={`pill ${t.stale ? "bg-warning/15 text-warning" : "bg-border/60 text-neutral"}`}>{t.age_days}d old</span>
                    {t.last_used_days != null && (
                      <span className="text-[11px] text-neutral">last used {t.last_used_days}d ago</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Object grants */}
          <div>
            <div className="flex items-center gap-2 mb-2 text-sm">
              <span className="text-neutral inline-flex items-center gap-1.5">
                Object grants
                <InfoTip text="Privileges this principal holds on securable objects (catalogs, schemas, tables, volumes, functions), whether granted directly or through a group." />
              </span>
              <span className="text-xs text-neutral">{grants.length} of {access.num_grants}</span>
            </div>
            <DataTable columns={grantCols} rows={grants} rowKey={(r) => `${r.securable}-${r.privilege}`} initialSort={{ key: "priv", dir: "desc" }} emptyMessage="No object grants match the filters." />
          </div>
        </>
      )}
    </div>
  );
}
