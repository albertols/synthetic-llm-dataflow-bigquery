/**
 * 384-d → 3-D, and the numbers that say how honest the picture is. Written
 * for a laptop iGPU/CPU: one pass over the data per step, three dot products
 * at a time, no per-row allocation.
 *
 * - PCA (Pearson 1901) by block subspace iteration with a Rayleigh–Ritz step:
 *   linear and deterministic (seeded start), and it keeps its axes, so a new
 *   vector (the query) projects exactly: (q − mean) · component.
 * - Spherical k-means (k = 3, seeded k-means++ start, cosine): clusters found
 *   in the full space, so colouring by cluster shows whether a projection
 *   kept them apart.
 * - k-NN preservation (Venna & Kaski 2001): the share of each sampled point's
 *   10 nearest neighbours in the full space (Euclidean; the same as cosine on
 *   unit vectors) that stay among its 10 nearest in 3-D.
 * - `normalizeCoords` centres the cloud and scales its 98th-percentile radius
 *   to 1, so the camera frames every set alike and an outlier cannot shrink
 *   the rest.
 *
 * UMAP lives in the worker only (projection.worker.ts): umap-js stays out of
 * every page chunk.
 */
import { mulberry32 } from "@synthetic-platform/stats";

export interface Frame {
  center: [number, number, number];
  scale: number;
}

export interface PcaModel {
  mean: Float64Array;
  components: Float64Array[];
  /** Share of the total variance along each component. */
  explained: number[];
}

export interface ProjectionResult {
  method: "pca" | "umap";
  /** n × 3, normalised by `frame`. */
  coords: Float32Array;
  frame: Frame;
  pca?: PcaModel;
}

export const TRUST_K = 10;
export const TRUST_SAMPLE = 150;
export const CLUSTER_K = 3;
export const PCA_ITERATIONS = 14;
export const UMAP_PARAMS = { nNeighbors: 15, minDist: 0.1, seed: 42 } as const;

export function normalizeCoords(raw: Float32Array): { coords: Float32Array; frame: Frame } {
  const n = Math.floor(raw.length / 3);
  const center: [number, number, number] = [0, 0, 0];
  for (let i = 0; i < n; i += 1) for (let c = 0; c < 3; c += 1) center[c]! += raw[i * 3 + c]! / Math.max(n, 1);
  const radii = new Float64Array(n);
  for (let i = 0; i < n; i += 1)
    radii[i] = Math.hypot(raw[i * 3]! - center[0], raw[i * 3 + 1]! - center[1], raw[i * 3 + 2]! - center[2]);
  const sorted = Float64Array.from(radii).sort();
  const r98 = n ? sorted[Math.min(n - 1, Math.floor(0.98 * (n - 1)))]! : 1;
  const scale = r98 > 1e-12 ? 1 / r98 : 1;
  const coords = new Float32Array(n * 3);
  for (let i = 0; i < n; i += 1)
    for (let c = 0; c < 3; c += 1) coords[i * 3 + c] = (raw[i * 3 + c]! - center[c]!) * scale;
  return { coords, frame: { center, scale } };
}

export function applyFrame(point: readonly number[], frame: Frame): [number, number, number] {
  return [
    (point[0]! - frame.center[0]) * frame.scale,
    (point[1]! - frame.center[1]) * frame.scale,
    (point[2]! - frame.center[2]) * frame.scale,
  ];
}

/** A new vector in the PCA's 3-D frame (exact: PCA is linear). */
export function projectPca(vector: ArrayLike<number>, model: PcaModel, frame: Frame): [number, number, number] {
  const raw = model.components.map((comp) => {
    let s = 0;
    for (let j = 0; j < comp.length; j += 1) s += ((vector[j] ?? 0) - model.mean[j]!) * comp[j]!;
    return s;
  });
  return applyFrame(raw, frame);
}

/**
 * Where to draw a vector a non-linear projection never saw: the
 * similarity-weighted mean of its nearest neighbours' positions (how UMAP
 * itself initialises `transform`). An approximation, labelled as such.
 */
export function placeByNeighbours(
  hits: readonly { index: number; score: number }[],
  coords: Float32Array,
): [number, number, number] | null {
  let wsum = 0;
  const out: [number, number, number] = [0, 0, 0];
  for (const { index, score } of hits) {
    const w = Math.max(score, 0) + 1e-6;
    wsum += w;
    for (let c = 0; c < 3; c += 1) out[c]! += w * coords[index * 3 + c]!;
  }
  return wsum > 0 ? [out[0] / wsum, out[1] / wsum, out[2] / wsum] : null;
}

