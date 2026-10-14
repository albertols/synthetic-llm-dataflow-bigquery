/** Shannon entropy on counts, as the profiler (`_add_value_mix`) and the evaluator's census compute it. */

/** H = −Σ p log2 p over positive counts, bits. null when there is nothing to count. */
export function entropyBits(counts: readonly number[]): number | null {
  const total = counts.reduce((a, b) => a + (b > 0 ? b : 0), 0);
  if (total <= 0) return null;
  let h = 0;
  for (const c of counts) if (c > 0) h -= (c / total) * Math.log2(c / total);
  return h;
}

/** H / log2(distinct): 1 = uniform over the observed support; 0 when ≤ 1 distinct value (profiler convention). */
export function normalizedEntropy(counts: readonly number[]): number | null {
  const h = entropyBits(counts);
  if (h === null) return null;
  const distinct = counts.filter((c) => c > 0).length;
  return distinct > 1 ? h / Math.log2(distinct) : 0;
}

/** Miller–Madow bias-corrected entropy, bits: H + (k − 1)/(2n ln 2). */
export function millerMadowBits(counts: readonly number[]): number | null {
  const h = entropyBits(counts);
  if (h === null) return null;
  const n = counts.reduce((a, b) => a + (b > 0 ? b : 0), 0);
  const k = counts.filter((c) => c > 0).length;
  return h + (k - 1) / (2 * n * Math.LN2);
}
