/**
 * TEACHING-ONLY retrieval contrasts. Neither of these runs anywhere in the
 * pipeline; the RAG tab shows them next to the real strategies (centroid,
 * kcenter, kcenter_rotate in `retrieval.ts`) and must label them
 * "teaching contrast — not in pipeline".
 *
 * - `mmr`: Maximal Marginal Relevance (Carbonell & Goldstein 1998,
 *   https://doi.org/10.1145/290941.291025): relevance to the centroid traded
 *   against similarity to the picks so far.
 * - `randomPick`: k indices uniformly at random (seeded).
 */
import { Random } from "./rng";
import { centroid, type Vector } from "./retrieval";

/** The teaching-only functions, for labels and tests. */
export const TEACHING_ONLY = ["mmr", "randomPick"] as const;

function cosine(a: Vector, b: Vector): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i += 1) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

/** Teaching only. `lambda` = 1 is pure relevance (≈ centroid top-k), 0 pure diversity. */
export function mmr(vectors: readonly Vector[], k: number, lambda = 0.5): number[] {
  const n = vectors.length;
  if (!n || k <= 0) return [];
  const c = centroid(vectors);
  const relevance = vectors.map((v) => cosine(v, c));
  const chosen: number[] = [];
  const maxSim = new Array<number>(n).fill(-Infinity);
  while (chosen.length < Math.min(k, n)) {
    let best = -1;
    let bestScore = -Infinity;
    for (let i = 0; i < n; i += 1) {
      if (chosen.includes(i)) continue;
      const redundancy = chosen.length ? maxSim[i]! : 0;
      const score = lambda * relevance[i]! - (1 - lambda) * redundancy;
      if (score > bestScore) {
        bestScore = score;
        best = i;
      }
    }
    chosen.push(best);
    for (let i = 0; i < n; i += 1) maxSim[i] = Math.max(maxSim[i]!, cosine(vectors[i]!, vectors[best]!));
  }
  return chosen;
}

/** Teaching only. k distinct indices out of n, seeded. */
export function randomPick(n: number, k: number, seed = 0): number[] {
  const indices = Array.from({ length: n }, (_, i) => i);
  return new Random(seed).shuffle(indices).slice(0, Math.max(0, Math.min(k, n)));
}
