/**
 * "≈ within noise, was FAIL": the marker a PASS row carries when the
 * evaluator downgraded its WARN/FAIL crossing as sampling noise (Ruling R40,
 * detail.noise_downgraded_from). Text, not colour: the ≈ glyph is the one the
 * docked noise legend explains, and the words carry the meaning on their own.
 */
import { cn } from "@/lib/cn";

import { downgradeLabel } from "../lib/reading";

export function NoiseDowngrade({ from, className }: { from: "warn" | "fail"; className?: string }) {
  const label = downgradeLabel(from);
  return (
    <span
      data-slot="noise-downgrade"
      title={`The evaluator measured a ${from.toUpperCase()} crossing that sampling noise explains at this n; it counts as PASS and scores as no effect.`}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-1.5 py-px text-[11px] whitespace-nowrap text-text-2",
        className,
      )}
    >
      <span aria-hidden="true" className="font-mono text-text-1">
        ≈
      </span>
      <span className="sr-only">{label}</span>
      <span aria-hidden="true">{label.slice(2)}</span>
    </span>
  );
}
