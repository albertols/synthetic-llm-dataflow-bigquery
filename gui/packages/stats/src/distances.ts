/**
 * Distances between two count (or probability) vectors over the same support.
 * Inputs are normalized internally, so raw counts work. Mirrors the evaluator's
 * `stats/distances.py` (plan Task 7).
 */

export function normalize(values: readonly number[]): number[] {
  const total = values.reduce((a, b) => a + b, 0);
  return total > 0 ? values.map((v) => v / total) : values.map(() => 0);
}

/** Union support of two value → count maps, in first-seen order. */
export function alignCounts(
  a: ReadonlyMap<string, number> | Readonly<Record<string, number>>,
  b: ReadonlyMap<string, number> | Readonly<Record<string, number>>,
): { keys: string[]; a: number[]; b: number[] } {
  const toMap = (x: typeof a): ReadonlyMap<string, number> =>
    x instanceof Map ? (x as ReadonlyMap<string, number>) : new Map(Object.entries(x as Record<string, number>));
  const ma = toMap(a);
  const mb = toMap(b);
  const keys = [...new Set([...ma.keys(), ...mb.keys()])];
  return { keys, a: keys.map((k) => ma.get(k) ?? 0), b: keys.map((k) => mb.get(k) ?? 0) };
}

function check(p: readonly number[], q: readonly number[]) {
  if (p.length !== q.length) throw new Error(`support mismatch: ${p.length} vs ${q.length}`);
}

/** Total variation distance ½ Σ|p − q| ∈ [0, 1]. null when either side is empty. */
export function tvd(p: readonly number[], q: readonly number[]): number | null {
  check(p, q);
  if (!p.some((v) => v > 0) || !q.some((v) => v > 0)) return null;
  const a = normalize(p);
  const b = normalize(q);
  return 0.5 * a.reduce((acc, v, i) => acc + Math.abs(v - b[i]!), 0);
}

/** Jensen–Shannon divergence in bits ∈ [0, 1] (Lin 1991), no smoothing; one-sided mass contributes ½·mass. */
export function jsdBits(p: readonly number[], q: readonly number[]): number | null {
  check(p, q);
  if (!p.some((v) => v > 0) || !q.some((v) => v > 0)) return null;
  const a = normalize(p);
  const b = normalize(q);
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) {
    const m = (a[i]! + b[i]!) / 2;
    if (a[i]! > 0) sum += 0.5 * a[i]! * Math.log2(a[i]! / m);
    if (b[i]! > 0) sum += 0.5 * b[i]! * Math.log2(b[i]! / m);
  }
  return Math.max(0, sum);
}

/** Population stability index with +pseudo-count smoothing on COUNTS: Σ (q − p) ln(q/p). */
export function psi(countsSource: readonly number[], countsSynthetic: readonly number[], pseudo = 0.5): number | null {
  check(countsSource, countsSynthetic);
  const ns = countsSource.reduce((a, b) => a + b, 0);
  const ny = countsSynthetic.reduce((a, b) => a + b, 0);
  if (ns <= 0 || ny <= 0) return null;
  const bins = countsSource.length;
  let sum = 0;
  for (let i = 0; i < bins; i += 1) {
    const p = (countsSource[i]! + pseudo) / (ns + pseudo * bins);
    const q = (countsSynthetic[i]! + pseudo) / (ny + pseudo * bins);
    sum += (q - p) * Math.log(q / p);
  }
  return sum;
}

/** Cohen's w over the source support (p > 0): √Σ (q − p)²/p. */
export function cohensW(p: readonly number[], q: readonly number[]): number | null {
  check(p, q);
  if (!p.some((v) => v > 0) || !q.some((v) => v > 0)) return null;
  const a = normalize(p);
  const b = normalize(q);
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) if (a[i]! > 0) sum += (b[i]! - a[i]!) ** 2 / a[i]!;
  return Math.sqrt(sum);
}

/** Hellinger distance ∈ [0, 1]. */
export function hellinger(p: readonly number[], q: readonly number[]): number | null {
  check(p, q);
  if (!p.some((v) => v > 0) || !q.some((v) => v > 0)) return null;
  const a = normalize(p);
  const b = normalize(q);
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) sum += (Math.sqrt(a[i]!) - Math.sqrt(b[i]!)) ** 2;
  return Math.sqrt(sum / 2);
}
