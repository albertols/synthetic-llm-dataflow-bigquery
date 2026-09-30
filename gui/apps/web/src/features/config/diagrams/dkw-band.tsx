import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** An empirical CDF step inside its ±ε DKW band. */
export default function DkwBandDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const steps = [0, 0.08, 0.2, 0.35, 0.52, 0.66, 0.78, 0.88, 0.95, 1];
  const x = (i: number) => 24 + i * 30;
  const y = (p: number) => 76 - p * 60;
  const path = steps.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p)} L${x(i + 1)},${y(p)}`).join(" ");
  const band = (sign: number) =>
    steps.map(
      (p, i) =>
        `${x(i)},${y(Math.min(1, Math.max(0, p + sign * 0.1)))} ${x(i + 1)},${y(Math.min(1, Math.max(0, p + sign * 0.1)))}`,
    );
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A step-shaped empirical CDF inside a shaded band of half-width epsilon: the true CDF lies inside the band with
        probability 1 minus alpha.
      </title>
      <polygon
        points={[...band(1), ...band(-1).reverse()].join(" ")}
        fill="var(--chart-1)"
        fillOpacity="0.16"
        stroke="none"
      />
      <line x1="24" y1="76" x2="316" y2="76" stroke="var(--chart-axis)" strokeWidth="1" />
      <path d={path} fill="none" stroke="var(--chart-1)" strokeWidth="2" />
      <text x="24" y="90" fill="var(--text-3)" fontSize="10">
        value
      </text>
      <text x="250" y="30" fill="var(--text-2)" fontSize="10">
        F̂ₙ ± ε
      </text>
    </svg>
  );
}
