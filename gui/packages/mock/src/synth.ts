/**
 * How each engine turns the reference rows into synthetic rows, in miniature —
 * enough for the metrics to tell the engines apart for the right reasons:
 *
 * - b1_rag samples every column from its own marginal (inverse CDF through the
 *   whole sorted sample for numbers, exact frequencies for categories, a
 *   similarity blend with uniform for timestamps), so joint structure is lost;
 *   free text comes from a bounded LLM pool (512 cap) with optional expansion.
 * - b2_library interpolates through 11 deciles, tempers categories with
 *   T = 2(1 − similarity) and keeps `corrKeep` of each row's joint structure.
 *
 * `quality` injects the storyline's defects (leaks, collapse, drift, orphans).
 */
import { cumulative, profilerDeciles, Random, seedFrom } from "@synthetic-platform/stats";

import type { EvalSpec } from "./storyline";
import {
  CITIES,
  CITY_WEIGHTS,
  email,
  FIRST_NAMES,
  FIRST_WEIGHTS,
  isoFromEpoch,
  LAST_NAMES,
  LAST_WEIGHTS,
  NOVEL_CITIES,
  NOVEL_FIRST,
  NOVEL_LAST,
  type ColumnDef,
  type Row,
  type TableDef,
  type Value,
} from "./thelook";

export const FREE_TEXT_POOL_MAX = 512;
/** Identifier ids of synthetic rows start here, far from the source's 1..N. */
export const SYNTHETIC_ID_BASE = 5_000_000;

export const epoch = (iso: string) => Date.parse(iso) / 1000;

/** The universe (value → probability) a free-text column's values come from, when finite. */
export function textUniverse(table: string, column: string): { values: string[]; weights: number[] } | null {
  if (table !== "users") return null;
  if (column === "first_name") return { values: FIRST_NAMES, weights: FIRST_WEIGHTS };
  if (column === "last_name") return { values: LAST_NAMES, weights: LAST_WEIGHTS };
  if (column === "city") return { values: CITIES, weights: CITY_WEIGHTS };
  return null;
}

/** A value an LLM plausibly invents for the column, outside the source universe. */
const CDFS = new Map<string, Float64Array>();
function universeCdf(table: string, column: string, weights: readonly number[]): Float64Array {
  const key = `${table}.${column}`;
  let cdf = CDFS.get(key);
  if (!cdf) CDFS.set(key, (cdf = cumulative(weights)));
  return cdf;
}

function novelValue(column: ColumnDef, rng: Random): string {
  if (column.kind === "identifier")
    return email(rng.pick(NOVEL_FIRST), rng.pick(LAST_NAMES), rng.bernoulli(0.8) ? rng.int(1, 9999) : null);
  if (column.name === "first_name") return rng.pick(NOVEL_FIRST);
  if (column.name === "last_name") return rng.pick(NOVEL_LAST);
  return rng.pick(NOVEL_CITIES);
}

export interface Pool {
  column: string;
  target: number;
  values: string[];
  stagnated: boolean;
  attempts: number;
}

/**
 * The free-text pool an engine builds for (reference digest, model, column): up
 * to `target` distinct values, `valueLeak` of them copied from the reference
 * rows, the rest LLM-plausible (mostly from the source's style, some novel).
 */
export function buildPool(
  table: TableDef,
  column: ColumnDef,
  reference: readonly Row[],
  spec: EvalSpec,
  digest: string,
): Pool {
  const rng = new Random(seedFrom("pool", digest, spec.llm, column.name));
  const universe = textUniverse(table.name, column.name);
  const observed = new Set(
    reference.map((r) => r[column.name]).filter((v): v is string => typeof v === "string" && v !== ""),
  );
  const target = Math.min(spec.numRows, universe ? universe.values.length : observed.size, FREE_TEXT_POOL_MAX);
  const values = new Set<string>();
  const referenceValues = [...observed];
  const novelShare = spec.quality.poolCollapse ? 0.12 : 0.04;
  let attempts = 0;
  const perCall = 32 * 4;
  // Stagnation: a small model gives up earlier on a narrow vocabulary.
  const stagnateAt = spec.llm === "gemma4-e4b" && column.name === "first_name" ? Math.round(target * 0.8) : target;
  while (values.size < stagnateAt && attempts < 40) {
    attempts += 1;
    for (let i = 0; i < perCall && values.size < stagnateAt; i += 1) {
      const u = rng.uniform();
      if (u < spec.quality.valueLeak && referenceValues.length) values.add(rng.pick(referenceValues));
      else if (u < spec.quality.valueLeak + novelShare || !universe) values.add(novelValue(column, rng));
      else values.add(universe.values[rng.int(0, universe.values.length - 1)]!);
    }
  }
  return { column: column.name, target, values: [...values], stagnated: values.size < target, attempts };
}

