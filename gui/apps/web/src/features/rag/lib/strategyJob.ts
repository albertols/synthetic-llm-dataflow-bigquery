/**
 * Every strategy's seeds on one cloud, with their coverage / diversity /
 * redundancy, and the k-center walk with its frontier — the retrieval
 * simulator's numbers, computed off the main thread (projection.worker.ts)
 * with the same exact ports (`runStrategy`, `strategyWalk`).
 */
import { seedMetrics, type SeedMetrics } from "./metrics";
import { runStrategy, strategyWalk, STRATEGY_IDS, type StrategyId } from "./strategies";
import { norms, rowsOf } from "./vectors";

export interface StrategyRowData extends SeedMetrics {
  id: StrategyId;
  picks: number[];
}

export interface WalkData {
  strategy: "kcenter" | "kcenter_rotate";
  attempt: number;
  steps: { pick: number; distance: number }[];
  /** steps × n: squared distance of every item to its nearest pick among the first t + 1 picks. */
  frontier: Float32Array;
}

export interface StrategyJobResult {
  rows: StrategyRowData[];
  walk: WalkData;
}

export function computeStrategies(
  vectors: Float32Array,
  dim: number,
  k: number,
  attempt: number,
  walkStrategy: "kcenter" | "kcenter_rotate",
): StrategyJobResult {
  const rows = rowsOf(vectors, dim);
  const rowNorms = norms(rows);
  const out = STRATEGY_IDS.map((id) => {
    const picks = runStrategy(id, rows, k, { attempt });
    return { id, picks, ...seedMetrics(rows, picks, rowNorms) };
  });
  const steps = strategyWalk(walkStrategy, rows, k, attempt);
  const n = rows.length;
  const frontier = new Float32Array(steps.length * n);
  const best = new Float64Array(n).fill(Infinity);
  steps.forEach((step, t) => {
    const o = step.pick * dim;
    for (let i = 0; i < n; i += 1) {
      const oi = i * dim;
      let d = 0;
      for (let j = 0; j < dim; j += 1) {
        const x = vectors[oi + j]! - vectors[o + j]!;
        d += x * x;
      }
      if (d < best[i]!) best[i] = d;
      frontier[t * n + i] = best[i]!;
    }
  });
  return { rows: out, walk: { strategy: walkStrategy, attempt, steps, frontier } };
}
