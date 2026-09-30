/**
 * Empirical distributions: ECDF, quantiles, bin counts and the evaluator's exact
 * binned-CDF maths (plan Task 6): the KS bracket, W1 from bins and PIT-W1.
 *
 * Bins follow the evaluator's `bin_counts`: interior `edges` e0 < … < e_last give
 * `edges.length + 1` bins (−∞, e0], (e0, e1], …, (e_last, +∞) (numpy
 * `searchsorted(edges, x, side="left")`); NaN is excluded.
 */
import { normalize } from "./distances";

export interface Ecdf {
  /** Sorted values. */
  x: number[];
  /** F at each sorted value, i/n (right-continuous). */
  y: number[];
  /** F(t) = #{x ≤ t} / n. */
  at(t: number): number;
}

export function ecdf(values: readonly number[]): Ecdf {
  const x = values.filter((v) => !Number.isNaN(v)).sort((a, b) => a - b);
  const n = x.length;
  const y = x.map((_, i) => (i + 1) / n);
  return {
    x,
    y,
    at(t: number) {
      let lo = 0;
      let hi = n;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (x[mid]! <= t) lo = mid + 1;
        else hi = mid;
      }
      return n ? lo / n : 0;
    },
  };
}

/** Quantiles with linear interpolation between order statistics (numpy's default, Hyndman–Fan type 7). */
export function quantiles(values: readonly number[], probs: readonly number[]): number[] {
  const s = values.filter((v) => !Number.isNaN(v)).sort((a, b) => a - b);
  if (!s.length) return probs.map(() => Number.NaN);
  return probs.map((p) => {
    const h = (s.length - 1) * Math.min(Math.max(p, 0), 1);
    const lo = Math.floor(h);
    const hi = Math.min(lo + 1, s.length - 1);
    return s[lo]! + (h - lo) * (s[hi]! - s[lo]!);
  });
}

/** Python's round(): half to even. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : r;
}

/** The profiler's 11-point deciles: ordered[round(i·(n−1)/10)], i = 0..10 (`source_stats._add_numeric`). */
export function profilerDeciles(values: readonly number[]): number[] {
  const s = values.filter((v) => !Number.isNaN(v)).sort((a, b) => a - b);
  if (!s.length) return [];
  return Array.from({ length: 11 }, (_, i) => s[pyRound((i * (s.length - 1)) / 10)]!);
}

/** Bin counts on interior edges: edges.length + 1 bins, x ≤ e0 in bin 0. */
export function histogram(values: Iterable<number>, edges: readonly number[]): number[] {
  const counts = new Array<number>(edges.length + 1).fill(0);
  for (const x of values) {
    if (Number.isNaN(x)) continue;
    let lo = 0;
    let hi = edges.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (edges[mid]! < x) lo = mid + 1;
      else hi = mid;
    }
    counts[lo]! += 1;
  }
  return counts;
}

/** Equiprobable interior edges from source quantiles (the evaluator's `profile_edges`), deduplicated. */
export function equiprobableEdges(values: readonly number[], bins: number): number[] {
  const probs = Array.from({ length: bins - 1 }, (_, i) => (i + 1) / bins);
  return [...new Set(quantiles(values, probs))];
}

function cumulative(p: readonly number[]): number[] {
  let acc = 0;
  return p.map((v) => (acc += v));
}

export interface KsBracket {
  /** max over edges of |F_s − F_y| — a lower bound on the true KS distance. */
  dLo: number;
  /** max over bins of the largest gap the within-bin mass could hide — an upper bound. */
  dHi: number;
}

/** Exact two-sided bracket on the KS distance from bin counts (plan Task 6). null when a side is empty. */
export function ksBracket(countsSource: readonly number[], countsSynthetic: readonly number[]): KsBracket | null {
  if (countsSource.length !== countsSynthetic.length) throw new Error("bin count mismatch");
  if (!countsSource.some((c) => c > 0) || !countsSynthetic.some((c) => c > 0)) return null;
  const fs = cumulative(normalize(countsSource));
  const fy = cumulative(normalize(countsSynthetic));
  let dLo = 0;
  for (let i = 0; i < fs.length - 1; i += 1) dLo = Math.max(dLo, Math.abs(fs[i]! - fy[i]!));
  let dHi = 0;
  for (let b = 0; b < fs.length; b += 1) {
    const sLo = b === 0 ? 0 : fs[b - 1]!;
    const yLo = b === 0 ? 0 : fy[b - 1]!;
    const sHi = b === fs.length - 1 ? 1 : fs[b]!;
    const yHi = b === fy.length - 1 ? 1 : fy[b]!;
    dHi = Math.max(dHi, sHi - yLo, yHi - sLo);
  }
  return { dLo, dHi: Math.max(dHi, dLo) };
}

