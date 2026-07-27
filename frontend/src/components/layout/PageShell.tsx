import type { ReactNode } from "react";

export function PageShell({
  title,
  subtitle,
  badge,
  children,
}: {
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="max-w-[1500px] mx-auto px-6 py-6 flex flex-col gap-5">
      <div>
        <h2 className="text-xl font-semibold tracking-tight flex items-center gap-2">
          {title}
          {badge}
        </h2>
        {subtitle && <p className="text-sm text-neutral mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

export function LoadingCard({ label = "Loading…" }: { label?: string }) {
  return <div className="card text-sm text-neutral py-8 text-center">{label}</div>;
}

export function ErrorCard({ message }: { message: string }) {
  return (
    <div className="card border-l-4 border-l-danger text-sm">
      <span className="text-danger font-medium">Error</span>
      <span className="text-neutral ml-2">{message}</span>
    </div>
  );
}
