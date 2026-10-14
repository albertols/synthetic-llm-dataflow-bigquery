/**
 * Noise floors and intervals: the GUI's mirror of the evaluator's
 * `sdfb_evaluation.stats.noise` (plan Task 5). Every function cites its source;
 * none returns a p-value (decision D5: effect sizes against noise floors).
 */
import { betaQuantile, normalCdf, normalQuantile } from "./special";

export const Z95 = 1.959964;

/**
 * DKW(-Massart) band half-width: sup |F̂_n − F| ≤ ε with probability 1 − α,
 * ε = √(ln(2/α) / 2n). Massart 1990, https://doi.org/10.1214/aop/1176990746.
 * n = 10,000 → 0.01358.
 */
export function dkwEpsilon(n: number, alpha = 0.05): number {
  if (n <= 0) return Infinity;
  return Math.sqrt(Math.log(2 / alpha) / (2 * n));
}

/**
 * Two-sample KS critical value: c(α)·√((n + m)/(n·m)), c(α) = √(−ln(α/2)/2)
 * (1.358 at α = 0.05). Smirnov 1948, https://doi.org/10.1214/aoms/1177730256.
 */
export function ksCritical(n: number, m: number, alpha = 0.05): number {
  if (n <= 0 || m <= 0) return Infinity;
  return Math.sqrt(-Math.log(alpha / 2) / 2) * Math.sqrt((n + m) / (n * m));
}

/**
 * Wilson score interval for k successes in n (Wilson 1927, https://doi.org/10.1080/01621459.1927.10502953).
 * k = 0 and k = n return the exact bounds 0 and 1, as the evaluator's `wilson_interval` does, so
 * an interval metric at its edge reference (a share of 1, a rate of 0) reads as covering it exactly.
 */
export function wilson(k: number, n: number, z = Z95): [number, number] {
  if (n <= 0) return [0, 1];
  const p = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = (p + z2 / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom;
  return [k <= 0 ? 0 : Math.max(0, center - half), k >= n ? 1 : Math.min(1, center + half)];
}

/**
 * The interval for |X| from a two-sided interval (lo, hi) for a signed X (the evaluator's
 * `_folded_abs_interval`): (0, max(|lo|, |hi|)) when it straddles 0, else (min, max) of the
 * absolute ends. How an absolute-difference metric carries its Newcombe CI (Ruling R41).
 */
export function foldedAbsInterval(lo: number, hi: number): [number, number] {
  if (lo <= 0 && 0 <= hi) return [0, Math.max(Math.abs(lo), Math.abs(hi))];
  return [Math.min(Math.abs(lo), Math.abs(hi)), Math.max(Math.abs(lo), Math.abs(hi))];
}

/**
 * Newcombe's hybrid score interval for p1 − p2 (method 10; Newcombe 1998,
 * https://doi.org/10.1002/(SICI)1097-0258(19980430)17:8%3C873::AID-SIM779%3E3.0.CO;2-I).
 */
export function newcombe(k1: number, n1: number, k2: number, n2: number, z = Z95): [number, number] {
  const p1 = n1 > 0 ? k1 / n1 : 0;
  const p2 = n2 > 0 ? k2 / n2 : 0;
  const [l1, u1] = wilson(k1, n1, z);
  const [l2, u2] = wilson(k2, n2, z);
  const d = p1 - p2;
  return [d - Math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + Math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)];
}

/**
 * Expected TVD between two samples of the SAME distribution p (sizes n, m):
 * ½ Σ √(2 p(1−p)(1/n + 1/m) / π) — the half-normal mean of each cell's difference.
 */
export function tvdNullExpectation(p: readonly number[], n: number, m: number): number {
  const total = p.reduce((a, b) => a + b, 0) || 1;
  const scale = 1 / n + 1 / m;
  return (
    0.5 *
    p.reduce((acc, raw) => {
      const q = raw / total;
      return acc + Math.sqrt((2 * q * (1 - q) * scale) / Math.PI);
    }, 0)
  );
}

/** Expected JSD (bits) under the null for k categories: (k − 1)(1/n + 1/m) / (8 ln 2) (the χ² bias of the plug-in). */
export function jsdNullExpectationBits(k: number, n: number, m: number): number {
  if (k <= 1) return 0;
  return ((k - 1) * (1 / n + 1 / m)) / (8 * Math.LN2);
}

/** Δr attributable to noise at r ≈ 0: tanh(z·√(1/(n−3) + 1/(m−3))) (Fisher 1915 z-transform). */
export function fisherZDeltaFloor(n: number, m: number, z = Z95): number {
  if (n <= 3 || m <= 3) return Infinity;
  return Math.tanh(z * Math.sqrt(1 / (n - 3) + 1 / (m - 3)));
}

/** Plug-in mutual-information bias, nats: (r−1)(c−1)/(2n) (Treves & Panzeri 1995, https://doi.org/10.1162/neco.1995.7.2.399). */
export function miBiasNats(r: number, c: number, n: number): number {
  if (n <= 0) return Infinity;
  return ((r - 1) * (c - 1)) / (2 * n);
}

export interface RateRatio {
  /** (m1/t1)/(m2/t2); null when m2 = 0. */
  ratio: number | null;
  lo: number;
  hi: number;
}

