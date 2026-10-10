import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Two binned CDFs: the gap at an edge is the lower bound, the gap one bin can hide the upper bound. */
export default function KsBracketDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const source = "M20 80 L80 80 L80 62 L140 62 L140 40 L200 40 L200 24 L300 24";
  const synthetic = "M20 80 L80 80 L80 70 L140 70 L140 50 L200 50 L200 24 L300 24";
  return (
    <svg viewBox="0 0 320 100" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Two step CDFs over the same bin edges. The largest vertical gap at an edge is the lower bound of the KS
        distance; inside a bin the true curves could separate further, up to the upper bound.
      </title>
      <path d={source} fill="none" stroke="var(--chart-1)" strokeWidth="2" />
      <path d={synthetic} fill="none" stroke="var(--chart-2)" strokeWidth="2" />
      <line x1="140" y1="40" x2="140" y2="50" stroke="var(--text-1)" strokeWidth="2" />
      <text x="146" y="48" fill="var(--text-2)" fontSize="10">
        d_lo at the edge
      </text>
      <rect x="140" y="40" width="60" height="30" fill="var(--slate)" fillOpacity="0.22" />
      <text x="206" y="62" fill="var(--text-2)" fontSize="10">
        d_hi: what the bin can hide
      </text>
      <text x="20" y="96" fill="var(--text-3)" fontSize="10">
        blue = source, orange = synthetic
      </text>
    </svg>
  );
}
