import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Plug-in entropy rises with n; comparing a 90M-row table with a 100k-row source needs the same n on both sides. */
export default function MatchedNDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const curve = "M24 78 C 70 40, 120 30, 300 22";
  return (
    <svg viewBox="0 0 320 100" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Measured entropy climbs with the number of rows read and flattens out. Comparing the source at 100 thousand rows
        with the synthetic table at 90 million rows would read the curve at two different points; matched n reads both
        at the same point.
      </title>
      <line x1="24" y1="84" x2="304" y2="84" stroke="var(--chart-axis)" />
      <line x1="24" y1="12" x2="24" y2="84" stroke="var(--chart-axis)" />
      <path d={curve} fill="none" stroke="var(--chart-1)" strokeWidth="2" />
      <line x1="90" y1="16" x2="90" y2="84" stroke="var(--text-3)" strokeWidth="1" />
      <circle cx="90" cy="45" r="4.5" fill="var(--chart-1)" stroke="var(--surface-2)" strokeWidth="2" />
      <circle cx="292" cy="22" r="4.5" fill="var(--chart-2)" stroke="var(--surface-2)" strokeWidth="2" />
      <text x="94" y="72" fill="var(--text-2)" fontSize="10">
        matched n: compare here
      </text>
      <text x="300" y="38" fill="var(--text-2)" fontSize="10" textAnchor="end">
        full synthetic n
      </text>
      <text x="28" y="96" fill="var(--text-3)" fontSize="10">
        rows read (n) →
      </text>
    </svg>
  );
}
