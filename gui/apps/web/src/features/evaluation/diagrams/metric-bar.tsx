import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** How a metric bar reads: noise-floor band, warn and fail ticks, the baseline diamond and the value with its CI. */
export default function MetricBarDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A metric bar: a grey noise-floor band starts at the reference 0, a diamond marks the reference baseline, amber
        and red ticks mark the warn and fail thresholds, and a dot with a whisker shows the value and its confidence
        interval.
      </title>
      <line x1="16" y1="48" x2="304" y2="48" stroke="var(--chart-axis)" strokeWidth="1" />
      <rect x="16" y="38" width="46" height="20" rx="3" fill="var(--slate)" fillOpacity="0.3" />
      <text x="39" y="30" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        noise floor
      </text>
      <path d="M78 42 L84 48 L78 54 L72 48 Z" fill="var(--text-2)" />
      <text x="78" y="72" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        baseline
      </text>
      <line x1="180" y1="36" x2="180" y2="60" stroke="var(--status-warn)" strokeWidth="2" />
      <text x="180" y="30" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        warn
      </text>
      <line x1="252" y1="36" x2="252" y2="60" stroke="var(--status-critical)" strokeWidth="2" />
      <text x="252" y="30" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        fail
      </text>
      <line x1="112" y1="48" x2="160" y2="48" stroke="var(--text-1)" strokeWidth="2" />
      <line x1="112" y1="43" x2="112" y2="53" stroke="var(--text-1)" strokeWidth="2" />
      <line x1="160" y1="43" x2="160" y2="53" stroke="var(--text-1)" strokeWidth="2" />
      <circle cx="134" cy="48" r="5" fill="var(--accent)" stroke="var(--surface-2)" strokeWidth="2" />
      <text x="136" y="72" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        value ± CI
      </text>
      <text x="16" y="90" fill="var(--text-3)" fontSize="10">
        lower is better → the gate reads the value, or ci_low
      </text>
    </svg>
  );
}
