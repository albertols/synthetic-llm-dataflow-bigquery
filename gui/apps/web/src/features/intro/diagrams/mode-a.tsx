import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const GATES = ["FK integrity", "Pydantic", "Pandera", "uniqueness"] as const;

/** Rows pass four gates in order; each gate diverts its rejects to the DLQ; survivors land. */
export default function ModeADiagram({ className }: DiagramProps) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const w = 62;
  const gap = 8;
  const y = 26;
  const h = 30;
  const x = (i: number) => 8 + i * (w + gap);
  return (
    <svg viewBox="0 0 320 110" role="img" aria-labelledby={`${id}-t`} className={className} width="100%">
      <title id={`${id}-t`}>
        Generated rows pass four gates in order: FK integrity, Pydantic, Pandera and uniqueness. Each gate diverts its
        rejects to the dead-letter queue with their reason; the rows that pass all four land.
      </title>
      <defs>
        <marker id={`${id}-m`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--text-3)" />
        </marker>
      </defs>
      {GATES.map((gate, i) => (
        <g key={gate}>
          <rect x={x(i)} y={y} width={w} height={h} rx="5" fill="var(--surface-3)" stroke="var(--accent)" />
          <text x={x(i) + w / 2} y={y + 19} fill="var(--text-1)" fontSize="9.5" textAnchor="middle">
            {gate}
          </text>
          {i < GATES.length - 1 ? (
            <line
              x1={x(i) + w}
              y1={y + h / 2}
              x2={x(i + 1) - 1}
              y2={y + h / 2}
              stroke="var(--text-3)"
              markerEnd={`url(#${id}-m)`}
            />
          ) : null}
          <line
            x1={x(i) + w / 2}
            y1={y + h}
            x2={x(i) + w / 2}
            y2={88}
            stroke="var(--status-critical)"
            strokeDasharray="3 2"
            markerEnd={`url(#${id}-m)`}
          />
        </g>
      ))}
      <rect
        x="8"
        y="90"
        width={x(3) + w - 8}
        height="16"
        rx="4"
        fill="var(--surface-3)"
        stroke="var(--border-strong)"
      />
      <text x={(x(3) + w) / 2 + 4} y="101" fill="var(--text-2)" fontSize="9" textAnchor="middle">
        dlq — every reject, with its rule and reason
      </text>
      <line x1={x(3) + w} y1={y + h / 2} x2="306" y2={y + h / 2} stroke="var(--text-3)" markerEnd={`url(#${id}-m)`} />
      <text x="312" y={y - 6} fill="var(--text-2)" fontSize="9" textAnchor="end">
        landing
      </text>
      <text x="8" y="14" fill="var(--text-3)" fontSize="9">
        generated rows →
      </text>
    </svg>
  );
}
