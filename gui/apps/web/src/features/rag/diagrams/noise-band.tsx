import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Cosines of unrelated hashed texts pile up in a narrow band around 0 (±1/√384). */
export default function NoiseBandDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const bars = [1, 3, 8, 14, 18, 14, 8, 3, 1];
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A histogram of cosines between unrelated hashed texts, centred on 0 inside a shaded band of plus or minus 0.051;
        a pair far to the right shares tokens.
      </title>
      <rect x="112" y="8" width="72" height="62" fill="var(--slate)" fillOpacity="0.25" rx="3" />
      {bars.map((h, i) => (
        <rect key={i} x={104 + i * 10} y={70 - h * 3} width="8" height={h * 3} rx="2" fill="var(--chart-1)" />
      ))}
      <rect x="262" y="61" width="8" height="9" rx="2" fill="var(--chart-1)" />
      <line x1="16" y1="70" x2="304" y2="70" stroke="var(--chart-axis)" />
      <text x="148" y="84" fontSize="10" textAnchor="middle" fill="var(--text-2)">
        0 ± 0.051
      </text>
      <text x="266" y="84" fontSize="10" textAnchor="middle" fill="var(--text-2)">
        shared tokens
      </text>
    </svg>
  );
}
