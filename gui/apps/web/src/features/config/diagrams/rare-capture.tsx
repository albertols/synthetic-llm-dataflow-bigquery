import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** P(seen) = 1 − (1 − p)^n against n for one category share, with the 3/p knee. */
export default function RareCaptureDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const points = Array.from({ length: 41 }, (_, i) => {
    const n = (i / 40) * 6;
    const prob = 1 - Math.exp(-n);
    return `${24 + i * 7},${80 - prob * 64}`;
  }).join(" ");
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        The chance a category of share p appears rises with the sample size n and reaches 95 percent near n = 3/p.
      </title>
      <line x1="24" y1="80" x2="310" y2="80" stroke="var(--chart-axis)" strokeWidth="1" />
      <polyline points={points} fill="none" stroke="var(--chart-1)" strokeWidth="2" />
      <line x1={24 + 20 * 7} y1="16" x2={24 + 20 * 7} y2="80" stroke="var(--text-3)" strokeWidth="1" />
      <text x={24 + 20 * 7 + 4} y="28" fill="var(--text-2)" fontSize="10">
        n ≈ 3/p → 95 %
      </text>
      <text x="24" y="93" fill="var(--text-3)" fontSize="10">
        sample rows n
      </text>
    </svg>
  );
}
