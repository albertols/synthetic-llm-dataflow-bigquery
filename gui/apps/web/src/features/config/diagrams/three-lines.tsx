import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Generate → (FK integrity) → Pydantic → Pandera → uniqueness → load, rejects to the DLQ. */
export default function ThreeLinesDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const steps = ["generate", "record", "batch", "unique", "land"];
  return (
    <svg viewBox="0 0 320 84" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Rows flow from generation through a per-record check, a per-batch check and a uniqueness barrier before they
        land; every rejected row drops into the dead-letter queue below.
      </title>
      {steps.map((label, i) => (
        <g key={label}>
          <rect
            x={4 + i * 64}
            y="10"
            width="54"
            height="24"
            rx="5"
            fill="var(--surface-3)"
            stroke={i > 0 && i < 4 ? "var(--accent)" : "var(--border-strong)"}
          />
          <text x={31 + i * 64} y="26" fill="var(--text-1)" fontSize="9.5" textAnchor="middle">
            {label}
          </text>
          {i < 4 ? (
            <line x1={60 + i * 64} y1="22" x2={66 + i * 64} y2="22" stroke="var(--text-3)" strokeWidth="1.5" />
          ) : null}
          {i > 0 && i < 4 ? (
            <line x1={31 + i * 64} y1="36" x2={31 + i * 64} y2="58" stroke="var(--status-critical)" strokeWidth="1.5" />
          ) : null}
        </g>
      ))}
      <rect x="60" y="58" width="196" height="18" rx="4" fill="var(--surface-2)" stroke="var(--status-critical)" />
      <text x="158" y="71" fill="var(--text-1)" fontSize="9.5" textAnchor="middle">
        DLQ · rule_id · error context
      </text>
    </svg>
  );
}
