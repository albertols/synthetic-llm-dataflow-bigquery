/**
 * Scale arithmetic for the CONFIG scenario calculator: what a reference sample
 * of n rows can and cannot see, and what M generated rows do to a pool or a key space.
 */
import { logChoose } from "./special";

/** P(a category of share p appears at least once in n draws) = 1 − (1 − p)^n. */
export function rareCaptureProb(p: number, n: number): number {
  if (p <= 0 || n <= 0) return 0;
  if (p >= 1) return 1;
  return -Math.expm1(n * Math.log1p(-p));
}

/** Expected sample points above the q-quantile: n·(1 − q). */
export function tailPoints(n: number, q: number): number {
  return n * (1 - q);
}

/** P(at least one collision) among n uniform draws from `space` values ≈ 1 − exp(−n(n−1)/2·space). */
export function birthdayCollisionProb(n: number, space: number): number {
  if (n <= 1 || space <= 0) return 0;
  return -Math.expm1((-n * (n - 1)) / (2 * space));
}

/** Expected number of colliding pairs: C(n, 2)/space. */
export function expectedCollisions(n: number, space: number): number {
  return space > 0 ? (n * (n - 1)) / (2 * space) : Infinity;
}

/**
 * Rarefaction (Hurlbert 1971, https://doi.org/10.2307/1934145): expected distinct
 * values in a sample of m drawn without replacement from a population with the
 * given per-value counts. Σ_i [1 − C(N − N_i, m) / C(N, m)].
 */
export function rarefaction(counts: readonly number[], m: number): number {
  const total = counts.reduce((a, b) => a + b, 0);
  if (m <= 0 || total <= 0) return 0;
  if (m >= total) return counts.filter((c) => c > 0).length;
  const denom = logChoose(total, m);
  let expected = 0;
  for (const c of counts) {
    if (c <= 0) continue;
    expected += total - c < m ? 1 : 1 - Math.exp(logChoose(total - c, m) - denom);
  }
  return expected;
}

/** Rows per distinct value when M rows are drawn from a pool of `poolSize` values (the 512 free-text cap). */
export function poolReuse(rows: number, poolSize: number): number {
  return poolSize > 0 ? rows / poolSize : Infinity;
}
