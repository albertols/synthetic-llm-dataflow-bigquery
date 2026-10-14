import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Collision probability against M for a fixed keyspace: it jumps near M ≈ √(2K). */
export default function BirthdayDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const points = Array.from({ length: 41 }, (_, i) => {
    const m = (i / 40) * 3;
    const prob = 1 - Math.exp(-(m * m) / 2);
    return `${24 + i * 7},${80 - prob * 64}`;
  }).join(" ");
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        The probability that M identifiers from K values contain a repeat stays near zero, then rises steeply once M
        approaches the square root of 2K.
      </title>
      <line x1="24" y1="80" x2="310" y2="80" stroke="var(--chart-axis)" strokeWidth="1" />
      <polyline points={points} fill="none" stroke="var(--chart-1)" strokeWidth="2" />
      <text x="150" y="30" fill="var(--text-2)" fontSize="10">
        M ≈ √(2K): a coin flip
      </text>
      <text x="24" y="93" fill="var(--text-3)" fontSize="10">
        identifiers M
      </text>
    </svg>
  );
}
