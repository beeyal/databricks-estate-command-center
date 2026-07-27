export type AccessMode = "data" | "principal";

export function ModeToggle({ mode, onChange }: { mode: AccessMode; onChange: (m: AccessMode) => void }) {
  const opts: { id: AccessMode; label: string; hint: string }[] = [
    { id: "data", label: "Who can access this?", hint: "Data-centric" },
    { id: "principal", label: "What can this principal access?", hint: "Principal-centric" },
  ];
  return (
    <div className="inline-flex rounded-lg border border-border overflow-hidden self-start">
      {opts.map((o) => {
        const active = mode === o.id;
        return (
          <button
            key={o.id}
            type="button"
            onClick={() => onChange(o.id)}
            aria-pressed={active}
            className={`px-4 py-2 text-sm font-medium transition flex flex-col items-start leading-tight ${
              active ? "bg-accent text-white" : "text-neutral hover:text-brand-dark hover:bg-surface"
            }`}
          >
            <span>{o.label}</span>
            <span className={`text-[10px] uppercase tracking-wide ${active ? "text-white/70" : "text-neutral/60"}`}>{o.hint}</span>
          </button>
        );
      })}
    </div>
  );
}
