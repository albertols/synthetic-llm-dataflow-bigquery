import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Rows grow with the table; model calls stay flat (a bounded pool per free-text column). A schematic, not data. */
export default function O1CallsDiagram({ className }: DiagramProps) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const x0 = 36;
  const x1 = 300;
  const y0 = 100;
  const top = 18;
  return (
    <svg viewBox="0 0 320 124" role="img" aria-labelledby={`${id}-t`} className={className} width="100%">
      <title id={`${id}-t`}>
        Schematic: as the table grows, CPU sampling work grows with the rows, while the number of model calls stays
        flat, bounded by the free-text columns and their pool cap.
      </title>
      <line x1={x0} y1={y0} x2={x1} y2={y0} stroke="var(--chart-axis)" />
      <line x1={x0} y1={y0} x2={x0} y2={top - 6} stroke="var(--chart-axis)" />
      <text x={x1} y={y0 + 16} fill="var(--text-3)" fontSize="10" textAnchor="end">
        rows generated →
      </text>
      <text
        x={x0 - 6}
        y={top}
        fill="var(--text-3)"
        fontSize="10"
        textAnchor="end"
        transform={`rotate(-90 ${x0 - 22} ${top + 30})`}
      >
        work
      </text>
      {/* CPU work: grows with rows. */}
      <line x1={x0} y1={y0 - 4} x2={x1 - 8} y2={top + 4} stroke="var(--cpu)" strokeWidth="2.5" strokeLinecap="round" />
      <text x={x1 - 12} y={top + 18} fill="var(--cpu-text)" fontSize="11" fontWeight="600" textAnchor="end">
        CPU sampling: O(rows)
      </text>
      {/* LLM calls: flat. */}
      <line
        x1={x0}
        y1={y0 - 22}
        x2={x1 - 8}
        y2={y0 - 22}
        stroke="var(--gpu)"
        strokeWidth="2.5"
        strokeDasharray="6 4"
        strokeLinecap="round"
      />
      <text x={x1 - 12} y={y0 - 28} fill="var(--gpu-text)" fontSize="11" fontWeight="600" textAnchor="end">
        LLM calls: O(1) per free-text column
      </text>
    </svg>
  );
}
