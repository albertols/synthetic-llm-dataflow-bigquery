import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Row null bitstrings: B and C null together (the truth) vs independent draws (ghost patterns). */
export default function NullPatternsDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const truth = ["000", "011", "000", "011"];
  const model = ["001", "010", "011", "000"];
  const grid = (rows: string[], x0: number) =>
    rows.map((bits, r) =>
      [...bits].map((b, c) => (
        <rect
          key={`${r}-${c}`}
          x={x0 + c * 20}
          y={12 + r * 16}
          width="16"
          height="12"
          rx="2"
          fill={b === "1" ? "var(--chart-2)" : "var(--surface-3)"}
          stroke="var(--border-strong)"
        />
      )),
    );
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Rows of null flags for columns A, B and C: in the source B and C are null together; drawing each column's null
        rate independently invents rows where only one of them is null.
      </title>
      {grid(truth, 40)}
      {grid(model, 200)}
      <text x="40" y="86" fill="var(--text-2)" fontSize="10">
        source: B, C null together
      </text>
      <text x="200" y="86" fill="var(--text-2)" fontSize="10">
        independent: ghost rows
      </text>
    </svg>
  );
}