/** Gram–Schmidt on three column vectors (in place, in order). */
function orthonormalize(q: Float64Array[]): void {
  for (let c = 0; c < q.length; c += 1) {
    const v = q[c]!;
    for (let p = 0; p < c; p += 1) {
      const u = q[p]!;
      let d = 0;
      for (let j = 0; j < v.length; j += 1) d += v[j]! * u[j]!;
      for (let j = 0; j < v.length; j += 1) v[j]! -= d * u[j]!;
    }
    let s = 0;
    for (let j = 0; j < v.length; j += 1) s += v[j]! * v[j]!;
    const inv = s > 0 ? 1 / Math.sqrt(s) : 0;
    for (let j = 0; j < v.length; j += 1) v[j]! *= inv;
  }
}

/** Eigen-decomposition of a symmetric 3 × 3 matrix (cyclic Jacobi). Returns eigenvalues and column eigenvectors. */
export function jacobi3(a: number[][]): { values: number[]; vectors: number[][] } {
  const m = a.map((row) => [...row]);
  const v = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  for (let sweep = 0; sweep < 50; sweep += 1) {
    const off = m[0]![1]! ** 2 + m[0]![2]! ** 2 + m[1]![2]! ** 2;
    if (off < 1e-24) break;
    for (const [p, q] of [
      [0, 1],
      [0, 2],
      [1, 2],
    ] as const) {
      const apq = m[p]![q]!;
      if (Math.abs(apq) < 1e-30) continue;
      const theta = (m[q]![q]! - m[p]![p]!) / (2 * apq);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1);
      const s = t * c;
      for (let k = 0; k < 3; k += 1) {
        const mkp = m[k]![p]!;
        const mkq = m[k]![q]!;
        m[k]![p] = c * mkp - s * mkq;
        m[k]![q] = s * mkp + c * mkq;
      }
      for (let k = 0; k < 3; k += 1) {
        const mpk = m[p]![k]!;
        const mqk = m[q]![k]!;
        m[p]![k] = c * mpk - s * mqk;
        m[q]![k] = s * mpk + c * mqk;
      }
      for (let k = 0; k < 3; k += 1) {
        const vkp = v[k]![p]!;
        const vkq = v[k]![q]!;
        v[k]![p] = c * vkp - s * vkq;
        v[k]![q] = s * vkp + c * vkq;
      }
    }
  }
  return { values: [m[0]![0]!, m[1]![1]!, m[2]![2]!], vectors: v };
}

/**
 * The top three principal components by block subspace iteration
 * (Y = X̃Q, Z = X̃ᵀY, Q = orth(Z)), then a Rayleigh–Ritz rotation so the
 * components come out ordered by variance. X̃ is the centred data, never
 * materialised: X̃v = Xv − (x̄·v)1.
 */
