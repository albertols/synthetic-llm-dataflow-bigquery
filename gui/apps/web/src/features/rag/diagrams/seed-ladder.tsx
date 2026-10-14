import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const RUNGS = ["1 · value vectors", "2 · first 1,024 rows", "3 · row exemplars", "4 · observed examples"];

/** The fallback order, top to bottom; the pool branch lands on rung 2. */
export default function SeedLadderDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 100" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Four rungs tried in order: persisted value vectors, the column's values in the first 1,024 rows, row exemplars,
        observed examples. The pool branch has no chunk store, so it lands on rung 2; rung 3 cannot fire as wired.
      </title>
      {RUNGS.map((label, i) => (
        <g key={label}>
          <rect
            x="8"
            y={4 + i * 24}
            width="200"
            height="20"
            rx="4"
            fill={i === 1 ? "var(--accent-soft)" : "var(--surface-3)"}
            stroke={i === 1 ? "var(--accent)" : "var(--border-strong)"}
            strokeDasharray={i === 2 ? "3 3" : undefined}
          />
          <text x="16" y={18 + i * 24} fontSize="10" fill="var(--text-1)">
            {label}
          </text>
        </g>
      ))}
      <text x="216" y="42" fontSize="10" fill="var(--accent-text)">
        ← pool branch
      </text>
      <text x="216" y="66" fontSize="10" fill="var(--text-3)">
        cannot fire
      </text>
    </svg>
  );
}
