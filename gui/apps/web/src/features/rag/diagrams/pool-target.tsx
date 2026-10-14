import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const BARS: [string, number][] = [
  ["num_rows", 280],
  ["distinct", 150],
  ["cap 512", 190],
];

/** The target is the shortest of three bounds. */
export default function PoolTargetDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Three bounds — rows to generate, the column's distinct count and the 512 cap — and the target is the smallest of
        them, here the distinct count.
      </title>
      {BARS.map(([label, width], i) => (
        <g key={label}>
          <text x="8" y={20 + i * 24} fontSize="10" fill="var(--text-2)">
            {label}
          </text>
          <rect
            x="70"
            y={10 + i * 24}
            width={width * 0.8}
            height="14"
            rx="3"
            fill={i === 1 ? "var(--accent)" : "var(--chart-other)"}
          />
        </g>
      ))}
      <line x1={70 + 150 * 0.8} y1="4" x2={70 + 150 * 0.8} y2="80" stroke="var(--text-1)" strokeWidth="1.5" />
      <text x={74 + 150 * 0.8} y="90" fontSize="10" fill="var(--text-1)">
        target = min
      </text>
    </svg>
  );
}