/**
 * The conditional rate-ratio test: m1 | m1 + m2 ~ Bin(m1 + m2, t1/(t1 + t2)), a
 * Clopper–Pearson interval on that proportion mapped to (π/(1−π))·(t2/t1).
 * The evaluator's lifts (memorization, exposure, near-match) use it.
 */
export function rateRatio(
  m1: number,
  t1: number,
  m2: number,
  t2: number,
  alpha = 0.05,
  options: { zeroCorrection?: boolean } = {},
): RateRatio {
  const total = m1 + m2;
  if (total === 0 || t1 <= 0 || t2 <= 0) return { ratio: null, lo: 0, hi: Infinity };
  // With m2 = 0 the point estimate is infinite; `zeroCorrection` reports the
  // Haldane–Anscombe (+½) estimate instead. The interval is exact either way.
  const ratio = m2 > 0 ? m1 / t1 / (m2 / t2) : options.zeroCorrection ? (m1 + 0.5) / t1 / ((m2 + 0.5) / t2) : null;
  const piLo = m1 === 0 ? 0 : betaQuantile(alpha / 2, m1, m2 + 1);
  const piHi = m2 === 0 ? 1 : betaQuantile(1 - alpha / 2, m1 + 1, m2);
  const map = (pi: number) => (pi >= 1 ? Infinity : (pi / (1 - pi)) * (t2 / t1));
  return { ratio, lo: map(piLo), hi: map(piHi) };
}

/**
 * AUC confidence interval, Hanley & McNeil 1982 (https://doi.org/10.1148/radiology.143.1.7063747):
 * an analytic stand-in for DeLong's interval when the per-row scores are not at hand.
 */
export function aucInterval(auc: number, nPositive: number, nNegative: number, z = Z95): [number, number] {
  const q1 = auc / (2 - auc);
  const q2 = (2 * auc * auc) / (1 + auc);
  const variance =
    (auc * (1 - auc) + (nPositive - 1) * (q1 - auc * auc) + (nNegative - 1) * (q2 - auc * auc)) /
    (nPositive * nNegative);
  const half = z * Math.sqrt(Math.max(variance, 0));
  return [Math.max(0, auc - half), Math.min(1, auc + half)];
}

/** Binormal ROC curve with the given AUC (equal variances): TPR = Φ(√2·Φ⁻¹(AUC) + Φ⁻¹(FPR)). */
export function binormalRoc(auc: number, points = 21): [number, number][] {
  const a = Math.SQRT2 * normalQuantile(Math.min(Math.max(auc, 1e-6), 1 - 1e-6));
  return Array.from({ length: points }, (_, i) => {
    const fpr = i / (points - 1);
    if (fpr === 0) return [0, 0] as [number, number];
    if (fpr === 1) return [1, 1] as [number, number];
    return [fpr, normalCdf(a + normalQuantile(fpr))] as [number, number];
  });
}

export type NoiseMethod =
  "ks_two_sample" | "wilson" | "newcombe" | "tvd_null" | "jsd_null" | "fisher_z" | "mi_bias" | "rate_ratio" | "delong";

export interface NoiseInputs {
  n?: number;
  m?: number;
  /** Source distribution (tvd_null) or its category count (jsd_null). */
  p?: readonly number[];
  k?: number;
  /** Two proportions (newcombe). */
  k1?: number;
  k2?: number;
  /** Contingency shape (mi_bias). */
  rows?: number;
  cols?: number;
  /** AUC and its CI (delong: the half-width). */
  ciLow?: number;
  ciHigh?: number;
}

/**
 * The noise floor of a metric row: the smallest |value − noise reference| that
 * is not explained by sampling alone. null when the inputs are missing
 * (rate_ratio metrics read their CI bound instead of a floor).
 */
/** `method` is a NoiseMethod name (or the catalogue's "none"). */
export function noiseFloor(method: string | null, input: NoiseInputs): number | null {
  const { n = 0, m = 0 } = input;
  switch (method) {
    case "ks_two_sample":
      return n > 0 && m > 0 ? ksCritical(n, m) : null;
    case "wilson":
      // The largest rate still consistent with zero events at this n.
      return n > 0 ? wilson(0, n)[1] : null;
    case "newcombe": {
      if (!(n > 0 && m > 0)) return null;
      const [lo, hi] = newcombe(input.k1 ?? 0, n, input.k2 ?? 0, m);
      return (hi - lo) / 2;
    }
    case "tvd_null":
      return input.p && n > 0 && m > 0 ? tvdNullExpectation(input.p, n, m) : null;
    case "jsd_null":
      return input.k && n > 0 && m > 0 ? jsdNullExpectationBits(input.k, n, m) : null;
    case "fisher_z":
      return n > 3 && m > 3 ? fisherZDeltaFloor(n, m) : null;
    case "mi_bias":
      return input.rows && input.cols && n > 0 ? miBiasNats(input.rows, input.cols, n) / Math.LN2 : null;
    case "delong":
      return input.ciLow !== undefined && input.ciHigh !== undefined ? (input.ciHigh - input.ciLow) / 2 : null;
    default:
      return null;
  }
}
