import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface InfoTipProps {
  /** Plain-language explanation of the metric / term. */
  text: string;
  /** Optional accessible label for the trigger. Defaults to "What is this?". */
  label?: string;
  /** Visual size of the trigger glyph. */
  size?: "sm" | "md";
}

/**
 * A small "?" affordance that reveals a plain-language definition of a metric.
 * Opens on hover, keyboard focus, or tap; closes on blur, mouse-leave, or Escape.
 *
 * The bubble is rendered in a PORTAL to <body> with FIXED positioning computed
 * from the trigger's viewport rect — so it is never clipped by a table's
 * overflow-x-auto/scroll container (the previous absolute-in-wrapper bubble got
 * trimmed at table edges, esp. for the last columns). It flips left/above to
 * stay on-screen.
 */
export function InfoTip({ text, label = "What is this?", size = "sm" }: InfoTipProps) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ left: number; top: number; alignRight: boolean; above: boolean }>({
    left: 0, top: 0, alignRight: false, above: false,
  });
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const tipId = useId();

  const place = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const alignRight = r.left > window.innerWidth * 0.62;
    const above = window.innerHeight - r.bottom < 140;
    setCoords({
      // Anchor x: bubble's left edge (or right edge when flipping left).
      left: alignRight ? r.right : r.left,
      top: above ? r.top : r.bottom,
      alignRight,
      above,
    });
  }, []);

  const show = useCallback(() => {
    place();
    setOpen(true);
  }, [place]);
  const hide = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };
    // Close on scroll/resize — a fixed bubble would otherwise detach from its
    // trigger once the table or page scrolls.
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [open, hide]);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  const glyph = size === "md" ? "h-4 w-4 text-[11px]" : "h-3.5 w-3.5 text-[10px]";

  return (
    <span ref={wrapRef} className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? tipId : undefined}
        className={`${glyph} inline-flex items-center justify-center rounded-full border border-border bg-transparent font-semibold leading-none text-neutral transition-colors hover:border-accent hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60`}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={(e) => {
          e.stopPropagation();
          open ? hide() : show();
        }}
      >
        ?
      </button>
      {open &&
        createPortal(
          <span
            id={tipId}
            role="tooltip"
            style={{
              position: "fixed",
              // Fixed = viewport-relative, so it escapes any overflow container.
              left: coords.alignRight ? undefined : coords.left,
              right: coords.alignRight ? window.innerWidth - coords.left : undefined,
              top: coords.above ? undefined : coords.top + 6,
              bottom: coords.above ? window.innerHeight - coords.top + 6 : undefined,
            }}
            className="z-[100] w-max max-w-[260px] rounded-lg border border-border bg-card px-3 py-2 text-left text-xs font-normal normal-case leading-snug tracking-normal text-brand-dark shadow-card"
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  );
}