function temper(weights: number[], temperature: number): number[] {
  if (temperature <= 1e-6) {
    const max = Math.max(...weights);
    return weights.map((w) => (w === max ? 1 : 0));
  }
  return weights.map((w) => (w > 0 ? w ** (1 / temperature) : 0));
}

interface ColumnSampler {
  /** A draw; b2 passes the row's base source value to keep joint structure. */
  draw(rng: Random, base: Row): Value;
}

function nullRate(values: readonly Value[]): number {
  return values.filter((v) => v === null).length / Math.max(values.length, 1);
}

function numericSampler(column: ColumnDef, values: Value[], spec: EvalSpec, isTime: boolean): ColumnSampler {
  const nums = values.filter((v) => v !== null).map((v) => (isTime ? epoch(v as string) : (v as number)));
  const sorted = [...nums].sort((a, b) => a - b);
  const knots = spec.engine === "b2_library" ? profilerDeciles(sorted) : sorted;
  const lo = sorted[0] ?? 0;
  const hi = sorted[sorted.length - 1] ?? 0;
  const mean = nums.reduce((a, b) => a + b, 0) / Math.max(nums.length, 1);
  const sd = Math.sqrt(nums.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(nums.length, 1));
  const nulls = nullRate(values);
  const shift = spec.quality.drift * (isTime ? (hi - lo) * 0.1 : sd);
  const s = spec.similarity;
  const interp = (u: number) => {
    const h = u * (knots.length - 1);
    const i = Math.floor(h);
    const j = Math.min(i + 1, knots.length - 1);
    return knots[i]! + (h - i) * (knots[j]! - knots[i]!);
  };
  const rankOf = (x: number) => {
    let lo2 = 0;
    let hi2 = sorted.length;
    while (lo2 < hi2) {
      const mid = (lo2 + hi2) >> 1;
      if (sorted[mid]! < x) lo2 = mid + 1;
      else hi2 = mid;
    }
    return lo2 / Math.max(sorted.length - 1, 1);
  };
  return {
    draw(rng, base) {
      const baseValue = base[column.name];
      // b2 keeps joint structure through ranks (a copula), never by copying the base value.
      if (spec.quality.corrKeep > 0 && rng.bernoulli(spec.quality.corrKeep)) {
        if (baseValue === null || baseValue === undefined) return null;
        const x = isTime ? epoch(String(baseValue)) : Number(baseValue);
        const u = Math.min(1, Math.max(0, rankOf(x) + rng.normal(0, 0.06)));
        const w = interp(u) + shift;
        if (isTime) return isoFromEpoch(w);
        return column.bqType === "INT64" ? Math.round(w) : Math.round(w * 100) / 100;
      }
      if (rng.bernoulli(nulls)) return null;
      let v: number;
      if (isTime && spec.engine === "b1_rag" && spec.quality.temporalBlend) {
        // b1 temporal: similarity · (anchored + jitter) + (1 − similarity) · uniform(lo, hi).
        const anchored = sorted[rng.int(0, sorted.length - 1)]!;
        const jitter = (rng.uniform() - 0.5) * (hi - lo) * 0.02 * (1 - s);
        v = s * (anchored + jitter) + (1 - s) * rng.between(lo, hi);
      } else v = interp(rng.uniform());
      v += shift;
      if (isTime) return isoFromEpoch(v);
      if (column.bqType === "INT64") return Math.round(v);
      return Math.round(v * 100) / 100;
    },
  };
}

function categoricalSampler(values: Value[], spec: EvalSpec): ColumnSampler {
  const counts = new Map<Value, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  const keys = [...counts.keys()];
  let weights = keys.map((k) => counts.get(k)!);
  if (spec.engine === "b2_library") weights = temper(weights, 2 * (1 - spec.similarity));
  if (spec.quality.drift > 0) {
    const total = weights.reduce((a, b) => a + b, 0);
    weights = weights.map((w) => w / total + spec.quality.drift / keys.length);
  }
  return { draw: (rng) => keys[rng.weighted(weights)]! };
}

function textSampler(
  table: TableDef,
  column: ColumnDef,
  values: Value[],
  spec: EvalSpec,
  pool: Pool | undefined,
  reference: readonly Row[],
): ColumnSampler {
  const empties = values.filter((v) => v === "").length / Math.max(values.length, 1);
  const nulls = nullRate(values);
  const referenceValues = reference
    .map((r) => r[column.name])
    .filter((v): v is string => typeof v === "string" && v !== "");
  const universe = textUniverse(table.name, column.name);
  const cdf = universe ? universeCdf(table.name, column.name, universe.weights) : null;
  const expand = !spec.quality.poolCollapse;
  return {
    draw(rng) {
      if (rng.bernoulli(nulls)) return null;
      if (rng.bernoulli(empties)) return "";
      if (column.kind === "identifier") {
        // E-mails: a pool when expansion is off; shape-expanded (novel) otherwise; leaks copy the reference.
        if (rng.bernoulli(spec.quality.valueLeak)) return rng.pick(referenceValues);
        if (!expand && pool) return rng.pick(pool.values);
        return email(rng.pick(NOVEL_FIRST), rng.pick(LAST_NAMES), rng.bernoulli(0.8) ? rng.int(1, 9999) : null);
      }
      if (pool && pool.values.length) {
        if (expand && universe && rng.bernoulli(0.45)) {
          // Expansion recombines observed tokens into values the pool never held.
          const parts = universe.values[rng.fromCdf(cdf!)]!.split(" ");
          const other = universe.values[rng.fromCdf(cdf!)]!.split(" ");
          return parts.length > 1 && other.length > 1 ? `${parts[0]} ${other[1]}` : rng.pick(pool.values);
        }
        return rng.pick(pool.values);
      }
      return rng.pick(referenceValues);
    },
  };
}

