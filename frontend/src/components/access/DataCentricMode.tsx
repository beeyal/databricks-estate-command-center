import { useMemo, useState } from "react";
import { fetchSecurableAccess } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { LoadingCard, ErrorCard } from "../layout/PageShell";
import { DataTable } from "../shared/DataTable";
import type { Column } from "../shared/DataTable";
import { FilterBar, Dropdown, SearchBox } from "../shared/FilterBar";
import type { AccessTree, SecurableGrant } from "../../types";
import { PrivilegePill, InheritedPill, PrincipalTypePill, PolicyPill, SecurableTypePill } from "./AccessPills";
import { InfoTip } from "../shared/InfoTip";

// "Who can access this?" — cascading workspace → catalog → schema → table,
// then the full grant list on the chosen securable plus any row filter /
// column mask / ABAC policy in effect.
export function DataCentricMode({
  tree,
  onSelectPrincipal,
}: {
  tree: AccessTree;
  onSelectPrincipal: (p: string) => void;
}) {
  const [catalog, setCatalog] = useState<string>(Object.keys(tree.catalogs)[0] ?? "all");
  const [schema, setSchema] = useState("all");
  const [table, setTable] = useState("all");
  const [privilege, setPrivilege] = useState("all");
  const [via, setVia] = useState("all");
  const [search, setSearch] = useState("");

  const schemas = useMemo(() => Object.keys(tree.catalogs[catalog] ?? {}), [tree, catalog]);
  const tables = useMemo(() => {
    if (schema === "all") {
      return Object.values(tree.catalogs[catalog] ?? {}).flat();
    }
    return (tree.catalogs[catalog]?.[schema] ?? []);
  }, [tree, catalog, schema]);

  // The securable we resolve grants for: most specific selection wins.
  const securable = table !== "all" ? table : schema !== "all" ? `${catalog}.${schema}` : catalog;

  const { data, loading, error } = useApi(() => fetchSecurableAccess(securable), [securable]);
  const detail = data?.data;

  let rows: SecurableGrant[] = detail?.grants ?? [];
  if (privilege !== "all") rows = rows.filter((r) => r.privilege === privilege);
  if (via === "inherited") rows = rows.filter((r) => r.inherited);
  if (via === "direct") rows = rows.filter((r) => !r.inherited);
  if (via === "via-group") rows = rows.filter((r) => r.granted_via === "group");
  if (search) {
    const s = search.toLowerCase();
    rows = rows.filter((r) => r.principal.toLowerCase().includes(s));
  }

  const columns: Column<SecurableGrant>[] = [
    {
      key: "principal",
      header: (
        <>
          Principal <InfoTip text="The identity a grant is assigned to: a user, group, or service principal. Click one to see everything it can access." />
        </>
      ),
      sortValue: (r) => r.principal,
      render: (r) => (
        <button type="button" onClick={() => onSelectPrincipal(r.principal)} className="font-mono text-xs text-brand-dark hover:text-accent text-left">
          {r.principal}
        </button>
      ),
    },
    { key: "ptype", header: "Type", align: "center", sortValue: (r) => r.principal_type, render: (r) => <PrincipalTypePill type={r.principal_type} /> },
    {
      key: "priv",
      header: (
        <>
          Privilege <InfoTip text="A specific right this principal holds on the object, such as SELECT, MODIFY, or USE CATALOG. Write privileges are shown in red." />
        </>
      ),
      align: "center",
      sortValue: (r) => r.privilege,
      render: (r) => <PrivilegePill privilege={r.privilege} />,
    },
    {
      key: "inh",
      header: "Direct / inherited",
      align: "center",
      sortValue: (r) => (r.inherited ? 1 : 0),
      render: (r) => <InheritedPill inherited={r.inherited} from={r.inherited_from} />,
    },
    {
      key: "via",
      header: (
        <>
          Granted <InfoTip text="How the grant reaches the principal: assigned directly to them, or held through a group they belong to." />
        </>
      ),
      align: "center",
      sortValue: (r) => r.granted_via,
      render: (r) => (
        <span className={`pill ${r.granted_via === "direct" ? "bg-warning/15 text-warning" : "bg-border/60 text-neutral"}`}>
          {r.granted_via === "direct" ? "to principal" : "via group"}
        </span>
      ),
    },
    {
      key: "from",
      header: "Inherited from",
      sortValue: (r) => r.inherited_from ?? "",
      render: (r) => (r.inherited_from ? <span className="font-mono text-xs text-neutral">{r.inherited_from}</span> : <span className="text-neutral">—</span>),
    },
  ];

  const privOptions = useMemo(() => {
    const set = new Set((detail?.grants ?? []).map((g) => g.privilege));
    return [{ value: "all", label: "All privileges" }, ...[...set].sort().map((p) => ({ value: p, label: p }))];
  }, [detail]);

  return (
    <div className="flex flex-col gap-4">
      <FilterBar>
        {/* No Workspace filter here: UC grants are metastore-scoped (a securable's
            grants aren't per-workspace), so a workspace control couldn't filter
            this view — it was a dead control showing raw ids. Removed. */}
        <Dropdown
          label="Catalog"
          value={catalog}
          onChange={(v) => {
            setCatalog(v);
            setSchema("all");
            setTable("all");
          }}
          options={Object.keys(tree.catalogs).map((c) => ({ value: c, label: c }))}
        />
        <Dropdown
          label="Schema"
          value={schema}
          onChange={(v) => {
            setSchema(v);
            setTable("all");
          }}
          options={[{ value: "all", label: "All schemas" }, ...schemas.map((s) => ({ value: s, label: s }))]}
        />
        <Dropdown
          label="Table"
          value={table}
          onChange={setTable}
          options={[{ value: "all", label: "All tables" }, ...tables.map((t) => ({ value: t, label: t.split(".").slice(-1)[0] }))]}
        />
        <Dropdown label="Privilege" value={privilege} onChange={setPrivilege} options={privOptions} />
        <Dropdown
          label="Grant"
          value={via}
          onChange={setVia}
          options={[
            { value: "all", label: "All" },
            { value: "direct", label: "Direct only" },
            { value: "inherited", label: "Inherited only" },
            { value: "via-group", label: "Via group" },
          ]}
        />
        <SearchBox value={search} onChange={setSearch} placeholder="Search principals…" />
      </FilterBar>

      <div className="flex items-center gap-2 text-sm">
        <span className="text-neutral inline-flex items-center gap-1">
          Grants on
          <InfoTip text="A grant is a privilege assigned on a securable object (a catalog, schema, table, volume, or function). This lists every grant on the selected object." />
        </span>
        <span className="font-mono text-brand-dark">{securable}</span>
        {detail && <SecurableTypePill type={detail.securable_type} />}
        {detail && <span className="text-xs text-neutral">{rows.length} of {detail.num_grants} grants</span>}
      </div>

      {loading && <LoadingCard />}
      {error && <ErrorCard message={error} />}

      {detail && detail.policies.length > 0 && (
        <div className="card border-l-4 border-l-warning">
          <div className="text-xs uppercase tracking-wide text-neutral mb-2">Policies in effect</div>
          <div className="flex flex-col gap-2">
            {detail.policies.map((p) => (
              <div key={p.name} className="flex items-start gap-2 text-sm">
                <PolicyPill kind={p.kind} />
                <div>
                  <span className="font-mono text-xs text-brand-dark">{p.name}</span>
                  <p className="text-xs text-neutral">{p.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {detail && (
        <DataTable columns={columns} rows={rows} rowKey={(r) => `${r.id}-${r.privilege}`} initialSort={{ key: "priv", dir: "desc" }} emptyMessage="No grants on this securable match the filters." />
      )}
    </div>
  );
}
