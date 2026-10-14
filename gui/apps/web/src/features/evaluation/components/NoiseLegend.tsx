/**
 * The noise-floor legend: the glyphs every metric bar uses, in one row. The
 * run view docks it to the bottom of the viewport so it is always visible
 * while reading metrics; the drawer carries its own copy.
 */
import type { ReactNode } from "react";

import { InfoHint } from "@/components/InfoHint";
import { cn } from "@/lib/cn";

function Swatch({ children, label }: { children: ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden="true" className="relative inline-flex h-4 w-5 items-center justify-center">
        {children}
      </span>
      {label}
    </span>
  );
}

export function NoiseLegend({ className, docked = false }: { className?: string; docked?: boolean }) {
  const glyphs = (
    <>
      <Swatch label="noise floor">
        <span className="h-3 w-5 rounded-sm bg-slate/35" />
      </Swatch>
      <Swatch label="warn">
        <span className="h-3.5 w-0.5 rounded-full bg-status-warn" />
      </Swatch>
      <Swatch label="fail">
        <span className="h-4 w-0.5 rounded-full bg-status-critical" />
      </Swatch>
      <Swatch label="baseline">
        <span className="size-2.5 rotate-45 bg-text-2" />
      </Swatch>
      <Swatch label="value ± 95% CI">
        <span className="absolute h-0.5 w-5 bg-text-1" />
        <span className="relative size-2.5 rounded-full bg-accent ring-2 ring-bg" />
      </Swatch>
      <Swatch label="gate (CI bound)">
        <span className="size-0 border-x-[5px] border-b-[7px] border-x-transparent border-b-text-1" />
      </Swatch>
      <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
        <span className="font-mono text-text-1">≈</span> within noise
        <InfoHint concept="eval:status-rule" />
      </span>
    </>
  );
  return (
    <div
      role="note"
      aria-label="Legend: how metric bars read"
      data-slot="noise-legend"
      className={cn(
        "flex items-center gap-x-4 gap-y-1 text-xs text-text-2",
        docked
          ? "sticky bottom-0 z-20 -mx-4 border-t border-border bg-bg/92 px-4 py-2 backdrop-blur-md md:-mx-6 md:px-6"
          : "flex-wrap",
        className,
      )}
    >
      <span className="inline-flex shrink-0 items-center gap-0.5 font-medium text-text-1">
        Reading a metric
        <InfoHint concept="eval:metric-bar" />
      </span>
      {docked ? (
        // One line on phones: the glyphs scroll sideways inside a focusable region instead of stacking.
        <div
          role="region"
          aria-label="Legend glyphs"
          tabIndex={0}
          className="flex min-w-0 flex-1 flex-nowrap items-center gap-x-4 gap-y-1 overflow-x-auto sm:flex-wrap"
        >
          {glyphs}
        </div>
      ) : (
        glyphs
      )}
    </div>
  );
}
