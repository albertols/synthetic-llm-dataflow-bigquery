import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Two 4-category columns with the same distinct count and very different entropy. */
export default function EntropyDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const balanced = [0.25, 0.25, 0.25, 0.25];
  const collapsed = [0.85, 0.05, 0.05, 0.05];
  const bars = (shares: number[], x0: number) =>
    shares.map((s, i) => (
      <rect key={i} x={x0 + i * 26} y={70 - s * 56} width="18" height={s * 56} rx="3" fill="var(--chart-1)" />
    ));
  return (
    <svg viewBox="0 0 320 96" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Two columns with four categories each: the balanced one has entropy 2 bits and normalized entropy 1; the
        collapsed one puts 85 percent on one value and has normalized entropy about 0.42.
      </title>
      {bars(balanced, 24)}
      {bars(collapsed, 186)}
      <line x1="16" y1="70" x2="304" y2="70" stroke="var(--chart-axis)" strokeWidth="1" />
      <text x="24" y="86" fill="var(--text-2)" fontSize="10">
        balanced · H_norm = 1
      </text>
      <text x="186" y="86" fill="var(--text-2)" fontSize="10">
        collapsed · H_norm ≈ 0.42
      </text>
    </svg>
  );
}
