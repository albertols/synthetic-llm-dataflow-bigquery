import { createPortal } from "react-dom";

/** The lasso being drawn, in client coordinates, above everything (pointer-transparent). */
export function LassoPath({ path }: { path: readonly (readonly [number, number])[] | null }) {
  if (!path || path.length < 2 || typeof document === "undefined") return null;
  const points = path.map(([x, y]) => `${x},${y}`).join(" ");
  return createPortal(
    <svg className="pointer-events-none fixed inset-0 z-50 h-screen w-screen" aria-hidden="true">
      <polygon
        points={points}
        fill="var(--accent)"
        fillOpacity={0.08}
        stroke="var(--accent)"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
    </svg>,
    document.body,
  );
}
