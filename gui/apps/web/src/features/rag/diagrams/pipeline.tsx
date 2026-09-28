import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const STEPS = ["row", "GReaT text", "384-d vector", "exact index", "8 seeds", "prompt → pool"];

/** The b1_rag chain, once per column: a row becomes text, a vector, an index entry; eight seeds reach the prompt. */
export default function PipelineDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 76" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        The chain: a reference row becomes a GReaT sentence, a 384-dimensional vector and an entry of an exact index;
        eight retrieved seeds go into the prompt that builds the pool.
      </title>
      {STEPS.map((label, i) => {
        const x = 6 + (i % 3) * 106;
        const y = i < 3 ? 6 : 42;
        return (
          <g key={label}>
            <rect x={x} y={y} width="94" height="26" rx="5" fill="var(--surface-3)" stroke="var(--border-strong)" />
            <text x={x + 47} y={y + 17} fontSize="10" textAnchor="middle" fill="var(--text-1)">
              {label}
            </text>
            {i % 3 < 2 ? (
              <path d={`M${x + 96} ${y + 13} h8`} stroke="var(--accent)" strokeWidth="1.5" markerEnd="none" />
            ) : null}
          </g>
        );
      })}
      <path d="M265 32 v5 H53 v5" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
    </svg>
  );
}
