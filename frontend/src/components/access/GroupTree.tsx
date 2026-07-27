import { useState } from "react";
import type { GroupNode } from "../../types";
import { PrincipalTypePill } from "./AccessPills";

// Expandable, recursive group-membership tree. Nested groups (a group listed
// as a member of another group) can be drilled into so effective access
// resolves through the hierarchy. Guards against cycles via a visited set.
export function GroupTree({
  groups,
  roots,
  onSelectPrincipal,
}: {
  groups: GroupNode[];
  roots?: string[]; // top-level groups to render; defaults to all
  onSelectPrincipal?: (principal: string) => void;
}) {
  const byName = new Map(groups.map((g) => [g.group, g]));
  const rootNames = roots ?? groups.map((g) => g.group);
  return (
    <div className="flex flex-col gap-1">
      {rootNames.map((name) => (
        <GroupBranch key={name} name={name} byName={byName} depth={0} visited={new Set()} onSelectPrincipal={onSelectPrincipal} />
      ))}
    </div>
  );
}

function GroupBranch({
  name,
  byName,
  depth,
  visited,
  onSelectPrincipal,
}: {
  name: string;
  byName: Map<string, GroupNode>;
  depth: number;
  visited: Set<string>;
  onSelectPrincipal?: (principal: string) => void;
}) {
  const [open, setOpen] = useState(depth === 0 && byName.size <= 2);
  const node = byName.get(name);
  const cycle = visited.has(name);

  if (!node) {
    return null;
  }

  const childGroups = node.direct_members.filter((m) => m.type === "group");
  const childLeaves = node.direct_members.filter((m) => m.type !== "group");
  const hasChildren = node.direct_members.length > 0 && !cycle;

  return (
    <div style={{ marginLeft: depth ? 14 : 0 }} className={depth ? "border-l border-border/60 pl-3" : ""}>
      <div className="flex items-center gap-2 py-1">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          disabled={!hasChildren}
          className={`text-xs w-4 text-neutral ${hasChildren ? "cursor-pointer hover:text-brand-dark" : "opacity-30"}`}
          aria-label={open ? "collapse" : "expand"}
        >
          {hasChildren ? (open ? "▾" : "▸") : "•"}
        </button>
        <button
          type="button"
          onClick={() => onSelectPrincipal?.(name)}
          className="text-sm font-medium text-brand-dark hover:text-accent"
        >
          {name}
        </button>
        <span className="pill bg-info/15 text-info">group</span>
        <span className="text-xs text-neutral">{node.member_count} members</span>
        {cycle && <span className="pill bg-warning/15 text-warning">cycle</span>}
      </div>
      {open && hasChildren && (
        <div className="flex flex-col">
          {childGroups.map((m) => (
            <GroupBranch
              key={m.principal}
              name={m.principal}
              byName={byName}
              depth={depth + 1}
              visited={new Set([...visited, name])}
              onSelectPrincipal={onSelectPrincipal}
            />
          ))}
          {childLeaves.map((m) => (
            <div key={m.principal} style={{ marginLeft: 14 }} className="border-l border-border/60 pl-3 flex items-center gap-2 py-0.5">
              <span className="text-neutral text-xs w-4">•</span>
              <button
                type="button"
                onClick={() => onSelectPrincipal?.(m.principal)}
                className="text-xs font-mono text-neutral hover:text-accent"
              >
                {m.principal}
              </button>
              <PrincipalTypePill type={m.type} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
