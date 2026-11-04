/**
 * How an ADR 0038 verdict reads on screen, for every tab that shows one
 * (EVALUATION's run view, CONFIG's guardrails): the pill it becomes, and the
 * two shares and their difference in words.
 *
 * ADR 0038: a launch that measured a declared `pk:` repeating in the source
 * dropped that key from the effective model and copied the repetition; the
 * generator then compares the landing repeat share with the source's.
 */
import type { AdjustmentVerdict } from "@contracts/adjustments";

import { isFiniteNumber, MISSING } from "./format";

/**
 * The generator's tolerance on |landing − source| repeat share: `repeat_share_tolerance` in
 * sdfb_core/config/model.py (config key `guardrails.repeat_share_tolerance`), the knob
 * `repeat_share_tolerance`. Held here as a constant so the EVALUATION tab does not bundle
 * knobs.json; adjustments.test.ts pins it to the knob's default.
 */
export const REPEAT_SHARE_TOLERANCE = 0.05;

export type AdjustmentMark =
  | { kind: "adjusted"; tone: "info" | "warn"; label: string; long: string }
  | { kind: "question"; tone: null; label: string; long: string };

/**
 * ADR 0038 status mapping. An adjusted key whose copy is faithful is information, not a defect;
 * a copy that missed is a warning in the direction the generator measured; a key whose adjustment
 * cannot be read (no share, or no row) is a question, and the evaluator's status stands.
 */
const MARKS: Record<AdjustmentVerdict, AdjustmentMark | null> = {
  not_adjusted: null,
  faithful: {
    kind: "adjusted",
    tone: "info",
    label: "Adjusted · faithful",
    long: "The launch adjusted this key (ADR 0038) and the synthetic key repeats as often as the source's",
  },
  under: {
    kind: "adjusted",
    tone: "warn",
    label: "Adjusted · under",
    long: "The launch adjusted this key (ADR 0038) and the synthetic key repeats less than the source's: under-reproduced",
  },
  over: {
    kind: "adjusted",
    tone: "warn",
    label: "Adjusted · over",
    long: "The launch adjusted this key (ADR 0038) and the synthetic key repeats more than the source's: over-reproduced",
  },
  unmeasured: {
    kind: "question",
    tone: null,
    label: "Adjusted?",
    long: "The launch adjusted this key (ADR 0038) but measured no source repeat share, so the copy cannot be judged",
  },
  unknown: {
    kind: "question",
    tone: null,
    label: "Adjusted?",
    long: "No validation_runs row could be joined to this table, so whether the launch adjusted its key (ADR 0038) is not known",
  },
};

/** The mark a verdict shows; null for a key the launch did not adjust (the normal rule applies). */
export function verdictMark(verdict: AdjustmentVerdict): AdjustmentMark | null {
  return MARKS[verdict] ?? null;
}

/** The words a verdict takes in a sentence or a count ("2 under-reproduced"). */
export const VERDICT_WORD: Record<AdjustmentVerdict, string> = {
  not_adjusted: "not adjusted",
  faithful: "faithful",
  under: "under-reproduced",
  over: "over-reproduced",
  unmeasured: "unmeasured",
  unknown: "unknown",
};

/** A repeat share with one decimal ("50.3%", "42.0%"): two shares side by side compare digit for digit. */
export function fmtRepeatShare(share: number | null | undefined): string {
  return isFiniteNumber(share) ? `${(share * 100).toFixed(1)}%` : MISSING;
}

/** A difference of two shares in percentage points, signed: −0.2419 → "−24.2 pt". */
export function fmtPoints(delta: number | null | undefined): string {
  if (!isFiniteNumber(delta)) return MISSING;
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
  return `${sign}${Math.abs(delta * 100).toFixed(1)} pt`;
}

/** "±5 pt": the tolerance the generator judged the copy against. */
export function fmtTolerance(tolerance: number = REPEAT_SHARE_TOLERANCE): string {
  return `±${Number((tolerance * 100).toFixed(2))} pt`;
}
