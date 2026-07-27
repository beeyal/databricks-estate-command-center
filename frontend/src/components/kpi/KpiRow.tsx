import type { ReactNode } from "react";

export function KpiRow({ children, cols = 6 }: { children: ReactNode; cols?: number }) {
  const colClass =
    cols === 7
      ? "grid-cols-2 md:grid-cols-4 lg:grid-cols-7"
      : cols === 6
        ? "grid-cols-2 md:grid-cols-3 lg:grid-cols-6"
        : cols === 5
          ? "grid-cols-2 md:grid-cols-3 lg:grid-cols-5"
          : cols === 4
            ? "grid-cols-2 lg:grid-cols-4"
            : "grid-cols-2 md:grid-cols-3";
  return <div className={`grid ${colClass} gap-4`}>{children}</div>;
}
