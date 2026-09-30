import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** The DCR holdout test: each synthetic row is closer to R or to H; no memorization sits at one half. */
export default function HoldoutShareDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const x = (share: number) => 20 + share * 280;
  return (
    <svg viewBox="0 0 320 90" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        A share axis from 0 to 1: 0.5 means synthetic rows are as often closest to the reference R as to the holdout H.
        The Wilson interval of the observed share is checked by its lower bound against warn 0.55 and fail 0.6.
      </title>
      <line x1={x(0)} y1="44" x2={x(1)} y2="44" stroke="var(--chart-axis)" />
      {[0, 0.5, 1].map((t) => (
        <text key={t} x={x(t)} y="84" fill="var(--text-3)" fontSize="10" textAnchor="middle">
          {t}
        </text>
      ))}
      <line x1={x(0.5)} y1="30" x2={x(0.5)} y2="58" stroke="var(--text-3)" strokeWidth="1" />
      <text x={x(0.5)} y="24" fill="var(--text-2)" fontSize="10" textAnchor="middle">
        no memorization
      </text>
      <line x1={x(0.55)} y1="36" x2={x(0.55)} y2="52" stroke="var(--status-warn)" strokeWidth="2" />
      <line x1={x(0.6)} y1="36" x2={x(0.6)} y2="52" stroke="var(--status-critical)" strokeWidth="2" />
      <line x1={x(0.43)} y1="66" x2={x(0.62)} y2="66" stroke="var(--text-1)" strokeWidth="2" />
      <line x1={x(0.43)} y1="61" x2={x(0.43)} y2="71" stroke="var(--text-1)" strokeWidth="2" />
      <line x1={x(0.62)} y1="61" x2={x(0.62)} y2="71" stroke="var(--text-1)" strokeWidth="2" />
      <circle cx={x(0.53)} cy="66" r="5" fill="var(--accent)" stroke="var(--surface-2)" strokeWidth="2" />
      <text x={x(0.43) - 6} y="70" fill="var(--text-2)" fontSize="10" textAnchor="end">
        gate = ci_low
      </text>
    </svg>
  );
}
