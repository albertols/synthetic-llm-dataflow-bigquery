import { lazy, Suspense } from "react";

import { cn } from "@/lib/cn";

import { Skeleton } from "./ui/skeleton";

const MermaidRender = lazy(() => import("./MermaidRender"));

export type MermaidProps = {
  /** Mermaid source (flowchart, sequence …). */
  chart: string;
  /** What the diagram shows, in one sentence; the diagram is announced as an image with this name. */
  ariaLabel: string;
  className?: string;
};

/**
 * A mermaid diagram (lazy chunk; strict security; themed from tokens and
 * re-rendered on theme change). Keep the facts in the page text too: the
 * diagram is announced as one image.
 */
export function Mermaid({ chart, ariaLabel, className }: MermaidProps) {
  return (
    <div
      role="img"
      aria-label={ariaLabel}
      className={cn("rounded-lg border border-border bg-surface-1 p-4", className)}
    >
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <MermaidRender chart={chart} />
      </Suspense>
    </div>
  );
}
