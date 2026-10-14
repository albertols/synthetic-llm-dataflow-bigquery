import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const POINTS: [number, number][] = [
  [60, 40],
  [72, 52],
  [66, 30],
  [80, 44],
  [54, 56],
  [74, 34],
  [62, 62],
  [86, 58],
  [170, 22],
  [262, 70],
  [200, 80],
];
const WALK = [3, 9, 8, 10];

/** Start at the medoid, then keep adding the point farthest from every pick so far. */
export default function KcenterDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        The walk starts at the medoid of the dense group (pick 1), then jumps to the farthest point (pick 2), then to
        the point farthest from both (pick 3), and so on: every mode is reached in a few picks.
      </title>
      {POINTS.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="4" fill="var(--chart-1)" />
      ))}
      {WALK.slice(1).map((p, i) => {
        const from = POINTS[WALK[i]!]!;
        const to = POINTS[p]!;
        return (
          <line key={p} x1={from[0]} y1={from[1]} x2={to[0]} y2={to[1]} stroke="var(--accent)" strokeWidth="1.2" />
        );
      })}
      {WALK.map((p, rank) => (
        <g key={p}>
          <circle
            cx={POINTS[p]![0]}
            cy={POINTS[p]![1]}
            r="8"
            fill="var(--surface-2)"
            stroke="var(--text-1)"
            strokeWidth="1.5"
          />
          <text x={POINTS[p]![0]} y={POINTS[p]![1] + 3.5} fontSize="9" textAnchor="middle" fill="var(--text-1)">
            {rank + 1}
          </text>
        </g>
      ))}
    </svg>
  );
}