export interface SynthesisInputs {
  table: TableDef;
  source: readonly Row[];
  reference: readonly Row[];
  spec: EvalSpec;
  pools: ReadonlyMap<string, Pool>;
  rows: number;
  /** The synthetic parent keys an FK column draws from (per FK column name). */
  parentKeys: ReadonlyMap<string, number>;
}

/**
 * `n` values of one free-text column from the engine's sampler alone: a larger
 * sample than the synthetic rows, so distinct-at-n estimates do not saturate.
 */
export function drawTextValues(inputs: SynthesisInputs, columnName: string, n: number): string[] {
  const { table, source, reference, spec, pools } = inputs;
  const column = table.columns.find((c) => c.name === columnName)!;
  const sampler = textSampler(
    table,
    column,
    source.map((r) => r[column.name] ?? null),
    spec,
    pools.get(column.name),
    reference,
  );
  const rng = new Random(seedFrom("text-draws", spec.id, table.name, columnName));
  const out: string[] = [];
  for (let i = 0; i < n; i += 1) {
    const v = sampler.draw(rng, source[0]!);
    if (typeof v === "string" && v !== "") out.push(v);
  }
  return out;
}

/** `rows` synthetic rows for one table under one evaluation's engine and quality. */
export function synthesize(inputs: SynthesisInputs): Row[] {
  const { table, source, reference, spec, pools, rows, parentKeys } = inputs;
  const rng = new Random(seedFrom("synth", spec.id, table.name));
  const samplers = new Map<string, ColumnSampler>();
  for (const column of table.columns) {
    if (column.role) continue;
    const values = source.map((r) => r[column.name] ?? null);
    if (column.kind === "numeric") samplers.set(column.name, numericSampler(column, values, spec, false));
    else if (column.kind === "temporal") samplers.set(column.name, numericSampler(column, values, spec, true));
    else if (column.kind === "categorical" || column.kind === "boolean")
      samplers.set(column.name, categoricalSampler(values, spec));
    else samplers.set(column.name, textSampler(table, column, values, spec, pools.get(column.name), reference));
  }
  const out: Row[] = [];
  const { rowLeak, exposureLeak, nearLeak, corrKeep, orphanShare, invalidShare } = spec.quality;
  for (let i = 0; i < rows; i += 1) {
    const base = source[rng.int(0, source.length - 1)]!;
    const row: Row = {};
    const u = rng.uniform();
    // Copies: a reference row (leak), one of the first 1,024 (prompt-exposed), or one field changed.
    const copy =
      u < rowLeak
        ? reference[rng.int(0, reference.length - 1)]!
        : u < rowLeak + exposureLeak
          ? reference[rng.int(0, Math.min(1024, reference.length) - 1)]!
          : u < rowLeak + exposureLeak + nearLeak
            ? reference[rng.int(0, reference.length - 1)]!
            : null;
    const changed = u >= rowLeak + exposureLeak && copy ? table.columns.find((c) => !c.role)?.name : undefined;
    for (const column of table.columns) {
      if (column.role === "pk") {
        row[column.name] = SYNTHETIC_ID_BASE + i + 1;
      } else if (column.role === "fk") {
        const parents = parentKeys.get(column.name) ?? 1000;
        const orphan = column.name === "product_id" && rng.bernoulli(orphanShare);
        row[column.name] = orphan
          ? 9_000_000 + rng.int(1, 999)
          : (column.name === "product_id" ? 1 : SYNTHETIC_ID_BASE + 1) + rng.int(0, parents - 1);
      } else if (copy && column.name !== changed) {
        row[column.name] = copy[column.name] ?? null;
      } else if (
        corrKeep > 0 &&
        (column.kind === "categorical" || column.kind === "boolean") &&
        rng.bernoulli(corrKeep)
      ) {
        // Categories keep the base row's value (joint structure; not memorization at ≤ 50 values).
        row[column.name] = base[column.name] ?? null;
      } else {
        row[column.name] = samplers.get(column.name)!.draw(rng, base);
      }
    }
    if (invalidShare > 0 && table.name === "users" && rng.bernoulli(invalidShare)) row.age = "N/A";
    out.push(row);
  }
  return out;
}
