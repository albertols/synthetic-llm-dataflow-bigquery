import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** A right-skewed density with the points past the q-quantile marked. */
export default function TailDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const curve = Array.from({ length: 57 }, (_, i) => {
    const t = i / 56;
    const d = Math.pow(t * 4, 1.4) * Math.exp(-t * 6);
    return `${16 + i * 5},${80 - d * 120}`;
  }).join(" ");
  const dots = [262, 276, 289, 301];
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A skewed distribution: only n times (1 minus q) sample points lie beyond the q-quantile, so deep tails rest on a
        handful of rows.
      </title>
      <line x1="16" y1="80" x2="312" y2="80" stroke="var(--chart-axis)" strokeWidth="1" />
      <polyline points={curve} fill="none" stroke="var(--chart-1)" strokeWidth="2" />
      <line x1="252" y1="20" x2="252" y2="80" stroke="var(--text-3)" strokeWidth="1" />
      {dots.map((cx) => (
        <circle key={cx} cx={cx} cy="74" r="4" fill="var(--accent)" stroke="var(--surface-2)" strokeWidth="2" />
      ))}
      <text x="200" y="30" fill="var(--text-2)" fontSize="10">
        q-quantile · n(1 − q) points
      </text>
    </svg>
  );
}
