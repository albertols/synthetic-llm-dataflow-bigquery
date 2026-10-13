/**
 * AdjustmentPill — the generator's verdict on a declared key the launch
 * adjusted (ADR 0038), wherever a `table.pk_duplicate_rate` row or a
 * `validation_runs` row is shown:
 *
 *   faithful      "Adjusted · faithful"  (info)  the synthetic key repeats as often as the source's
 *   under / over  "Adjusted · under"     (warn)  the copy missed, in that direction
 *   unmeasured    "Adjusted?"  (outline badge)   adjusted, but no share was measured
 *   unknown       "Adjusted?"  (outline badge)   no validation_runs row could be joined
 *   not_adjusted  nothing: the normal "unique key" rule applies
 *
 * Only the first two replace the evaluator's pill, and then what the evaluator
 * stored (`evaluator`, e.g. "evaluator: FAIL (threshold 0)") stays on screen
 * next to it: the raw status is never lost. `disagree` shows "Measures
 * disagree" instead of a verdict. The (i) explains the reading.
 */
import type { AdjustmentVerdict } from "@contracts/adjustments";

import { InfoHint } from "@/components/InfoHint";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { verdictMark } from "@/lib/adjustments";
import { cn } from "@/lib/cn";

export type AdjustmentPillProps = {
  verdict: AdjustmentVerdict;
  /** What the evaluator stored for the same key, in words; shown after the pill that replaced its own. */
  evaluator?: string | null;
  /** The evaluator's and the generator's shares differ: a warning replaces the verdict. */
  disagree?: boolean;
  size?: "sm" | "md";
  /** The (i) of `eval:model-adjusted` after the pill (default true). */
  hint?: boolean;
  className?: string;
};

export function AdjustmentPill({
  verdict,
  evaluator,
  disagree = false,
  size = "sm",
  hint = true,
  className,
}: AdjustmentPillProps) {
  const mark = verdictMark(verdict);
  if (!mark) return null;
  const replaces = !disagree && mark.kind === "adjusted";
  return (
    <span
      data-slot="adjustment"
      data-verdict={verdict}
      data-kind={disagree ? "disagree" : mark.kind}
      className={cn("inline-flex flex-wrap items-center gap-x-1 gap-y-0.5", className)}
    >
      <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
        {disagree ? (
          <StatusPill
            status="adjusted"
            tone="warn"
            label="Measures disagree"
            title="The evaluator's counts and the generator's landing repeat share differ: no adjusted reading is shown"
            size={size}
          />
        ) : mark.kind === "adjusted" ? (
          <StatusPill status="adjusted" tone={mark.tone} label={mark.label} title={mark.long} size={size} />
        ) : (
          <Badge variant="outline" title={mark.long} className={size === "sm" ? "px-1.5 py-px text-[11px]" : undefined}>
            {mark.label}
            <span className="sr-only"> ({mark.long})</span>
          </Badge>
        )}
        {hint ? <InfoHint concept="eval:model-adjusted" /> : null}
      </span>
      {replaces && evaluator ? (
        <span data-slot="evaluator-status" className="font-mono text-[11px] whitespace-nowrap text-text-3">
          {evaluator}
        </span>
      ) : null}
    </span>
  );
}
