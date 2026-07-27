import { useEffect, useRef, useState } from "react";

// Small "Copy" button with a transient "Copied ✓" confirmation, so the copy
// action gives visible feedback (was a silent navigator.clipboard call). Falls
// back to a document.execCommand copy on insecure origins where the async
// Clipboard API is unavailable, and shows "Copy failed" if neither works.
export function CopyButton({
  text,
  label = "Copy SQL",
  title = "Copy to clipboard",
  className = "",
}: {
  text: string;
  label?: string;
  title?: string;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "ok" | "err">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  async function copy() {
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        ok = true;
      } else {
        // Insecure-origin fallback (no async Clipboard API available).
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      }
    } catch {
      ok = false;
    }
    setState(ok ? "ok" : "err");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={title}
      aria-live="polite"
      className={`pill text-[11px] px-2.5 py-0.5 border transition ${
        state === "ok"
          ? "border-success/40 text-success"
          : state === "err"
            ? "border-danger/40 text-danger"
            : "border-border text-neutral hover:text-brand-dark hover:bg-surface"
      } ${className}`}
    >
      {state === "ok" ? "Copied ✓" : state === "err" ? "Copy failed" : label}
    </button>
  );
}
