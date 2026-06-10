import { useCallback, useEffect, useRef, useState } from "react";

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
  /** Double-click resets to this size (VS Code's sash double-click
   * restores the default layout). Omit to disable. */
  resetTo?: number;
  className?: string;
}

/** Tiny custom split handle. No dep. VS Code-style sash:
 *
 * - 9px invisible grab zone (VS Code uses 8) so the user doesn't
 *   need pixel-perfect aim on the 1px visual line.
 * - The accent line lights up on hover AND stays lit while dragging
 *   even when the cursor drifts off the strip — `dragging` state,
 *   not just `:hover`.
 * - Double-click resets to `resetTo` when provided.
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
  resetTo,
  className,
}: Props) {
  const draggingRef = useRef(false);
  const originRef = useRef({ pos: 0, value: 0 });
  // Mirror of draggingRef that triggers re-render so the accent line
  // stays visible for the whole drag, not just while hovered.
  const [dragging, setDragging] = useState(false);

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
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
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
    setDragging(true);
    originRef.current = {
      pos: axis === "horizontal" ? e.clientX : e.clientY,
      value,
    };
    document.body.style.cursor = axis === "horizontal" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
  };

  const onDoubleClick = () => {
    if (resetTo !== undefined) onChange(Math.min(max, Math.max(min, resetTo)));
  };

  return (
    <div
      role="separator"
      aria-orientation={axis === "horizontal" ? "vertical" : "horizontal"}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      onMouseDown={startDrag}
      onDoubleClick={onDoubleClick}
      className={cn(
        "group relative shrink-0 bg-transparent",
        axis === "horizontal" ? "w-px cursor-col-resize" : "h-px cursor-row-resize",
        className,
      )}
    >
      {/* invisible 9px hit area for easier grabbing (VS Code sash ≈8px) */}
      <div
        className={cn(
          "absolute z-10",
          axis === "horizontal"
            ? "inset-y-0 left-[-4px] w-[9px] cursor-col-resize"
            : "inset-x-0 top-[-4px] h-[9px] cursor-row-resize",
        )}
      />
      {/* visual line — 1px idle (border tone), thickens to 3px accent
          on hover / during drag, exactly like VS Code's sash. */}
      <div
        aria-hidden
        className={cn(
          "absolute transition-all duration-100",
          axis === "horizontal" ? "inset-y-0" : "inset-x-0",
          dragging
            ? cn(
                "bg-accent opacity-100",
                axis === "horizontal" ? "w-[3px] left-[-1px]" : "h-[3px] top-[-1px]",
              )
            : cn(
                "bg-border-strong opacity-0 group-hover:opacity-100 group-hover:bg-accent",
                axis === "horizontal"
                  ? "w-px group-hover:w-[3px] group-hover:left-[-1px]"
                  : "h-px group-hover:h-[3px] group-hover:top-[-1px]",
              ),
        )}
      />
    </div>
  );
}
