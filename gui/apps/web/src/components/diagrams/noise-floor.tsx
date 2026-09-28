import { useId } from "react";

import type { DiagramProps } from "../MiniDiagram";

/** A metric axis: the noise-floor band, the warn and fail thresholds, and one value. */
export default function NoiseFloorDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 92" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A metric axis from 0: values inside the shaded noise-floor band are indistinguishable from sampling noise; the
        warn and fail thresholds sit further right.
      </title>
      <rect x="16" y="30" width="72" height="22" rx="4" fill="var(--slate)" fillOpacity="0.28" />
      <line x1="16" y1="52" x2="304" y2="52" stroke="var(--chart-axis)" strokeWidth="1" />
      <line x1="16" y1="26" x2="16" y2="56" stroke="var(--text-3)" strokeWidth="1" />
      <text x="16" y="70" fill="var(--text-3)" fontSize="10" textAnchor="middle">
        0
      </text>
      <text x="52" y="22" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        noise floor
      </text>
      <line x1="176" y1="30" x2="176" y2="56" stroke="var(--status-warn)" strokeWidth="2" />
      <text x="176" y="70" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        warn
      </text>
      <line x1="256" y1="30" x2="256" y2="56" stroke="var(--status-critical)" strokeWidth="2" />
      <text x="256" y="70" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        fail
      </text>
      <circle cx="64" cy="41" r="5" fill="var(--accent)" stroke="var(--surface-2)" strokeWidth="2" />
      <text x="64" y="86" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        value ≈ noise → indistinguishable
      </text>
    </svg>
  );
}
