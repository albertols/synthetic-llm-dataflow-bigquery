/**
 * Small linear algebra for embedding clouds: cosine, PCA to 3-D by power
 * iteration, and k-NN preservation (how trustworthy a projection is).
 * Vectors are rows of a row-major Float32Array/number[] (n × dim).
 */
import { mulberry32 } from "./rng";

export type Rows = Float32Array | Float64Array | readonly number[];

export function cosine(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i += 1) {
    dot += a[i]! * b[i]!;
    na += a[i]! * a[i]!;
    nb += b[i]! * b[i]!;
  }
  return na > 0 && nb > 0 ? dot / Math.sqrt(na * nb) : 0;
}

export function row(data: Rows, dim: number, i: number): ArrayLike<number> {
  return Array.isArray(data)
    ? (data as readonly number[]).slice(i * dim, (i + 1) * dim)
    : (data as Float32Array).subarray(i * dim, (i + 1) * dim);
}

export interface Pca3 {
  /** n × 3, row-major. */
  projected: Float32Array;
  /** 3 unit principal axes, each `dim` long. */
  components: Float64Array[];
  /** Variance along each axis. */
  variances: number[];
  /** Share of total variance each axis explains. */
  explained: number[];
  mean: Float64Array;
}

/**
 * PCA to 3 components by power iteration on the implicit covariance
 * (v ← Xᵀ(Xv), X centred), with deflation. O(iterations · n · dim), no dim×dim matrix.
 */
export function pca3(data: Rows, dim: number, { iterations = 60, seed = 7 } = {}): Pca3 {
  const n = Math.floor(data.length / dim);
  const mean = new Float64Array(dim);
  for (let i = 0; i < n; i += 1) for (let j = 0; j < dim; j += 1) mean[j]! += data[i * dim + j]! / n;
  const centred = new Float64Array(n * dim);
  let totalVariance = 0;
  for (let i = 0; i < n; i += 1)
    for (let j = 0; j < dim; j += 1) {
      const v = data[i * dim + j]! - mean[j]!;
      centred[i * dim + j] = v;
      totalVariance += (v * v) / Math.max(n - 1, 1);
    }
  const rand = mulberry32(seed);
  const components: Float64Array[] = [];
  const variances: number[] = [];
  const scores = new Float64Array(n);
  for (let c = 0; c < 3; c += 1) {
    let v = Float64Array.from({ length: dim }, () => rand() - 0.5);
    let eigen = 0;
    for (let it = 0; it < iterations; it += 1) {
      for (const prev of components) {
        let d = 0;
        for (let j = 0; j < dim; j += 1) d += v[j]! * prev[j]!;
        for (let j = 0; j < dim; j += 1) v[j]! -= d * prev[j]!;
      }
      for (let i = 0; i < n; i += 1) {
        let s = 0;
        for (let j = 0; j < dim; j += 1) s += centred[i * dim + j]! * v[j]!;
        scores[i] = s;
      }
      const next = new Float64Array(dim);
      for (let i = 0; i < n; i += 1) {
        const s = scores[i]!;
        if (s === 0) continue;
        for (let j = 0; j < dim; j += 1) next[j]! += centred[i * dim + j]! * s;
      }
      let norm = 0;
      for (let j = 0; j < dim; j += 1) norm += next[j]! * next[j]!;
      norm = Math.sqrt(norm);
      eigen = norm / Math.max(n - 1, 1);
      if (norm === 0) break;
      v = next.map((x) => x / norm);
    }
    components.push(v);
    variances.push(eigen);
  }
  const projected = new Float32Array(n * 3);
  for (let i = 0; i < n; i += 1)
    for (let c = 0; c < 3; c += 1) {
      let s = 0;
      const comp = components[c]!;
      for (let j = 0; j < dim; j += 1) s += centred[i * dim + j]! * comp[j]!;
      projected[i * 3 + c] = s;
    }
  return {
    projected,
    components,
    variances,
    explained: variances.map((v) => (totalVariance > 0 ? v / totalVariance : 0)),
    mean,
  };
}

/** Indices of the k nearest rows to row i (Euclidean), excluding i. */
function knn(data: Rows, dim: number, i: number, k: number): number[] {
  const n = Math.floor(data.length / dim);
  const distances: [number, number][] = [];
  for (let j = 0; j < n; j += 1) {
    if (j === i) continue;
    let d = 0;
    for (let t = 0; t < dim; t += 1) d += (data[i * dim + t]! - data[j * dim + t]!) ** 2;
    distances.push([d, j]);
  }
  distances.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return distances.slice(0, k).map(([, j]) => j);
}

/**
 * Share of each point's k nearest neighbours in the high-dimensional space that
 * stay among its k nearest in the projection (mean over points), in [0, 1].
 * A simple trustworthiness proxy (Venna & Kaski 2001).
 */
export function knnPreservation(high: Rows, highDim: number, low: Rows, lowDim: number, k = 10, sample = 200): number {
  const n = Math.floor(high.length / highDim);
  if (n <= k) return 1;
  const step = Math.max(1, Math.floor(n / sample));
  let total = 0;
  let count = 0;
  for (let i = 0; i < n; i += step) {
    const a = new Set(knn(high, highDim, i, k));
    const b = knn(low, lowDim, i, k);
    total += b.filter((j) => a.has(j)).length / k;
    count += 1;
  }
  return total / count;
}

/** L2-normalize every row in place. */
export function normalizeRows(data: Float32Array | Float64Array, dim: number): void {
  const n = Math.floor(data.length / dim);
  for (let i = 0; i < n; i += 1) {
    let s = 0;
    for (let j = 0; j < dim; j += 1) s += data[i * dim + j]! ** 2;
    const norm = Math.sqrt(s);
    if (norm > 0) for (let j = 0; j < dim; j += 1) data[i * dim + j]! /= norm;
  }
}
