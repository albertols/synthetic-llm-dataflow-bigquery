/** Dependence between two columns: Pearson, Spearman, bias-corrected Cramér's V, NMI, contingency TVD. */

/** Pearson r over pairwise-complete values; null with fewer than 3 pairs or zero variance. */
export function pearson(x: readonly number[], y: readonly number[]): number | null {
  const pairs: [number, number][] = [];
  for (let i = 0; i < Math.min(x.length, y.length); i += 1) {
    if (Number.isFinite(x[i]) && Number.isFinite(y[i])) pairs.push([x[i]!, y[i]!]);
  }
  if (pairs.length < 3) return null;
  const mx = pairs.reduce((a, [v]) => a + v, 0) / pairs.length;
  const my = pairs.reduce((a, [, v]) => a + v, 0) / pairs.length;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (const [a, b] of pairs) {
    sxy += (a - mx) * (b - my);
    sxx += (a - mx) ** 2;
    syy += (b - my) ** 2;
  }
  if (sxx === 0 || syy === 0) return null;
  return sxy / Math.sqrt(sxx * syy);
}

/** Average ranks (ties share their mean rank), 1-based. */
export function ranks(values: readonly number[]): number[] {
  const order = values.map((v, i) => [v, i] as const).sort((a, b) => a[0] - b[0]);
  const out = new Array<number>(values.length);
  for (let i = 0; i < order.length;) {
    let j = i;
    while (j + 1 < order.length && order[j + 1]![0] === order[i]![0]) j += 1;
    const rank = (i + j) / 2 + 1;
    for (let k = i; k <= j; k += 1) out[order[k]![1]] = rank;
    i = j + 1;
  }
  return out;
}

/** Spearman ρ = Pearson on ranks. */
export function spearman(x: readonly number[], y: readonly number[]): number | null {
  return pearson(ranks(x), ranks(y));
}

function totals(table: readonly (readonly number[])[]) {
  const rows = table.map((r) => r.reduce((a, b) => a + b, 0));
  const cols = table[0]!.map((_, j) => table.reduce((a, r) => a + r[j]!, 0));
  const n = rows.reduce((a, b) => a + b, 0);
  return { rows, cols, n };
}

/** Bias-corrected Cramér's V (Bergsma 2013, https://doi.org/10.1016/j.jkss.2012.10.002). */
export function cramersV(table: readonly (readonly number[])[]): number | null {
  if (!table.length || !table[0]!.length) return null;
  const { rows, cols, n } = totals(table);
  if (n <= 1) return null;
  let chi2 = 0;
  table.forEach((row, i) =>
    row.forEach((obs, j) => {
      const expected = (rows[i]! * cols[j]!) / n;
      if (expected > 0) chi2 += (obs - expected) ** 2 / expected;
    }),
  );
  const r = rows.filter((v) => v > 0).length;
  const k = cols.filter((v) => v > 0).length;
  if (r < 2 || k < 2) return null;
  const phi2 = Math.max(0, chi2 / n - ((k - 1) * (r - 1)) / (n - 1));
  const rt = r - (r - 1) ** 2 / (n - 1);
  const kt = k - (k - 1) ** 2 / (n - 1);
  const denom = Math.min(kt - 1, rt - 1);
  return denom > 0 ? Math.sqrt(phi2 / denom) : null;
}

/** Normalized mutual information I(X;Y) / min(H(X), H(Y)). */
export function nmi(table: readonly (readonly number[])[]): number | null {
  if (!table.length) return null;
  const { rows, cols, n } = totals(table);
  if (n <= 0) return null;
  const h = (v: number[]) => v.reduce((acc, c) => (c > 0 ? acc - (c / n) * Math.log(c / n) : acc), 0);
  const hx = h(rows);
  const hy = h(cols);
  let mi = 0;
  table.forEach((row, i) =>
    row.forEach((c, j) => {
      if (c > 0) mi += (c / n) * Math.log((c * n) / (rows[i]! * cols[j]!));
    }),
  );
  const denom = Math.min(hx, hy);
  return denom > 0 ? Math.max(0, mi) / denom : null;
}

/** ½ Σ |p_src − p_syn| over the cells of two same-shape contingency tables. */
export function contingencyTvd(
  tableSource: readonly (readonly number[])[],
  tableSynthetic: readonly (readonly number[])[],
): number | null {
  const a = tableSource.flat();
  const b = tableSynthetic.flat();
  if (a.length !== b.length) throw new Error("contingency shape mismatch");
  const sa = a.reduce((x, y) => x + y, 0);
  const sb = b.reduce((x, y) => x + y, 0);
  if (sa <= 0 || sb <= 0) return null;
  return 0.5 * a.reduce((acc, v, i) => acc + Math.abs(v / sa - b[i]! / sb), 0);
}
