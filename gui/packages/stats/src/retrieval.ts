/**
 * Exact ports of `sdfb_core.rag.retrieval` over the pure-Python exact index
 * (`_PyExactIPIndex`): the same arithmetic in the same order (sequential sums
 * from 0, as CPython 3.11's `sum()` does), so picks match the goldens exactly.
 * Items are indices into `vectors`.
 *
 * - `centroidTopK`: the k vectors nearest the centroid by cosine (densest
 *   region); descending score, ascending index on ties. The pipeline's control arm.
 * - `kcenter`: greedy farthest-point walk (Gonzalez 1985), starting at the
 *   medoid (the vector nearest the centroid); ties break on the lower index;
 *   a chosen vector is never re-picked.
 * - `kcenterRotate`: the same walk started at `(attempt * k) mod n`.
 */
export type Vector = ArrayLike<number>;

export function centroid(vectors: readonly Vector[]): number[] {
  const dim = vectors[0]!.length;
  const total = new Array<number>(dim).fill(0);
  for (const vec of vectors) for (let j = 0; j < dim; j += 1) total[j]! += vec[j]!;
  return total.map((t) => t / vectors.length);
}

/** `_normalize`: divide by the L2 norm; a zero vector is returned unchanged. */
export function normalizePy(vec: Vector): number[] {
  let sum = 0;
  for (let i = 0; i < vec.length; i += 1) sum += vec[i]! * vec[i]!;
  const norm = Math.sqrt(sum);
  const out = Array.from(vec);
  return norm === 0 ? out : out.map((v) => v / norm);
}

function dotPy(a: Vector, b: Vector): number {
  const n = Math.min(a.length, b.length);
  let sum = 0;
  for (let i = 0; i < n; i += 1) sum += a[i]! * b[i]!;
  return sum;
}

export function sqDist(a: Vector, b: Vector): number {
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) {
    const d = a[i]! - b[i]!;
    sum += d * d;
  }
  return sum;
}

/** Cosine scores of every vector against the centroid, as the exact index computes them. */
export function centroidScores(vectors: readonly Vector[]): number[] {
  if (!vectors.length) return [];
  const query = normalizePy(centroid(vectors));
  return vectors.map((vec) => dotPy(normalizePy(vec), query));
}

export function centroidTopK(vectors: readonly Vector[], k: number): number[] {
  if (!vectors.length || k <= 0) return [];
  const scores = centroidScores(vectors);
  return scores
    .map((score, index) => ({ score, index }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .slice(0, k)
    .map((entry) => entry.index);
}

/** The medoid used as the default k-center start: argmin squared distance to the centroid (first on ties). */
export function medoidIndex(vectors: readonly Vector[]): number {
  const c = centroid(vectors);
  let best = 0;
  let bestDistance = Infinity;
  vectors.forEach((vec, i) => {
    const d = sqDist(vec, c);
    if (d < bestDistance) {
      bestDistance = d;
      best = i;
    }
  });
  return best;
}

export interface KcenterStep {
  pick: number;
  /** Squared distance of `pick` to its nearest earlier pick (0 for the start). */
  distance: number;
}

/** The greedy walk with each step's farthest distance (for the RAG tab's animation). */
export function kcenterWalk(vectors: readonly Vector[], k: number, start?: number): KcenterStep[] {
  const n = vectors.length;
  if (!n || k <= 0) return [];
  if (n <= k) return vectors.map((_, i) => ({ pick: i, distance: 0 }));
  let first = start ?? medoidIndex(vectors);
  first = ((first % n) + n) % n;
  const steps: KcenterStep[] = [{ pick: first, distance: 0 }];
  const best = vectors.map((vec) => sqDist(vec, vectors[first]!));
  best[first] = -1;
  for (let step = 0; step < k - 1; step += 1) {
    let next = 0;
    for (let i = 1; i < n; i += 1) if (best[i]! > best[next]!) next = i;
    steps.push({ pick: next, distance: best[next]! });
    best[next] = -1;
    for (let i = 0; i < n; i += 1) {
      if (best[i]! < 0) continue;
      const d = sqDist(vectors[i]!, vectors[next]!);
      if (d < best[i]!) best[i] = d;
    }
  }
  return steps;
}

export function kcenter(vectors: readonly Vector[], k: number, start?: number): number[] {
  return kcenterWalk(vectors, k, start).map((step) => step.pick);
}

export function kcenterRotate(vectors: readonly Vector[], k: number, attempt: number): number[] {
  const n = vectors.length;
  if (!n) return [];
  return kcenter(vectors, k, k > 0 ? (attempt * k) % n : 0);
}

export type SeedStrategy = "centroid" | "kcenter" | "kcenter_rotate";

/** `select_seed_examples`: the k prompt seeds of one ladder attempt; an unknown strategy falls back to centroid. */
export function selectSeedExamples(
  vectors: readonly Vector[],
  k: number,
  /** A SeedStrategy; anything else falls back to centroid. */
  strategy: string,
  attempt = 0,
): number[] {
  const n = vectors.length;
  if (!n || k <= 0) return [];
  if (n <= k) return vectors.map((_, i) => i);
  if (strategy === "kcenter") return kcenter(vectors, k);
  if (strategy === "kcenter_rotate") return kcenter(vectors, k, (attempt * k) % n);
  return centroidTopK(vectors, k);
}
