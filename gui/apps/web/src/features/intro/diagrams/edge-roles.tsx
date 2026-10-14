import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

type Node = { key: string; label: string; x: number; y: number };

const NODES: Node[] = [
  { key: "gp", label: "grandparent", x: 60, y: 18 },
  { key: "p", label: "parent", x: 60, y: 62 },
  { key: "other", label: "other parent", x: 250, y: 40 },
  { key: "c", label: "child", x: 150, y: 104 },
];

/** One child and its edges: driving (solid), implied (dotted), independent or conditional (dashed). */
export default function EdgeRolesDiagram({ className }: DiagramProps) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const at = (key: string) => NODES.find((n) => n.key === key)!;
  const c = at("c");
  const p = at("p");
  const gp = at("gp");
  const other = at("other");
  return (
    <svg viewBox="0 0 320 124" role="img" aria-labelledby={`${id}-t`} className={className} width="100%">
      <title id={`${id}-t`}>
        A child with three foreign-key edges: a solid driving edge to its parent, whose landed keys generate it; a
        dotted implied edge to its grandparent, copied from the driving key; and a dashed edge to another parent,
        independent when it shares no column with the driving edge, conditional when it does.
      </title>
      <defs>
        <marker id={`${id}-m`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--text-2)" />
        </marker>
      </defs>
      <line
        x1={p.x}
        y1={p.y + 9}
        x2={gp.x}
        y2={gp.y + 11}
        stroke="var(--text-3)"
        strokeWidth="1.5"
        markerEnd={`url(#${id}-m)`}
      />
      <line
        x1={c.x - 20}
        y1={c.y - 9}
        x2={p.x + 20}
        y2={p.y + 10}
        stroke="var(--accent)"
        strokeWidth="2.5"
        markerEnd={`url(#${id}-m)`}
      />
      <text x={100} y={92} fill="var(--accent-text)" fontSize="9.5" fontWeight="600" textAnchor="end">
        driving
      </text>
      <path
        d={`M ${c.x - 34} ${c.y - 2} C 8 104, 4 40, ${gp.x - 30} ${gp.y + 4}`}
        fill="none"
        stroke="var(--text-2)"
        strokeWidth="1.5"
        strokeDasharray="1.5 3"
        markerEnd={`url(#${id}-m)`}
      />
      <text x={8} y={70} fill="var(--text-2)" fontSize="9.5">
        implied
      </text>
      <line
        x1={c.x + 24}
        y1={c.y - 9}
        x2={other.x - 16}
        y2={other.y + 11}
        stroke="var(--chart-1)"
        strokeWidth="2"
        strokeDasharray="6 4"
        markerEnd={`url(#${id}-m)`}
      />
      <text x={214} y={86} fill="var(--text-2)" fontSize="9.5">
        independent / conditional
      </text>
      {NODES.map((n) => (
        <g key={n.key}>
          <rect
            x={n.x - 38}
            y={n.y - 9}
            width="76"
            height="19"
            rx="4"
            fill="var(--surface-3)"
            stroke="var(--border-strong)"
          />
          <text x={n.x} y={n.y + 4} fill="var(--text-1)" fontSize="9.5" textAnchor="middle">
            {n.label}
          </text>
        </g>
      ))}
    </svg>
  );
}
