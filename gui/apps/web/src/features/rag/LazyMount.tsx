/**
 * Mounts its children once they come within `margin` of the viewport, then
 * keeps them. The sections below the explorer carry charts, SVG grids and
 * exact-port computations; on a laptop CPU, rendering them all at load
 * delayed the 3-D cloud by seconds. Until then a labelled placeholder of
 * similar height holds the layout (anchors still land on the section
 * heading, which is outside this wrapper). Without IntersectionObserver
 * (tests, old browsers) the children mount at once.
 */
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useInViewOnce } from "@/lib/useInViewOnce";

export function LazyMount({
  children,
  minHeight = 480,
  margin = "250px",
  label,
}: {
  children: ReactNode;
  /** Placeholder height, close to the content's, so the page does not jump. */
  minHeight?: number;
  margin?: string;
  /** What is loading, for screen readers. */
  label: string;
}) {
  const [ref, visible] = useInViewOnce<HTMLDivElement>(margin);
  if (visible) return <>{children}</>;
  return (
    <div ref={ref} style={{ minHeight }} className="grid content-start gap-3" aria-busy="true">
      <span className="sr-only">{label} loads when you scroll to it.</span>
      <Skeleton className="h-10 w-2/3" />
      <Skeleton style={{ height: Math.max(minHeight - 64, 120) }} className="w-full" />
    </div>
  );
}