export function pca3Fast(data: Float32Array, dim: number, iterations = PCA_ITERATIONS, seed = 7) {
  const n = Math.floor(data.length / dim);
  const mean = new Float64Array(dim);
  for (let i = 0; i < n; i += 1) {
    const o = i * dim;
    for (let j = 0; j < dim; j += 1) mean[j]! += data[o + j]!;
  }
  for (let j = 0; j < dim; j += 1) mean[j]! /= Math.max(n, 1);
  let total = 0;
  for (let i = 0; i < n; i += 1) {
    const o = i * dim;
    for (let j = 0; j < dim; j += 1) {
      const d = data[o + j]! - mean[j]!;
      total += d * d;
    }
  }
  const denom = Math.max(n - 1, 1);
  total /= denom;
  const rand = mulberry32(seed);
  const q = [0, 1, 2].map(() => Float64Array.from({ length: dim }, () => rand() - 0.5));
  orthonormalize(q);
  const y0 = new Float64Array(n);
  const y1 = new Float64Array(n);
  const y2 = new Float64Array(n);
  const project = () => {
    const [q0, q1, q2] = q as [Float64Array, Float64Array, Float64Array];
    let m0 = 0;
    let m1 = 0;
    let m2 = 0;
    for (let j = 0; j < dim; j += 1) {
      m0 += mean[j]! * q0[j]!;
      m1 += mean[j]! * q1[j]!;
      m2 += mean[j]! * q2[j]!;
    }
    for (let i = 0; i < n; i += 1) {
      const o = i * dim;
      let a = 0;
      let b = 0;
      let c = 0;
      for (let j = 0; j < dim; j += 1) {
        const x = data[o + j]!;
        a += x * q0[j]!;
        b += x * q1[j]!;
        c += x * q2[j]!;
      }
      y0[i] = a - m0;
      y1[i] = b - m1;
      y2[i] = c - m2;
    }
  };
  for (let it = 0; it < iterations; it += 1) {
    project();
    const [z0, z1, z2] = q as [Float64Array, Float64Array, Float64Array];
    z0.fill(0);
    z1.fill(0);
    z2.fill(0);
    let s0 = 0;
    let s1 = 0;
    let s2 = 0;
    for (let i = 0; i < n; i += 1) {
      const o = i * dim;
      const a = y0[i]!;
      const b = y1[i]!;
      const c = y2[i]!;
      s0 += a;
      s1 += b;
      s2 += c;
      for (let j = 0; j < dim; j += 1) {
        const x = data[o + j]!;
        z0[j]! += x * a;
        z1[j]! += x * b;
        z2[j]! += x * c;
      }
    }
    for (let j = 0; j < dim; j += 1) {
      z0[j]! -= mean[j]! * s0;
      z1[j]! -= mean[j]! * s1;
      z2[j]! -= mean[j]! * s2;
    }
    orthonormalize(q);
  }
  // Rayleigh–Ritz: B = (X̃Q)ᵀ(X̃Q)/(n−1), rotate Q by B's eigenvectors.
  project();
  const ys = [y0, y1, y2];
  const b = [0, 1, 2].map((r) =>
    [0, 1, 2].map((c) => {
      let s = 0;
      const u = ys[r]!;
      const v = ys[c]!;
      for (let i = 0; i < n; i += 1) s += u[i]! * v[i]!;
      return s / denom;
    }),
  );
  const { values, vectors } = jacobi3(b);
  const order = [0, 1, 2].sort((a, c) => values[c]! - values[a]!);
  const components = order.map((col) => {
    const out = new Float64Array(dim);
    for (let r = 0; r < 3; r += 1) {
      const w = vectors[r]![col]!;
      const qr = q[r]!;
      for (let j = 0; j < dim; j += 1) out[j]! += w * qr[j]!;
    }
    return out;
  });
  const projected = new Float32Array(n * 3);
  order.forEach((col, c) => {
    for (let i = 0; i < n; i += 1)
      projected[i * 3 + c] = vectors[0]![col]! * y0[i]! + vectors[1]![col]! * y1[i]! + vectors[2]![col]! * y2[i]!;
  });
  const variances = order.map((col) => Math.max(values[col]!, 0));
  return {
    projected,
    components,
    variances,
    explained: variances.map((v) => (total > 0 ? v / total : 0)),
    mean,
  };
}

export function runPca(vectors: Float32Array, dim: number): ProjectionResult {
  const pca = pca3Fast(vectors, dim);
  const { coords, frame } = normalizeCoords(pca.projected);
  return {
    method: "pca",
    coords,
    frame,
    pca: { mean: pca.mean, components: pca.components, explained: pca.explained },
  };
}

/** Indices of the k smallest values of `dist` (excluding `self`), ascending distance, ties to the lower index. */
function smallestK(dist: Float64Array, k: number, self: number): number[] {
  const best: number[] = [];
  for (let j = 0; j < dist.length; j += 1) {
    if (j === self) continue;
    const d = dist[j]!;
    if (best.length === k && d >= dist[best[k - 1]!]!) continue;
    let at = best.length;
    while (at > 0 && dist[best[at - 1]!]! > d) at -= 1;
    best.splice(at, 0, j);
    if (best.length > k) best.pop();
  }
  return best;
}

/**
 * k-NN preservation: over `sample` evenly spaced points, the mean share of
 * their k nearest neighbours in `high` (dim-d) that are also among their k
 * nearest in `low` (3-d). Squared Euclidean in both.
 */
