import { useId } from "react";

import type { DiagramProps } from "../MiniDiagram";

const CELL_W = 22;
const CELL_H = 12;

/** Where each catalogue level looks: a cell, a column, a pair, a row, a table, an edge, the model. */
export default function LevelsDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const markerId = `lv-arrow-${titleId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const cells = [];
  for (let r = 0; r < 4; r += 1) {
    for (let c = 0; c < 4; c += 1) {
      cells.push(
        <rect
          key={`${r}-${c}`}
          x={20 + c * CELL_W}
          y={24 + r * CELL_H}
          width={CELL_W - 2}
          height={CELL_H - 2}
          rx="2"
          fill="var(--surface-3)"
        />,
      );
    }
  }
  return (
    <svg viewBox="0 0 320 112" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Evaluation levels on two linked tables: a field is one cell, a column is one column, a pair is two columns, a
        row is one row, a table is the whole grid, a relationship is the foreign-key edge, and the model is both tables.
      </title>
      <rect
        x="8"
        y="8"
        width="304"
        height="96"
        rx="8"
        fill="none"
        stroke="var(--border-strong)"
        strokeDasharray="4 3"
      />
      <text x="300" y="100" fill="var(--text-3)" fontSize="9" textAnchor="end">
        model
      </text>
      {cells}
      <rect x="20" y="24" width={CELL_W - 2} height={CELL_H - 2} rx="2" fill="var(--accent)" />
      <rect
        x={20 + CELL_W}
        y="22"
        width={CELL_W}
        height={CELL_H * 4 + 2}
        rx="3"
        fill="none"
        stroke="var(--chart-1)"
        strokeWidth="1.5"
      />
      <rect
        x={18 + CELL_W * 2}
        y="20"
        width={CELL_W * 2 + 2}
        height={CELL_H * 4 + 6}
        rx="4"
        fill="none"
        stroke="var(--chart-5)"
        strokeWidth="1.2"
        strokeDasharray="3 2"
      />
      <rect
        x="18"
        y={22 + CELL_H * 2}
        width={CELL_W * 4 + 2}
        height={CELL_H}
        rx="3"
        fill="none"
        stroke="var(--cpu)"
        strokeWidth="1.5"
      />
      <text x="20" y="18" fill="var(--text-2)" fontSize="9">
        field · column · pair
      </text>
      <text x="20" y="88" fill="var(--text-2)" fontSize="9">
        row · table
      </text>
      <rect x="220" y="36" width="70" height="36" rx="4" fill="var(--surface-3)" />
      <text x="255" y="58" fill="var(--text-2)" fontSize="9" textAnchor="middle">
        parent
      </text>
      <line x1="112" y1="54" x2="216" y2="54" stroke="var(--gpu)" strokeWidth="2" markerEnd={`url(#${markerId})`} />
      <text x="164" y="48" fill="var(--text-2)" fontSize="9" textAnchor="middle">
        relationship
      </text>
      <defs>
        <marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--gpu)" />
        </marker>
      </defs>
    </svg>
  );
}
