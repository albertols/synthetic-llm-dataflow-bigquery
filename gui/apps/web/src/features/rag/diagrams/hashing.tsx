import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const TOKENS: [string, number, 1 | -1][] = [
  ["city", 2, 1],
  ["is", 6, -1],
  ["Pine,", 2, -1],
];

/** Tokens hash to a bucket with a sign; colliding tokens share a cell; the sum is L2-normalised. */
export default function HashingDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 100" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Three tokens are hashed: city goes to bucket 2 with a plus sign, is to bucket 6 with a minus sign, and Pine
        comma also to bucket 2 with a minus sign, cancelling city there; the vector is then normalised.
      </title>
      {TOKENS.map(([token, bucket, sign], i) => (
        <g key={token}>
          <rect
            x={8}
            y={6 + i * 28}
            width="58"
            height="22"
            rx="4"
            fill="var(--surface-3)"
            stroke="var(--border-strong)"
          />
          <text
            x={37}
            y={21 + i * 28}
            fontSize="10"
            textAnchor="middle"
            fill="var(--text-1)"
            fontFamily="var(--font-code)"
          >
            {token}
          </text>
          <path
            d={`M68 ${17 + i * 28} L${104 + bucket * 24} 76`}
            stroke={sign > 0 ? "var(--div-pos)" : "var(--div-neg)"}
            strokeWidth="1.5"
            fill="none"
          />
          <text x={80} y={14 + i * 28} fontSize="9" fill="var(--text-3)">
            {sign > 0 ? "+1" : "−1"}
          </text>
        </g>
      ))}
      {Array.from({ length: 8 }, (_, b) => (
        <g key={b}>
          <rect
            x={92 + b * 24}
            y="76"
            width="22"
            height="18"
            rx="3"
            fill={b === 6 ? "var(--div-neg)" : "var(--div-mid)"}
            stroke="var(--border)"
          />
          <text x={103 + b * 24} y="89" fontSize="9" textAnchor="middle" fill="var(--text-2)">
            {b === 2 ? "0" : b === 6 ? "−" : ""}
          </text>
        </g>
      ))}
      <text x="292" y="89" fontSize="9" fill="var(--text-3)">
        …384
      </text>
    </svg>
  );
}