export function knnKept(high: Float32Array, dim: number, low: Float32Array, k = TRUST_K, sample = TRUST_SAMPLE) {
  const n = Math.floor(high.length / dim);
  if (n <= k + 1) return null;
  const sq = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const o = i * dim;
    let s = 0;
    for (let j = 0; j < dim; j += 1) s += high[o + j]! * high[o + j]!;
    sq[i] = s;
  }
  const step = Math.max(1, Math.floor(n / sample));
  const dh = new Float64Array(n);
  const dl = new Float64Array(n);
  let total = 0;
  let count = 0;
  for (let i = 0; i < n; i += step) {
    const oi = i * dim;
    for (let t = 0; t < n; t += 1) {
      const ot = t * dim;
      let dot = 0;
      for (let j = 0; j < dim; j += 1) dot += high[oi + j]! * high[ot + j]!;
      dh[t] = sq[i]! + sq[t]! - 2 * dot;
      const a = low[i * 3]! - low[t * 3]!;
      const b = low[i * 3 + 1]! - low[t * 3 + 1]!;
      const c = low[i * 3 + 2]! - low[t * 3 + 2]!;
      dl[t] = a * a + b * b + c * c;
    }
    const near = new Set(smallestK(dh, k, i));
    let kept = 0;
    for (const j of smallestK(dl, k, i)) if (near.has(j)) kept += 1;
    total += kept / k;
    count += 1;
  }
  return count ? total / count : null;
}

/** Spherical k-means (cosine), seeded k-means++ start, at most `iterations` Lloyd steps. */
export function sphericalKMeans(vectors: Float32Array, dim: number, k = CLUSTER_K, seed = 11, iterations = 15) {
  const n = Math.floor(vectors.length / dim);
  const labels = new Int32Array(n).fill(-1);
  if (n === 0) return labels;
  const kk = Math.min(k, n);
  const rand = mulberry32(seed);
  const inv = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const o = i * dim;
    let s = 0;
    for (let j = 0; j < dim; j += 1) s += vectors[o + j]! * vectors[o + j]!;
    inv[i] = s > 0 ? 1 / Math.sqrt(s) : 0;
  }
  const cosTo = (i: number, centre: Float64Array) => {
    const o = i * dim;
    let s = 0;
    for (let j = 0; j < dim; j += 1) s += vectors[o + j]! * centre[j]!;
    return s * inv[i]!;
  };
  const unitRow = (i: number) => {
    const out = new Float64Array(dim);
    for (let j = 0; j < dim; j += 1) out[j] = vectors[i * dim + j]! * inv[i]!;
    return out;
  };
  // k-means++ on cosine distance.
  const centres: Float64Array[] = [unitRow(Math.floor(rand() * n))];
  const best = new Float64Array(n).fill(Infinity);
  while (centres.length < kk) {
    const last = centres[centres.length - 1]!;
    let total = 0;
    for (let i = 0; i < n; i += 1) {
      const d = Math.max(0, 1 - cosTo(i, last));
      if (d < best[i]!) best[i] = d;
      total += best[i]! ** 2;
    }
    let target = rand() * total;
    let pick = n - 1;
    for (let i = 0; i < n; i += 1) {
      target -= best[i]! ** 2;
      if (target <= 0) {
        pick = i;
        break;
      }
    }
    centres.push(unitRow(pick));
  }
  for (let it = 0; it < iterations; it += 1) {
    let changed = false;
    for (let i = 0; i < n; i += 1) {
      let label = 0;
      let bestCos = -Infinity;
      for (let c = 0; c < centres.length; c += 1) {
        const v = cosTo(i, centres[c]!);
        if (v > bestCos) {
          bestCos = v;
          label = c;
        }
      }
      if (labels[i] !== label) {
        labels[i] = label;
        changed = true;
      }
    }
    if (!changed) break;
    const sums = centres.map(() => new Float64Array(dim));
    for (let i = 0; i < n; i += 1) {
      const sum = sums[labels[i]!]!;
      const o = i * dim;
      const w = inv[i]!;
      for (let j = 0; j < dim; j += 1) sum[j]! += vectors[o + j]! * w;
    }
    sums.forEach((sum, c) => {
      let s = 0;
      for (let j = 0; j < dim; j += 1) s += sum[j]! * sum[j]!;
      if (s === 0) return;
      const scale = 1 / Math.sqrt(s);
      const centre = centres[c]!;
      for (let j = 0; j < dim; j += 1) centre[j] = sum[j]! * scale;
    });
  }
  return orderLabels(labels, kk);
}

/** Relabel clusters by first appearance, so colours follow the data order, not the random start. */
function orderLabels(labels: Int32Array, k: number): Int32Array {
  const map = new Array<number>(k).fill(-1);
  let next = 0;
  const out = new Int32Array(labels.length);
  labels.forEach((l, i) => {
    if (l < 0) {
      out[i] = -1;
      return;
    }
    if (map[l] === -1) map[l] = next++;
    out[i] = map[l]!;
  });
  return out;
}
