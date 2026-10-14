/**
 * Free-text pool arithmetic, from the code (b1_rag/engine.py), not the docs.
 *
 * - target = min(num_rows, column_distinct, FREE_TEXT_POOL_MAX), unknown (0)
 *   bounds skipped, at least 1 (`_pool_target`).
 * - The ladder's call budget: max(len(levels), 2·⌈target / (32 · n)⌉), with
 *   32 values per completion, n = 4 parallel completions, 3 sampling levels
 *   (`_pool_llm_yield`).
 * - A pool row carries no route. It is inferred: a binary-class pool (≥ 50 %
 *   of values carry control characters, `is_binary_class`) came from the
 *   template fallback, which never records attempts; attempts > 0 is the LLM
 *   ladder; attempts = 0 otherwise means the ladder never ran a round (the
 *   lax exemplar fallback). A free-text column with value chunks but no pool
 *   row drew from shape-mix expansion or a clause sampler (Tier P/B).
 */
import { poolReuse } from "@synthetic-platform/stats";

export const POOL_VALUES_PER_CALL = 32;
export const POOL_PARALLEL_CHOICES = 4;
export const SAMPLING_LEVELS = 3;
/** `is_binary_class` defaults (text_shapes.py). */
export const BINARY_MIN_SHARE = 0.5;
export const BINARY_MIN_VALUES = 2;

export function poolTarget(numRows: number, distinct: number, cap: number): number {
  const bounds = [cap];
  if (numRows > 0) bounds.push(numRows);
  if (distinct > 0) bounds.push(distinct);
  return Math.max(Math.min(...bounds), 1);
}

/** The most LLM calls one column's ladder may spend today. */
export function callBudget(
  target: number,
  perCall = POOL_VALUES_PER_CALL,
  choices = POOL_PARALLEL_CHOICES,
  levels = SAMPLING_LEVELS,
): number {
  const perRound = perCall * Math.max(1, choices);
  return Math.max(levels, 2 * Math.ceil(target / perRound));
}

/** Rounds needed if every value of every completion were novel: ⌈target / (32 · 4)⌉. */
export function idealRounds(target: number, perCall = POOL_VALUES_PER_CALL, choices = POOL_PARALLEL_CHOICES) {
  return Math.ceil(target / (perCall * Math.max(1, choices)));
}

function isControl(code: number): boolean {
  return (code < 32 && code !== 9 && code !== 10 && code !== 13) || (code >= 127 && code < 160);
}

/** Port of `is_binary_class`: most non-empty values carry a control character. */
export function isBinaryClass(values: readonly string[], minShare = BINARY_MIN_SHARE): boolean {
  const vals = values.filter((v) => v);
  if (vals.length < BINARY_MIN_VALUES) return false;
  let hits = 0;
  for (const v of vals) {
    for (const char of v)
      if (isControl(char.codePointAt(0)!)) {
        hits += 1;
        break;
      }
  }
  return hits / vals.length >= minShare;
}

export type PoolRoute = "llm_ladder" | "binary_fallback" | "no_rounds" | "no_pool";

export const ROUTE_LABELS: Record<PoolRoute, string> = {
  llm_ladder: "LLM ladder",
  binary_fallback: "Binary fallback",
  no_rounds: "No LLM rounds",
  no_pool: "Expandable or routed · no pool",
};

export function inferPoolRoute(pool: { values: readonly string[]; attempts: number } | null): PoolRoute {
  if (!pool) return "no_pool";
  if (isBinaryClass(pool.values)) return "binary_fallback";
  return pool.attempts > 0 ? "llm_ladder" : "no_rounds";
}

/** Each pooled value's expected repeats when `rows` rows draw uniformly from it. */
export function reuseAt(rows: number, poolSize: number): number | null {
  const value = poolReuse(rows, poolSize);
  return Number.isFinite(value) ? value : null;
}
