import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** exact: one barrier · exact_chained: three · streaming: none (duplicates land, measured). */
export default function UniquenessDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const rows = [
    { label: "exact", barriers: 1 },
    { label: "chained", barriers: 3 },
    { label: "streaming", barriers: 0 },
  ];
  return (
    <svg viewBox="0 0 320 84" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Three modes: exact crosses one shuffle barrier, exact_chained crosses three, streaming crosses none and lands
        duplicates while measuring them.
      </title>
      {rows.map((row, r) => (
        <g key={row.label}>
          <text x="4" y={20 + r * 26} fill="var(--text-2)" fontSize="10">
            {row.label}
          </text>
          <line x1="70" y1={16 + r * 26} x2="312" y2={16 + r * 26} stroke="var(--text-3)" strokeWidth="1.5" />
          {Array.from({ length: row.barriers }, (_, b) => (
            <rect key={b} x={110 + b * 60} y={8 + r * 26} width="10" height="16" rx="2" fill="var(--accent)" />
          ))}
          {row.barriers === 0 ? (
            <text x="160" y={12 + r * 26} fill="var(--text-3)" fontSize="9">
              no barrier · duplicates land
            </text>
          ) : null}
        </g>
      ))}
    </svg>
  );
}
