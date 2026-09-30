/**
 * Number formatting for metric values. Values span 10⁻⁸ (a Wilson floor at
 * n = 9·10⁷) to 10⁸ (row counts), so fixed decimals lie in both directions:
 * these helpers keep three significant digits and switch to e-notation below
 * 10⁻⁴. Never prints NaN.
 */
import { formatCompact, formatCount, isFiniteNumber, MISSING } from "@/lib/format";

const SIG = new Intl.NumberFormat("en-US", { maximumSignificantDigits: 3 });

/** Three significant digits; e-notation for |v| < 1e-4; thousands separators above 1,000. */
export function fmtSig(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING;
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs < 1e-4) {
    const [mantissa, exponent] = value.toExponential(1).split("e");
    return `${mantissa}e${Number(exponent)}`;
  }
  if (abs >= 1000) return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
  return SIG.format(value);
}

/** A share as a percentage with up to three significant digits ("53%", "0.01%"); e-notation when tiny. */
export function fmtShare(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return MISSING;
  if (value === 0) return "0%";
  const pct = value * 100;
  if (Math.abs(pct) < 1e-3) return `${fmtSig(pct)}%`;
  return `${SIG.format(pct)}%`;
}

/** A metric value in the unit its `value_kind` implies. */
export function fmtMetric(value: number | null | undefined, kind?: string | null): string {
  if (!isFiniteNumber(value)) return MISSING;
  switch (kind) {
    case "share":
      return fmtShare(value);
    case "ratio":
      return `${fmtSig(value)}×`;
    case "bits":
      return `${fmtSig(value)} bits`;
    case "count":
      return formatCount(value);
    case "score":
      return value.toFixed(2);
    case "auc":
      return value.toFixed(3);
    default:
      return fmtSig(value);
  }
}

/** `value` to `digits` significant digits in the unit of `kind`, trailing zeros dropped. */
function fmtPrecise(value: number | null | undefined, kind: string | null | undefined, digits: number): string {
  if (!isFiniteNumber(value)) return MISSING;
  const sig = (x: number) => String(Number(x.toPrecision(digits)));
  switch (kind) {
    case "share":
      return `${sig(value * 100)}%`;
    case "ratio":
      return `${sig(value)}×`;
    case "bits":
      return `${sig(value)} bits`;
    default:
      return sig(value);
  }
}

/**
 * The numbers of one comparison ("value 99.995% ≤ warn 99.99%"), each in the metric's unit, with
 * as many significant digits as it takes for different numbers never to print alike — so a rule
 * sentence never reads "value 100% ≤ warn 100%", and a count threshold of 0.5 is never shown as
 * "1" beside a value of 0.
 */
export function fmtCompared(values: readonly (number | null | undefined)[], kind?: string | null): string[] {
  // A number that is not an edge (0, or 100% for a share) must not print as one: 0.99999 is not "100%".
  const parsed = (text: string) => {
    const n = Number(text.replace(/,|%|×| bits/g, ""));
    return kind === "share" ? n / 100 : n;
  };
  const edges = kind === "share" ? [0, 1] : [0];
  const clash = (texts: string[]) =>
    texts.some(
      (t, i) =>
        (isFiniteNumber(values[i]) && !edges.includes(values[i]) && edges.includes(parsed(t))) ||
        texts.some(
          (u, j) =>
            j > i && t === u && isFiniteNumber(values[i]) && isFiniteNumber(values[j]) && values[i] !== values[j],
        ),
    );
  const base = values.map((v) => fmtMetric(v, kind));
  const roundedCount = kind === "count" && values.some((v) => isFiniteNumber(v) && !Number.isInteger(v));
  if (!clash(base) && !roundedCount) return base;
  for (let digits = 3; digits <= 15; digits += 1) {
    const texts = values.map((v) => fmtPrecise(v, kind, digits));
    if (!clash(texts)) return texts;
  }
  return values.map((v) => fmtPrecise(v, kind, 17));
}

/** A 0–1 score with two decimals. */
export function fmtScore(value: number | null | undefined): string {
  return isFiniteNumber(value) ? value.toFixed(2) : MISSING;
}

/** Signed delta with the same unit as the value ("+0.012", "−3.1%"). */
export function fmtDelta(value: number | null | undefined, kind?: string | null): string {
  if (!isFiniteNumber(value)) return MISSING;
  if (value === 0) return "±0";
  const body = fmtMetric(Math.abs(value), kind);
  return `${value > 0 ? "+" : "−"}${body}`;
}

/** "gs://…/models/gemma4/gemma4-e4b/v1/" → "gemma4-e4b". Unknown shapes pass through. */
export function shortModel(uri: string | null | undefined): string {
  if (!uri) return MISSING;
  const parts = uri.split("/").filter(Boolean);
  const last = parts.at(-1) ?? uri;
  if (/^v\d+/.test(last) && parts.length > 1) return parts.at(-2) ?? last;
  return last;
}

/** 10000 → "10k" (lower case, for "10k-sample baseline"). */
export function fmtSampleSize(n: number | null | undefined): string {
  if (!isFiniteNumber(n)) return MISSING;
  return formatCompact(n, 1).toLowerCase();
}

/** The scope of a metric row in words: "users.age", "users.age × created_at", an edge, a table, the model. */
export function scopeLabel(row: {
  level: string;
  table_name: string;
  column_name: string | null;
  column_name_2: string | null;
  edge: string | null;
}): string {
  if (row.edge) return row.edge;
  if (row.column_name && row.column_name_2) return `${row.table_name}.${row.column_name} × ${row.column_name_2}`;
  if (row.column_name) return `${row.table_name}.${row.column_name}`;
  if (row.level === "model") return `model ${row.table_name}`;
  return row.table_name;
}