/** ∫|F_s − F_y| dx over [e0, e_last] with the CDFs constant between edges (column units). */
export function w1FromBins(
  edges: readonly number[],
  countsSource: readonly number[],
  countsSynthetic: readonly number[],
): number | null {
  if (!countsSource.some((c) => c > 0) || !countsSynthetic.some((c) => c > 0)) return null;
  const fs = cumulative(normalize(countsSource));
  const fy = cumulative(normalize(countsSynthetic));
  let sum = 0;
  for (let i = 0; i < edges.length - 1; i += 1) sum += Math.abs(fs[i]! - fy[i]!) * (edges[i + 1]! - edges[i]!);
  return sum;
}

/**
 * PIT Wasserstein-1 ∈ [0, ½]: Σ_b p_s(b)·|F̄_y(b) − F̄_s(b)| with the mid-distribution
 * CDF F̄(b) = cumsum(p)(b) − p(b)/2 over ALL bins, the open last one included
 * (Parzen's mid-CDF; Czado, Gneiting & Held 2009, https://doi.org/10.1111/j.1541-0420.2009.01191.x).
 * The evaluator's Ruling R16.
 */
export function pitW1(countsSource: readonly number[], countsSynthetic: readonly number[]): number | null {
  if (!countsSource.some((c) => c > 0) || !countsSynthetic.some((c) => c > 0)) return null;
  const ps = normalize(countsSource);
  const py = normalize(countsSynthetic);
  const fs = cumulative(ps);
  const fy = cumulative(py);
  let sum = 0;
  for (let b = 0; b < ps.length; b += 1) {
    const midS = fs[b]! - ps[b]! / 2;
    const midY = fy[b]! - py[b]! / 2;
    sum += ps[b]! * Math.abs(midY - midS);
  }
  return sum;
}

/** Quantiles read back from bins, linear inside each bin; the open tails use `min` / `max`. */
export function quantilesFromBins(
  edges: readonly number[],
  counts: readonly number[],
  probs: readonly number[],
  bounds: { min: number; max: number },
): number[] {
  const total = counts.reduce((a, b) => a + b, 0);
  if (total <= 0) return probs.map(() => Number.NaN);
  const lows = [bounds.min, ...edges];
  const highs = [...edges, bounds.max];
  return probs.map((p) => {
    let acc = 0;
    for (let b = 0; b < counts.length; b += 1) {
      const share = counts[b]! / total;
      if (acc + share >= p && share > 0) {
        const within = (p - acc) / share;
        return lows[b]! + within * (highs[b]! - lows[b]!);
      }
      acc += share;
    }
    return bounds.max;
  });
}

/**
 * The legacy decile KS (VERBATIM semantics of scripts/e2e/source_synthetic_stats_diff.py
 * `decile_ks`): two piecewise-linear CDFs built from 11-point decile vectors, compared
 * on the union grid (numpy.interp: flat outside the knots, the last knot wins on ties).
 */
export function decileKsLegacy(a: readonly number[], b: readonly number[]): number {
  if (!a.length || !b.length) return 0;
  const interp = (x: number, xp: readonly number[]) => {
    const n = xp.length;
    const fp = (i: number) => (n === 1 ? 0 : i / (n - 1));
    if (x < xp[0]!) return fp(0);
    if (x >= xp[n - 1]!) return fp(n - 1);
    let j = 0;
    while (j + 1 < n && xp[j + 1]! <= x) j += 1;
    const x0 = xp[j]!;
    const x1 = xp[j + 1]!;
    return x1 === x0 ? fp(j) : fp(j) + ((x - x0) / (x1 - x0)) * (fp(j + 1) - fp(j));
  };
  const grid = [...new Set([...a, ...b])].sort((x, y) => x - y);
  return Math.max(...grid.map((x) => Math.abs(interp(x, a) - interp(x, b))));
}
