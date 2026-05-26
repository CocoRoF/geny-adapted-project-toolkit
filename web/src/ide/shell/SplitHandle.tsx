import { useCallback, useEffect, useRef } from "react";

import { cn } from "@/ui/cn";

type Axis = "horizontal" | "vertical";

interface Props {
  /** `horizontal` = drag changes width (vertical bar between left/right
   * columns). `vertical` = drag changes height (horizontal bar between
   * top/bottom rows). */
  axis: Axis;
  /** Current size in px. Parent is the source of truth. */
  value: number;
  /** Called with the new size while the user drags. */
  onChange: (next: number) => void;
  /** Minimum size. Default 120. */
  min?: number;
  /** Maximum size. Default 800. */
  max?: number;
  /** When true, dragging *adds* to value; when false, dragging
   * subtracts. Use `invert=true` when the handle sits on the LEFT
   * edge of a right-pinned panel (e.g. the chat rail) so dragging
   * left grows the panel. Default false — fits the common case of a
   * left-pinned panel with the handle on its RIGHT edge. */
  invert?: boolean;
  className?: string;
}

/** Tiny custom split handle. ~50 lines of logic, no dep. The visual
 * is a 4px transparent strip that shows a 1px accent line on hover
 * and during drag — matches the VSCode resize affordance.
 *
 * The drag is plain mouse events tracked on `window` so we never
 * lose pointer mid-drag if the cursor leaves the strip. */
export function SplitHandle({
  axis,
  value,
  onChange,
  min = 120,
  max = 800,
  invert = false,
  className,
}: Props) {
  const draggingRef = useRef(false);
  const originRef = useRef({ pos: 0, value: 0 });

  const onMove = useCallback(
    (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const pos = axis === "horizontal" ? e.clientX : e.clientY;
      const delta = pos - originRef.current.pos;
      const signed = invert ? -delta : delta;
      const next = Math.min(max, Math.max(min, originRef.current.value + signed));
      onChange(next);
    },
    [axis, invert, max, min, onChange],
  );

  const onUp = useCallback(() => {
    draggingRef.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }, []);

  useEffect(() => {
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onMove, onUp]);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    originRef.current = {
      pos: axis === "horizontal" ? e.clientX : e.clientY,
      value,
    };
    document.body.style.cursor = axis === "horizontal" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
  };

  return (
    <div
      role="separator"
      aria-orientation={axis === "horizontal" ? "vertical" : "horizontal"}
      onMouseDown={startDrag}
      className={cn(
        "group relative shrink-0 bg-transparent transition-colors",
        axis === "horizontal" ? "w-px cursor-col-resize" : "h-px cursor-row-resize",
        className,
      )}
    >
      {/* invisible 4px hit area for easier grabbing */}
      <div
        className={cn(
          "absolute z-10",
          axis === "horizontal"
            ? "inset-y-0 left-[-2px] w-[5px]"
            : "inset-x-0 top-[-2px] h-[5px]",
        )}
      />
      <div
        aria-hidden
        className={cn(
          "absolute bg-border-strong opacity-0 transition-opacity group-hover:opacity-100",
          axis === "horizontal" ? "inset-y-0 w-px" : "inset-x-0 h-px",
        )}
      />
    </div>
  );
}
