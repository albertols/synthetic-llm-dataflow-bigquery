import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const CELLS = [
  ["city", "Pine"],
  ["age", "29"],
  ["email", "null"],
];

/** A row's cells become "column is value" clauses, in schema order, joined by commas. */
export default function GreatDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 92" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A row with city Pine, age 29 and a missing email becomes the sentence: city is Pine, age is 29, email is null.
      </title>
      {CELLS.map(([column, value], i) => (
        <g key={column}>
          <rect
            x={10 + i * 100}
            y="6"
            width="92"
            height="30"
            rx="4"
            fill="var(--surface-3)"
            stroke="var(--border-strong)"
          />
          <text x={56 + i * 100} y="18" fontSize="9" textAnchor="middle" fill="var(--text-3)">
            {column}
          </text>
          <text x={56 + i * 100} y="31" fontSize="11" textAnchor="middle" fill="var(--text-1)">
            {value}
          </text>
          <path d={`M${56 + i * 100} 38 v12`} stroke="var(--accent)" strokeWidth="1.5" />
        </g>
      ))}
      <text x="160" y="72" fontSize="11" textAnchor="middle" fill="var(--text-1)" fontFamily="var(--font-code)">
        city is Pine, age is 29, email is null
      </text>
      <text x="160" y="88" fontSize="9" textAnchor="middle" fill="var(--text-3)">
        schema order, never shuffled · a missing value is written “null”
      </text>
    </svg>
  );
}
