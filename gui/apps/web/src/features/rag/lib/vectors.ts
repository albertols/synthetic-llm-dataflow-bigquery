/**
 * Row views and cosine search over a flat, row-major vector buffer (the wire
 * format of `/api/rag/chunks`: n × dim Float32). Nothing here copies the
 * buffer: rows are `subarray` views.
 *
 * Search mirrors the exact inner-product index the pipeline falls back to
 * (`_PyExactIPIndex`): descending score, ties to the lower index. The stored
 * vectors are L2-normalised, so the inner product is the cosine; the helpers
 * still divide by the norms so a hand-typed query or an un-normalised vector
 * cannot skew a score.
 */
export type Flat = Float32Array | Float64Array;

/** `n` row views of a flat buffer (no copy). */
export function rowsOf(flat: Flat, dim: number): Flat[] {
  const n = dim > 0 ? Math.floor(flat.length / dim) : 0;
  const rows = new Array<Flat>(n);
  for (let i = 0; i < n; i += 1) rows[i] = flat.subarray(i * dim, (i + 1) * dim);
  return rows;
}

export function dot(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let sum = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i += 1) sum += a[i]! * b[i]!;
  return sum;
}

export function norm(a: ArrayLike<number>): number {
  return Math.sqrt(dot(a, a));
}

/** L2 norm of every row. */
export function norms(rows: readonly ArrayLike<number>[]): Float64Array {
  const out = new Float64Array(rows.length);
  rows.forEach((row, i) => (out[i] = norm(row)));
  return out;
}

/** Cosine with precomputed norms (0 when either vector is zero). */
export function cosineWith(a: ArrayLike<number>, na: number, b: ArrayLike<number>, nb: number): number {
  return na > 0 && nb > 0 ? dot(a, b) / (na * nb) : 0;
}

export interface Hit {
  index: number;
  score: number;
}

/**
 * The k rows most similar to `query` by cosine: descending score, the lower
 * index first on ties (the exact index's order). `exclude` drops one row (the
 * query's own point).
 */
export function topKByCosine(
  query: ArrayLike<number>,
  rows: readonly ArrayLike<number>[],
  k: number,
  options: { exclude?: number; rowNorms?: Float64Array } = {},
): Hit[] {
  if (k <= 0 || !rows.length) return [];
  const qn = norm(query);
  const rn = options.rowNorms ?? norms(rows);
  const hits: Hit[] = [];
  rows.forEach((row, index) => {
    if (index === options.exclude) return;
    hits.push({ index, score: cosineWith(query, qn, row, rn[index]!) });
  });
  hits.sort((a, b) => b.score - a.score || a.index - b.index);
  return hits.slice(0, k);
}

/** Smallest and largest row norm: an L2-normalised set reads 1.0000 … 1.0000. */
export function normRange(rows: readonly ArrayLike<number>[]): { min: number; max: number } | null {
  if (!rows.length) return null;
  let min = Infinity;
  let max = -Infinity;
  for (const row of rows) {
    const n = norm(row);
    if (n < min) min = n;
    if (n > max) max = n;
  }
  return { min, max };
}

/** Largest absolute component difference between two vectors (the parity check of a re-embedding). */
export function maxAbsDiff(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let max = 0;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    const d = Math.abs((a[i] ?? 0) - (b[i] ?? 0));
    if (d > max) max = d;
  }
  return max;
}
