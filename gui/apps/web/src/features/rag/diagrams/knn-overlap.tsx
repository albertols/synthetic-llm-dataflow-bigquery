import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

const HIGH: [number, number][] = [
  [40, 50],
  [52, 38],
  [30, 36],
  [58, 60],
];
const LOW: [number, number][] = [
  [210, 50],
  [222, 38],
  [262, 30],
  [228, 62],
];

/** A point's neighbours before and after projection: kept ones stay close, a lost one drifts away. */
export default function KnnOverlapDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Left, a point and its three nearest neighbours in 384 dimensions; right, the same points projected to three
        dimensions: two neighbours stayed near, one drifted away, so two of three are kept.
      </title>
      <text x="44" y="88" fontSize="10" textAnchor="middle" fill="var(--text-3)">
        384-d
      </text>
      <text x="230" y="88" fontSize="10" textAnchor="middle" fill="var(--text-3)">
        3-D picture
      </text>
      {HIGH.slice(1).map(([x, y], i) => (
        <line key={i} x1={HIGH[0]![0]} y1={HIGH[0]![1]} x2={x} y2={y} stroke="var(--text-3)" />
      ))}
      {LOW.slice(1).map(([x, y], i) => (
        <line
          key={i}
          x1={LOW[0]![0]}
          y1={LOW[0]![1]}
          x2={x}
          y2={y}
          stroke={i === 1 ? "var(--status-warn)" : "var(--text-3)"}
        />
      ))}
      {[...HIGH, ...LOW].map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i % 4 === 0 ? 5 : 4} fill={i % 4 === 0 ? "var(--accent)" : "var(--chart-1)"} />
      ))}
      <path d="M100 48 h70" stroke="var(--text-3)" markerEnd="none" />
      <text x="135" y="42" fontSize="10" textAnchor="middle" fill="var(--text-2)">
        project
      </text>
      <text x="290" y="34" fontSize="10" textAnchor="middle" fill="var(--status-warn-text)">
        lost
      </text>
      <text x="135" y="70" fontSize="10" textAnchor="middle" fill="var(--text-1)">
        kept 2 of 3
      </text>
    </svg>
  );
}
