import { lazy, Suspense, useCallback, useState, useSyncExternalStore } from "react";

import { cn } from "@/lib/cn";

import { Skeleton } from "./ui/skeleton";

const MermaidRender = lazy(() => import("./MermaidRender"));

export type MermaidProps = {
  /** Mermaid source (flowchart, sequence …). */
  chart: string;
  /** What the diagram shows, in one sentence; the diagram is announced as an image with this name. */
  ariaLabel: string;
  /**
   * Below the `md` breakpoint (phones), lay a left-to-right flowchart out in
   * this direction instead: `flowchart LR` scaled to 390 px has unreadable
   * labels. Same nodes and edges; only the header's direction changes.
   */
  narrowDirection?: "TB" | "BT";
  className?: string;
};

/** Matches Tailwind's `md` breakpoint: narrower than 768 px. */
const NARROW = "(max-width: 767px)";

function subscribeNarrow(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const media = window.matchMedia(NARROW);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function isNarrow(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia(NARROW).matches;
}

/**
 * `flowchart LR` / `graph RL` … → the same chart with `direction` in its
 * header. Other diagram kinds (sequence, state …) come back unchanged.
 */
export function withFlowchartDirection(chart: string, direction: "TB" | "BT"): string {
  return chart.replace(/^(\s*(?:flowchart|graph))\s+(?:LR|RL)\b/, `$1 ${direction}`);
}

/**
 * A mermaid diagram (lazy chunk; strict security; themed from tokens and
 * re-rendered on theme change). Keep the facts in the page text too: the
 * diagram is announced as one image. If mermaid cannot render the source, the
 * frame stops being an image and the error text is read like any paragraph.
 */
export function Mermaid({ chart, ariaLabel, narrowDirection, className }: MermaidProps) {
  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, () => false);
  const source = narrowDirection && narrow ? withFlowchartDirection(chart, narrowDirection) : chart;
  const [failed, setFailed] = useState(false);
  const onError = useCallback((message: string | null) => setFailed(message !== null), []);
  return (
    <div
      role={failed ? undefined : "img"}
      aria-label={failed ? undefined : ariaLabel}
      className={cn("rounded-lg border border-border bg-surface-1 p-4", className)}
    >
      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <MermaidRender chart={source} ariaLabel={ariaLabel} onError={onError} />
      </Suspense>
    </div>
  );
}
