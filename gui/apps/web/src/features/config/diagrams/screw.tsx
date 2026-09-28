import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** The amp's parts: a dial (turnable), a switch, a fixed screw (constant), a readout (derived). */
export default function ScrewDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 84" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Four panel parts: a dial you can turn, a stepped switch, a fixed screw for a code constant, and a readout for a
        derived value.
      </title>
      <circle cx="40" cy="34" r="18" fill="var(--surface-3)" stroke="var(--border-strong)" strokeWidth="2" />
      <line x1="40" y1="34" x2="52" y2="22" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
      <text x="40" y="72" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        dial
      </text>
      <circle cx="120" cy="34" r="18" fill="var(--surface-3)" stroke="var(--border-strong)" strokeWidth="2" />
      <line x1="120" y1="34" x2="120" y2="18" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
      <text x="120" y="72" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        switch
      </text>
      <circle cx="200" cy="34" r="12" fill="var(--surface-3)" stroke="var(--text-3)" strokeWidth="1.5" />
      <line x1="192" y1="30" x2="208" y2="38" stroke="var(--text-3)" strokeWidth="2.5" strokeLinecap="round" />
      <text x="200" y="72" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        fixed screw
      </text>
      <rect x="252" y="22" width="52" height="24" rx="4" fill="var(--bg)" stroke="var(--border-strong)" />
      <text x="278" y="38" fill="var(--accent-text)" fontSize="10" textAnchor="middle" fontFamily="monospace">
        0.7 → 1.3
      </text>
      <text x="278" y="72" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        readout
      </text>
    </svg>
  );
}
