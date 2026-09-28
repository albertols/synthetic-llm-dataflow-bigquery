import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** HyperLogLog++: hash → registers keeping the max leading-zero run → harmonic mean. */
export default function HllDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const boxes = [
    { x: 8, label: "values" },
    { x: 88, label: "64-bit hash" },
    { x: 168, label: "2^p registers" },
    { x: 248, label: "≈ distinct" },
  ];
  return (
    <svg viewBox="0 0 320 64" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        HyperLogLog plus plus: every value is hashed, each register keeps its longest run of leading zeros, and a
        bias-corrected harmonic mean of the registers estimates the distinct count.
      </title>
      {boxes.map((box, i) => (
        <g key={box.label}>
          <rect
            x={box.x}
            y="18"
            width="64"
            height="28"
            rx="6"
            fill="var(--surface-3)"
            stroke={i === 3 ? "var(--accent)" : "var(--border-strong)"}
          />
          <text x={box.x + 32} y="36" fill="var(--text-1)" fontSize="9.5" textAnchor="middle">
            {box.label}
          </text>
          {i < 3 ? (
            <line x1={box.x + 66} y1="32" x2={box.x + 78} y2="32" stroke="var(--text-3)" strokeWidth="1.5" />
          ) : null}
        </g>
      ))}
    </svg>
  );
}
