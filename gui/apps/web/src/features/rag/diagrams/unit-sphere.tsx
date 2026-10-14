import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Two unit vectors on the unit circle: their inner product is the cosine of the angle between them. */
export default function UnitSphereDiagram({ className }: DiagramProps) {
  const titleId = useId();
  const cx = 70;
  const cy = 60;
  const r = 44;
  const a = (-20 * Math.PI) / 180;
  const b = (-75 * Math.PI) / 180;
  return (
    <svg viewBox="0 0 320 120" role="img" aria-labelledby={titleId} className={className} width="100%">
      <title id={titleId}>
        Two vectors of length one on the unit circle with angle theta between them; for unit vectors the inner product
        equals cosine theta, so an inner-product index ranks by cosine.
      </title>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--chart-grid)" strokeWidth="1" />
      <line
        x1={cx}
        y1={cy}
        x2={cx + r * Math.cos(a)}
        y2={cy + r * Math.sin(a)}
        stroke="var(--chart-1)"
        strokeWidth="2"
      />
      <line
        x1={cx}
        y1={cy}
        x2={cx + r * Math.cos(b)}
        y2={cy + r * Math.sin(b)}
        stroke="var(--chart-2)"
        strokeWidth="2"
      />
      <path
        d={`M${cx + 16 * Math.cos(a)} ${cy + 16 * Math.sin(a)} A16 16 0 0 0 ${cx + 16 * Math.cos(b)} ${cy + 16 * Math.sin(b)}`}
        fill="none"
        stroke="var(--text-2)"
      />
      <text x={cx + 20} y={cy - 12} fontSize="10" fill="var(--text-2)">
        θ
      </text>
      <text x={cx + r * Math.cos(a) + 4} y={cy + r * Math.sin(a) + 4} fontSize="10" fill="var(--text-2)">
        u
      </text>
      <text x={cx + r * Math.cos(b) + 2} y={cy + r * Math.sin(b) - 4} fontSize="10" fill="var(--text-2)">
        v
      </text>
      <text x="136" y="44" fontSize="11" fill="var(--text-1)">
        ‖u‖ = ‖v‖ = 1
      </text>
      <text x="136" y="64" fontSize="11" fill="var(--text-1)">
        u · v = cos θ
      </text>
      <text x="136" y="86" fontSize="10" fill="var(--text-3)">
        inner-product search = cosine search
      </text>
    </svg>
  );
}
