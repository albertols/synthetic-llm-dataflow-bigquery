/**
 * How good a set of seeds is, measured in the full 384-d space (never on the
 * projection), with cosine distance d(a, b) = 1 − cos(a, b):
 *
 * - coverage   = mean over every item of d(item, nearest seed) — lower is
 *                better; `radius` is the max (the k-center objective).
 * - diversity  = mean over seed pairs of d(s_i, s_j) — higher is better.
 * - redundancy = mean over seeds of max_{j≠i} cos(s_i, s_j) — how close each
 *                seed sits to its nearest fellow seed; 1 = duplicates.
 */
import { mulberry32 } from "@synthetic-platform/stats";

import { cosineWith, norms, type Flat } from "./vectors";

export interface SeedMetrics {
  seeds: number;
  coverage: number | null;
  radius: number | null;
  diversity: number | null;
  redundancy: number | null;
}

export function seedMetrics(rows: readonly Flat[], picks: readonly number[], rowNorms = norms(rows)): SeedMetrics {
  const seeds = [...new Set(picks)].filter((i) => i >= 0 && i < rows.length);
  if (!rows.length || !seeds.length)
    return { seeds: seeds.length, coverage: null, radius: null, diversity: null, redundancy: null };
  let total = 0;
  let radius = 0;
  rows.forEach((row, i) => {
    let best = Infinity;
    for (const s of seeds) {
      const d = 1 - cosineWith(row, rowNorms[i]!, rows[s]!, rowNorms[s]!);
      if (d < best) best = d;
    }
    total += best;
    if (best > radius) radius = best;
  });
  let pairSum = 0;
  let pairs = 0;
  const nearest = new Array<number>(seeds.length).fill(-Infinity);
  for (let a = 0; a < seeds.length; a += 1)
    for (let b = a + 1; b < seeds.length; b += 1) {
      const sa = seeds[a]!;
      const sb = seeds[b]!;
      const c = cosineWith(rows[sa]!, rowNorms[sa]!, rows[sb]!, rowNorms[sb]!);
      pairSum += 1 - c;
      pairs += 1;
      if (c > nearest[a]!) nearest[a] = c;
      if (c > nearest[b]!) nearest[b] = c;
    }
  return {
    seeds: seeds.length,
    coverage: clampZero(total / rows.length),
    radius: clampZero(radius),
    diversity: pairs ? pairSum / pairs : null,
    redundancy: seeds.length > 1 ? nearest.reduce((a, b) => a + b, 0) / seeds.length : null,
  };
}

/** Rounding can leave −1e-16 where the distance is 0 (a seed is its own nearest seed). */
function clampZero(value: number): number {
  return Math.abs(value) < 1e-12 ? 0 : value;
}

/**
 * Mean pairwise cosine of a subset (a lasso selection), exact up to
 * `maxPairs` pairs and estimated from `maxPairs` seeded random pairs above
 * that. Returns the number of pairs used and whether it is exact.
 */
export function meanPairwiseCosine(
  rows: readonly Flat[],
  indices: readonly number[],
  rowNorms = norms(rows),
  maxPairs = 60_000,
): { mean: number; pairs: number; exact: boolean } | null {
  const n = indices.length;
  if (n < 2) return null;
  const all = (n * (n - 1)) / 2;
  let sum = 0;
  if (all <= maxPairs) {
    for (let a = 0; a < n; a += 1)
      for (let b = a + 1; b < n; b += 1) {
        const i = indices[a]!;
        const j = indices[b]!;
        sum += cosineWith(rows[i]!, rowNorms[i]!, rows[j]!, rowNorms[j]!);
      }
    return { mean: sum / all, pairs: all, exact: true };
  }
  const rand = mulberry32(20260928);
  for (let t = 0; t < maxPairs; t += 1) {
    const a = Math.floor(rand() * n);
    let b = Math.floor(rand() * (n - 1));
    if (b >= a) b += 1;
    const i = indices[a]!;
    const j = indices[b]!;
    sum += cosineWith(rows[i]!, rowNorms[i]!, rows[j]!, rowNorms[j]!);
  }
  return { mean: sum / maxPairs, pairs: maxPairs, exact: false };
}

/** How many distinct cluster labels the seeds land in. */
export function clustersReached(picks: readonly number[], labels: ArrayLike<number> | null): number | null {
  if (!labels) return null;
  return new Set(picks.map((i) => labels[i]).filter((l) => l !== undefined && l >= 0)).size;
}

/** The ±1/√d band two unrelated hashing-embedder texts fall in (one standard deviation). */
export function hashingNoise(dim: number): number {
  return dim > 0 ? 1 / Math.sqrt(dim) : 0;
}
