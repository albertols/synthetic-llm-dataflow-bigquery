/**
 * Seeded randomness shared by the mock generator, the goldens and the RAG tab.
 *
 * `mulberry32U32` is the canonical mulberry32 (Tommy Ettinger, public domain):
 * bit-identical to `mulberry32` in scripts/gui/export_golden_fixtures.py, so a
 * seeded matrix is the same array of doubles on both sides.
 */

/** A uint32 stream. */
export function mulberry32U32(seed: number): () => number {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return (t ^ (t >>> 14)) >>> 0;
  };
}

/** Uniform [0, 1) doubles (u32 / 2^32, exact). */
export function mulberry32(seed: number): () => number {
  const next = mulberry32U32(seed);
  return () => next() / 4294967296;
}

export type Rng = () => number;

/** A seeded random-number source with the draws the mock needs. */
export class Random {
  readonly uniform: Rng;
  private spare: number | null = null;

  constructor(seed: number) {
    this.uniform = mulberry32(seed);
  }

  /** Uniform in [lo, hi). */
  between(lo: number, hi: number): number {
    return lo + (hi - lo) * this.uniform();
  }

  /** Integer in [lo, hi] inclusive. */
  int(lo: number, hi: number): number {
    return lo + Math.floor(this.uniform() * (hi - lo + 1));
  }

  /** Standard normal (Box–Muller, cached pair). */
  normal(mean = 0, sd = 1): number {
    if (this.spare !== null) {
      const z = this.spare;
      this.spare = null;
      return mean + sd * z;
    }
    let u = 0;
    while (u === 0) u = this.uniform();
    const v = this.uniform();
    const r = Math.sqrt(-2 * Math.log(u));
    this.spare = r * Math.sin(2 * Math.PI * v);
    return mean + sd * r * Math.cos(2 * Math.PI * v);
  }

  bernoulli(p: number): boolean {
    return this.uniform() < p;
  }

  pick<T>(items: readonly T[]): T {
    if (!items.length) throw new Error("pick from an empty list");
    return items[Math.floor(this.uniform() * items.length)]!;
  }

  /** Index drawn with probability ∝ weights. */
  weighted(weights: readonly number[]): number {
    const total = weights.reduce((a, b) => a + b, 0);
    let u = this.uniform() * total;
    for (let i = 0; i < weights.length; i += 1) {
      u -= weights[i]!;
      if (u < 0) return i;
    }
    return weights.length - 1;
  }

  /** Poisson(lambda): inversion below 30, normal approximation above. */
  poisson(lambda: number): number {
    if (lambda <= 0) return 0;
    if (lambda > 30) return Math.max(0, Math.round(this.normal(lambda, Math.sqrt(lambda))));
    const limit = Math.exp(-lambda);
    let k = 0;
    let p = this.uniform();
    while (p > limit) {
      k += 1;
      p *= this.uniform();
    }
    return k;
  }

  /** Binomial(n, p): exact below n·min(p,1−p) = 30, normal approximation above (clamped to [0, n]). */
  binomial(n: number, p: number): number {
    if (n <= 0 || p <= 0) return 0;
    if (p >= 1) return n;
    const mean = n * p;
    if (Math.min(mean, n - mean) > 30) {
      return Math.min(n, Math.max(0, Math.round(this.normal(mean, Math.sqrt(mean * (1 - p))))));
    }
    if (n <= 200) {
      let k = 0;
      for (let i = 0; i < n; i += 1) if (this.uniform() < p) k += 1;
      return k;
    }
    return p < 0.5 ? Math.min(n, this.poisson(mean)) : n - Math.min(n, this.poisson(n * (1 - p)));
  }

  /** Multinomial counts of `n` draws over probabilities `probs` (conditional binomials). */
  multinomial(n: number, probs: readonly number[]): number[] {
    const out = new Array<number>(probs.length).fill(0);
    let remaining = n;
    let mass = probs.reduce((a, b) => a + b, 0);
    for (let i = 0; i < probs.length && remaining > 0; i += 1) {
      const p = probs[i]!;
      if (i === probs.length - 1 || mass <= 0) {
        out[i] = remaining;
        break;
      }
      const draw = this.binomial(remaining, Math.min(1, p / mass));
      out[i] = draw;
      remaining -= draw;
      mass -= p;
    }
    return out;
  }

  /** In-place Fisher–Yates. */
  shuffle<T>(items: T[]): T[] {
    for (let i = items.length - 1; i > 0; i -= 1) {
      const j = Math.floor(this.uniform() * (i + 1));
      [items[i], items[j]] = [items[j]!, items[i]!];
    }
    return items;
  }
}

/** A stable 32-bit seed from a string (FNV-1a), to derive per-entity streams. */
export function seedFrom(...parts: (string | number)[]): number {
  let h = 0x811c9dc5;
  for (const char of parts.join("\u001f")) {
    h ^= char.codePointAt(0)!;
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export interface GoldenMatrixSpec {
  seed: number;
  rows: number;
  cols: number;
  base_rows: number;
  repeat_base?: boolean;
  duplicates: readonly (readonly number[])[];
}

/** The matrix a retrieval golden describes (see `matrix()` in the exporter). */
export function goldenMatrix(spec: GoldenMatrixSpec): number[][] {
  const next = mulberry32U32(spec.seed);
  let rows: number[][] = [];
  for (let r = 0; r < spec.base_rows; r += 1) {
    const row: number[] = [];
    for (let c = 0; c < spec.cols; c += 1) row.push(next() / 4294967296 - 0.5);
    rows.push(row);
  }
  if (spec.repeat_base) rows = Array.from({ length: spec.rows }, (_, i) => [...rows[i % spec.base_rows]!]);
  for (const [dst, src] of spec.duplicates) rows[dst!] = [...rows[src!]!];
  return rows;
}
