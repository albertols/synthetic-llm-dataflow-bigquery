/**
 * A 384-d vector as a 16 × 24 grid of cells (dimension d at row ⌊d / 24⌋,
 * column d mod 24), on the diverging scale: negative → `--div-neg`, zero →
 * `--div-mid`, positive → `--div-pos`, scaled to the vector's largest |v|.
 * A hashing vector lights one cell per distinct bucket; a bge vector lights
 * all of them. `highlight` outlines cells (a hovered token's bucket), and
 * hovering a cell reports it. The values themselves are in the table twin.
 */
import { useMemo } from "react";

import { hexToRgb } from "@/lib/color";
import { formatFixed } from "@/lib/format";
import { readToken, useTheme } from "@/lib/theme";

export const GRID_COLS = 24;
export const GRID_ROWS = 16;

function mix(a: readonly number[], b: readonly number[], t: number): string {
  const c = a.map((v, i) => Math.round(v + (b[i]! - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

export function BucketHeatmap({
  vector,
  label,
  highlight,
  onHoverCell,
  cell = 13,
}: {
  vector: ArrayLike<number>;
  /** Accessible name: what the grid shows. */
  label: string;
  highlight?: ReadonlySet<number>;
  onHoverCell?: (dim: number | null) => void;
  cell?: number;
}) {
  const { resolved } = useTheme();
  const scale = useMemo(() => {
    void resolved;
    return {
      neg: hexToRgb(readToken("--div-neg", "#3987e5")),
      mid: hexToRgb(readToken("--div-mid", "#2a2f3a")),
      pos: hexToRgb(readToken("--div-pos", "#d95926")),
    };
  }, [resolved]);
  let max = 0;
  for (let i = 0; i < vector.length; i += 1) max = Math.max(max, Math.abs(vector[i] ?? 0));
  const gap = 1;
  const width = GRID_COLS * cell;
  const height = GRID_ROWS * cell;
  const lit = Array.from(vector).filter((v) => v !== 0).length;
  return (
    <figure className="grid gap-1.5">
      <svg
        role="img"
        aria-label={`${label}: ${lit} of ${vector.length} dimensions non-zero, largest |v| ${formatFixed(max, 3)}`}
        viewBox={`0 0 ${width} ${height}`}
        className="h-auto w-full max-w-[20rem]"
        onPointerLeave={() => onHoverCell?.(null)}
      >
        {Array.from({ length: Math.min(vector.length, GRID_COLS * GRID_ROWS) }, (_, d) => {
          const v = vector[d] ?? 0;
          // sqrt: a single token (1/‖·‖) stays visible next to a bucket that eleven “is” tokens share.
          const t = max > 0 ? Math.sqrt(Math.min(Math.abs(v) / max, 1)) : 0;
          const fill =
            v === 0 ? mix(scale.mid, scale.mid, 0) : mix(scale.mid, v > 0 ? scale.pos : scale.neg, 0.2 + 0.8 * t);
          const x = (d % GRID_COLS) * cell;
          const y = Math.floor(d / GRID_COLS) * cell;
          const on = highlight?.has(d);
          return (
            <rect
              key={d}
              x={x + gap / 2}
              y={y + gap / 2}
              width={cell - gap}
              height={cell - gap}
              rx={2}
              fill={fill}
              stroke={on ? "var(--text-1)" : "none"}
              strokeWidth={on ? 2 : 0}
              onPointerEnter={() => onHoverCell?.(d)}
            />
          );
        })}
      </svg>
      <figcaption className="flex items-center gap-2 text-xs text-text-3">
        <span className="tabular-nums">−{formatFixed(max, 2)}</span>
        <span
          aria-hidden="true"
          className="h-2 w-24 rounded-full"
          style={{
            background: `linear-gradient(90deg, ${mix(scale.mid, scale.neg, 1)}, ${mix(scale.mid, scale.mid, 0)}, ${mix(scale.mid, scale.pos, 1)})`,
          }}
        />
        <span className="tabular-nums">+{formatFixed(max, 2)}</span>
        <span>· 16 × 24 = 384 dims, row-major</span>
      </figcaption>
    </figure>
  );
}
