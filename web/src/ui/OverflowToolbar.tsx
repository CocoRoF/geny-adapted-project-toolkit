import {
  Children,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { MoreHorizontal } from "lucide-react";

import { cn } from "@/ui/cn";

/** Gap between toolbar items — must match the container's `gap-2`. */
const GAP_PX = 8;
/** Reserved width for the `…` trigger (button + leading gap). */
const MORE_BTN_PX = 40;

interface Props {
  children: ReactNode;
  className?: string;
  /** aria-label / tooltip for the overflow trigger. */
  moreLabel?: string;
}

/** Measurement-driven "priority+" toolbar.
 *
 * Renders children left-to-right; whatever doesn't fit the available
 * width collapses — in DOM order, tail first — into a `…` popover so
 * controls are NEVER clipped into unusability (the old chat header
 * simply cut off the right-hand pills when the panel was narrow).
 *
 * How it measures without a second hidden render pass: every child
 * gets a wrapper span; widths of *rendered* wrappers are cached in a
 * ref, so when an item moves into the popover its last-known width
 * still participates in the fit calculation (lets the row re-expand
 * when the container grows again). A ResizeObserver on the container
 * AND each visible wrapper catches both panel resizes and intrinsic
 * label changes (e.g. the live cost counter getting wider).
 *
 * Children keep their own identity/state semantics: items are plain
 * elements, controlled dropdown pills work unchanged inside the
 * popover (their absolute menus overlay it).
 */
export function OverflowToolbar({ children, className, moreLabel = "more" }: Props) {
  const items = Children.toArray(children).filter(Boolean);
  const count = items.length;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const itemEls = useRef<(HTMLSpanElement | null)[]>([]);
  const widthCache = useRef<number[]>([]);
  const [visibleCount, setVisibleCount] = useState(count);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);

  const recompute = useCallback(() => {
    const el = containerRef.current;
    if (el === null) return;
    for (let i = 0; i < count; i++) {
      const node = itemEls.current[i];
      if (node) widthCache.current[i] = node.offsetWidth;
    }
    const width = (i: number) => widthCache.current[i] ?? 0;
    const avail = el.clientWidth;
    // Unmeasurable container (display:none ancestor, jsdom, first
    // SSR paint) → don't collapse anything; a wrong `…` is worse
    // than a brief unclipped row, and the observer re-runs once real
    // dimensions exist.
    if (avail <= 0) {
      setVisibleCount(count);
      return;
    }
    let total = 0;
    for (let i = 0; i < count; i++) total += width(i) + (i > 0 ? GAP_PX : 0);
    if (total <= avail) {
      setVisibleCount(count);
      return;
    }
    let used = MORE_BTN_PX;
    let fit = 0;
    for (let i = 0; i < count; i++) {
      const need = width(i) + (fit > 0 ? GAP_PX : 0);
      if (used + need > avail) break;
      used += need;
      fit += 1;
    }
    setVisibleCount(fit);
  }, [count]);

  // Runs after every commit — cheap (a handful of offsetWidth reads)
  // and converges: setVisibleCount with an unchanged value is a no-op.
  useLayoutEffect(recompute);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (el === null) return;
    const ro = new ResizeObserver(() => recompute());
    ro.observe(el);
    for (const node of itemEls.current) {
      if (node) ro.observe(node);
    }
    return () => ro.disconnect();
  }, [recompute, visibleCount]);

  // Close the popover on outside clicks. Nested pill menus live
  // inside the wrapper subtree, so interacting with them never
  // counts as "outside".
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const wrap = menuWrapRef.current;
      if (wrap && e.target instanceof Node && !wrap.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  // All items fit again → stale-open menu would render empty.
  useEffect(() => {
    if (visibleCount >= count && menuOpen) setMenuOpen(false);
  }, [count, menuOpen, visibleCount]);

  const hidden = items.slice(visibleCount);

  return (
    <div
      ref={containerRef}
      className={cn(
        "flex min-w-0 flex-1 items-center gap-2 overflow-hidden",
        className,
      )}
    >
      {items.slice(0, visibleCount).map((child, i) => (
        <span
          // Index keys are safe here: the list is positional by
          // design (priority = DOM order) and wrappers carry no state.
          key={i}
          ref={(node) => {
            itemEls.current[i] = node;
          }}
          className="flex shrink-0 items-center"
        >
          {child}
        </span>
      ))}
      {hidden.length > 0 ? (
        <div ref={menuWrapRef} className="relative ml-auto shrink-0">
          <button
            type="button"
            aria-label={moreLabel}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title={moreLabel}
            onClick={() => setMenuOpen((v) => !v)}
            className={cn(
              "inline-flex h-6 items-center gap-1 rounded-md border border-border px-1.5 text-fg-muted",
              menuOpen
                ? "bg-bg-subtle text-fg"
                : "bg-bg-elevated hover:bg-bg-subtle hover:text-fg",
            )}
          >
            <MoreHorizontal className="h-3.5 w-3.5" strokeWidth={1.5} />
            <span className="text-[10px] tabular-nums">{hidden.length}</span>
          </button>
          {menuOpen ? (
            <div
              role="menu"
              className="absolute right-0 top-full z-50 mt-1 flex min-w-[200px] max-w-[min(420px,80vw)] flex-wrap items-center gap-2 rounded-md border border-border bg-bg-elevated p-2 shadow-xl"
            >
              {hidden.map((child, i) => (
                <span key={i} className="flex items-center">
                  {child}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
