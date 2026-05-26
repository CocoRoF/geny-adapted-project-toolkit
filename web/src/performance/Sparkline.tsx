import { useMemo } from "react";

interface Props {
  /** Most-recent-last array of values. Empty array renders a flat
   * line at the bottom. */
  values: number[];
  /** Optional explicit ceiling — useful for memory where we want
   * the line scaled against the cgroup limit instead of the local
   * series max. When null we auto-scale to max(values). */
  max?: number | null;
  width?: number;
  height?: number;
  /** CSS color (use a CSS variable or hex). Default uses the accent
   * color CSS var. */
  stroke?: string;
  className?: string;
}

/** Tiny inline SVG sparkline. ~30 lines, no charting lib. Renders a
 * polyline scaled to the prop max (or the series max). Series with
 * <2 points render a flat baseline so the row layout stays stable. */
export function Sparkline({
  values,
  max,
  width = 96,
  height = 28,
  stroke = "var(--color-accent)",
  className,
}: Props) {
  const path = useMemo(() => {
    if (values.length < 2) return null;
    const ceiling = max ?? Math.max(...values, 1);
    const safeCeil = ceiling > 0 ? ceiling : 1;
    const stepX = width / (values.length - 1);
    const pts: string[] = [];
    for (let i = 0; i < values.length; i++) {
      const x = i * stepX;
      const norm = Math.min(values[i] / safeCeil, 1);
      const y = height - norm * (height - 2) - 1;
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return pts.join(" ");
  }, [values, max, width, height]);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden="true"
    >
      <line
        x1={0}
        y1={height - 1}
        x2={width}
        y2={height - 1}
        stroke="var(--color-border)"
        strokeWidth={1}
      />
      {path ? (
        <polyline
          fill="none"
          stroke={stroke}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          points={path}
        />
      ) : null}
    </svg>
  );
}
