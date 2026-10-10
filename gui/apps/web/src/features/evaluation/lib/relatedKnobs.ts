/**
 * Which CONFIG knob drives an evaluation metric: the "Related knob" link in the
 * metric's (i). One table, and one rule taken from the catalogue itself; a link
 * appears only where the code makes the knob move the metric (no guesses).
 * `related-knobs.test.ts` checks every metric id against the catalogue and every
 * knob id against knobs.json.
 */
import type { KnobId } from "@contracts/generated/knobs";

import { metricMeta } from "./catalogue";

export type RelatedKnob = { knob: KnobId; why: string };

const POOL_CAP = "free_text_pool_max" satisfies KnobId;

const SIMILARITY_TEMPORAL: RelatedKnob = {
  knob: "similarity",
  why: "b1 blends temporal draws between observed instants and uniform over the range by this dial.",
};
const SIMILARITY_CATEGORICAL: RelatedKnob = {
  knob: "similarity",
  why: "b2 samples categories at temperature 2(1 − similarity): high collapses to the mode, low flattens the mix.",
};

/** Metric id → the knobs that drive it, with why (from the code the knob's sheet cites). */
export const RELATED_KNOBS: Readonly<Record<string, readonly RelatedKnob[]>> = {
  "column.distinct_ceiling_hit": [
    { knob: POOL_CAP, why: "The flag is 1 when a column's distinct count lands on this cap (or the pool target)." },
  ],
  "column.distinct_ratio": [
    { knob: POOL_CAP, why: "An LLM-pooled column holds at most this many values: the classic cause of a low ratio." },
  ],
  "column.entropy_ratio": [
    {
      knob: POOL_CAP,
      why: "An LLM-pooled column's entropy is capped by the pool size, log₂ of at most this many values.",
    },
  ],
  "column.dow_tvd": [SIMILARITY_TEMPORAL],
  "column.month_tvd": [SIMILARITY_TEMPORAL],
  "column.hour_tvd": [SIMILARITY_TEMPORAL],
  "column.tvd": [SIMILARITY_CATEGORICAL],
  "column.top1_share_delta": [SIMILARITY_CATEGORICAL],
};

/**
 * The rule: a catalogue metric with `baseline: true` stores baseline_value =
 * metric(R, source), where R is the reference sample the generator read —
 * reference_rows_limit rows. That floor is sampling noise, and it shrinks as n grows.
 */
export const REFERENCE_SAMPLE: RelatedKnob = {
  knob: "reference_rows_limit",
  why: "Its baseline is this metric on the reference sample itself: sampling noise that shrinks as the sample grows.",
};

/** The related knobs of a metric id (catalogue ids; unknown ids have none). */
export function relatedKnobs(metricId: string): readonly RelatedKnob[] {
  const explicit = RELATED_KNOBS[metricId] ?? [];
  return metricMeta(metricId)?.baseline ? [...explicit, REFERENCE_SAMPLE] : explicit;
}
