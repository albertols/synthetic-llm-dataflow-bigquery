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
  [170, 30],
  [240, 70],
  [200, 76],
];

/** The centroid's nearest neighbours all sit in the dense mode; the sparse modes are never shown. */
export default function CentroidDiagram({ className }: DiagramProps) {
  const titleId = useId();
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Most points sit in one dense group; the centroid lies near it, and its nearest neighbours — the seeds — are all
        from that group, while three sparse points far away are never picked.
      </title>
      {POINTS.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="4" fill="var(--chart-1)" />
      ))}
      {POINTS.slice(0, 4).map(([x, y], i) => (
        <circle key={`s${i}`} cx={x} cy={y} r="7" fill="none" stroke="var(--text-1)" strokeWidth="1.5" />
      ))}
      <path d="M96 48 l6 -6 m-6 0 l6 6" stroke="var(--accent)" strokeWidth="2" />
      <text x="110" y="52" fontSize="10" fill="var(--text-2)">
        centroid
      </text>
      <text x="220" y="20" fontSize="10" textAnchor="middle" fill="var(--text-3)">
        rare modes: never seeds
      </text>
    </svg>
  );
}
