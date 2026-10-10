import { useId } from "react";

import type { DiagramProps } from "@/components/MiniDiagram";

/** Two unit vectors on the unit circle: their inner product is the cosine of the angle between them. */
export default function CosineDiagram({ className }: DiagramProps) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const cx = 70;
  const cy = 70;
  const r = 52;
  const a = (-20 * Math.PI) / 180;
  const b = (-75 * Math.PI) / 180;
  const u = [cx + r * Math.cos(a), cy + r * Math.sin(a)] as const;
  const v = [cx + r * Math.cos(b), cy + r * Math.sin(b)] as const;
  const arcR = 20;
  const arcStart = [cx + arcR * Math.cos(a), cy + arcR * Math.sin(a)] as const;
  const arcEnd = [cx + arcR * Math.cos(b), cy + arcR * Math.sin(b)] as const;
  return (
    <svg viewBox="0 0 320 132" role="img" aria-labelledby={`${id}-t`} className={className} width="100%">
      <title id={`${id}-t`}>
        Two unit vectors u and v on the unit circle with the angle theta between them; for unit vectors the inner
        product u·v equals cos theta, so an exact inner-product index is an exact cosine search.
      </title>
      <defs>
        <marker id={`${id}-m`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--text-2)" />
        </marker>
      </defs>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--border-strong)" strokeDasharray="3 3" />
      <line x1={cx - r - 8} y1={cy} x2={cx + r + 8} y2={cy} stroke="var(--chart-grid)" />
      <line x1={cx} y1={cy - r - 8} x2={cx} y2={cy + r + 8} stroke="var(--chart-grid)" />
      <line x1={cx} y1={cy} x2={u[0]} y2={u[1]} stroke="var(--chart-1)" strokeWidth="2.5" markerEnd={`url(#${id}-m)`} />
      <line x1={cx} y1={cy} x2={v[0]} y2={v[1]} stroke="var(--chart-2)" strokeWidth="2.5" markerEnd={`url(#${id}-m)`} />
      <path
        d={`M ${arcStart[0]} ${arcStart[1]} A ${arcR} ${arcR} 0 0 0 ${arcEnd[0]} ${arcEnd[1]}`}
        fill="none"
        stroke="var(--text-3)"
      />
      <text x={cx + 22} y={cy - 16} fill="var(--text-2)" fontSize="10">
        θ
      </text>
      <text x={u[0] + 4} y={u[1] + 4} fill="var(--text-1)" fontSize="11" fontWeight="600">
        u
      </text>
      <text x={v[0] + 4} y={v[1] - 2} fill="var(--text-1)" fontSize="11" fontWeight="600">
        v
      </text>
      <text x="150" y="40" fill="var(--text-1)" fontSize="12" fontWeight="600">
        ‖u‖ = ‖v‖ = 1
      </text>
      <text x="150" y="62" fill="var(--text-2)" fontSize="11">
        inner product u · v = cos θ
      </text>
      <text x="150" y="84" fill="var(--text-2)" fontSize="11">
        IndexFlatIP ranks by u · v:
      </text>
      <text x="150" y="100" fill="var(--text-2)" fontSize="11">
        an exact cosine search
      </text>
    </svg>
  );
}
