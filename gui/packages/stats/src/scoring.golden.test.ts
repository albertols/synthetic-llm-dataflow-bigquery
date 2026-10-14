/**
 * The TypeScript scoring mirror against the evaluator itself: every case in
 * golden/scoring.json (written by scripts/gui/export_golden_fixtures.py from
 * `sdfb_evaluation.scoring`) must come out of scoring.ts with the same
 * status, score, detail notes and JSON-safe stored numbers; the score
 * functions, roll-ups and headline counts likewise.
 */
import { describe, expect, it } from "vitest";

import { catalogue, catalogueById, type MetricId } from "@contracts/generated/catalogue";
import golden from "@contracts/generated/golden/scoring.json";
import { vocabularies } from "@contracts/generated/schemas";

import {
  aggregateScores,
  headlineCounts,
  MODEL_KEY,
  scoreRow,
  scoreValue,
  statusFor,
  zeroToleranceIds,
  type MetricReading,
} from "./scoring";

/** "+inf" / "-inf" / "nan" → the non-finite number; everything else as is. */
function num(value: number | string | null): number | null {
  if (value === "+inf") return Number.POSITIVE_INFINITY;
  if (value === "-inf") return Number.NEGATIVE_INFINITY;
  if (value === "nan") return Number.NaN;
  return value as number | null;
}

type Case = (typeof golden.cases)[number];

function reading(c: Case): MetricReading {
  const i = c.input;
  return {
    value: num(i.value),
    ciLow: num(i.ci_low),
    ciHigh: num(i.ci_high),
    noiseFloor: num(i.noise_floor),
    sourceValue: num(i.source_value),
    detail: i.detail,
    columnKind: i.column_kind,
  };
}

const metricOf = (id: string) => catalogueById[id as MetricId];
const close = (a: number | null, b: number | null) =>
  a === null || b === null ? a === b : Math.abs(a - b) <= 1e-12 * Math.max(1, Math.abs(b));

describe("scoring.ts matches sdfb_evaluation.scoring (golden/scoring.json)", () => {
  it("covers every ruling the GUI mirrors, in both directions", () => {
    expect(golden.generated_by).toBe("scripts/gui/export_golden_fixtures.py");
    expect(golden.cases.length).toBeGreaterThanOrEqual(60);
    const rules = new Set(golden.cases.map((c) => c.rule));
    for (const rule of ["R9", "R26", "R38", "R39", "R40", "R41", "R42", "R43", "R45", "R66"])
      expect(rules).toContain(rule);
    const statuses = new Set(golden.cases.map((c) => c.row.status));
    expect([...statuses].sort()).toEqual(["fail", "info", "not_evaluated", "pass", "warn"]);
  });

  it("R66: a copy-rate case for every column kind the evaluator's schema names", () => {
    const copyRate = golden.cases.filter(
      (c) => c.rule === "R66" && c.input.metric_id === "field.substantive_copy_rate",
    );
    expect([...new Set(copyRate.map((c) => c.input.column_kind))].sort()).toEqual(
      [...vocabularies["evaluation_metrics.column_kind"]].sort(),
    );
    // Only free text is gated: every other kind with a value is INFO.
    for (const c of copyRate.filter((x) => x.input.value !== null))
      expect(c.row.status, `${c.id} ${c.input.column_kind}`).toBe(c.input.column_kind === "text" ? "fail" : "info");
  });

  it("status: every case", () => {
    const wrong = golden.cases
      .filter(
        (c) => statusFor(metricOf(c.input.metric_id), reading(c), { enforced: c.input.enforced }) !== c.row.status,
      )
      .map((c) => `${c.id} ${c.note}`);
    expect(wrong).toEqual([]);
  });

  it("the row: status, score, detail and the JSON-safe numbers", () => {
    const wrong: string[] = [];
    for (const c of golden.cases) {
      const row = scoreRow(metricOf(c.input.metric_id), reading(c), { enforced: c.input.enforced });
      const expected = c.row;
      const same =
        row.status === expected.status &&
        close(row.score, expected.score) &&
        JSON.stringify(row.detail) === JSON.stringify(expected.detail) &&
        row.value === expected.value &&
        row.ci_low === expected.ci_low &&
        row.ci_high === expected.ci_high &&
        row.noise_floor === expected.noise_floor &&
        row.source_value === expected.source_value;
      if (!same) wrong.push(`${c.id} ${c.note}: got ${JSON.stringify(row)} want ${JSON.stringify(expected)}`);
    }
    expect(wrong).toEqual([]);
  });

  it("score_value: the five score functions", () => {
    const wrong = golden.score_values
      .filter((s) => !close(scoreValue(metricOf(s.metric_id), num(s.value), { target: s.target }), s.score))
      .map((s) => `${s.metric_id} ${String(s.value)} → ${String(s.score)}`);
    expect(wrong).toEqual([]);
  });

  it("aggregate_scores and headline_counts over the roll-up rows", () => {
    const { rows, aggregate_scores: expected, headline_counts: counts, model_key } = golden.rollup;
    expect(MODEL_KEY).toBe(model_key);
    const got = aggregateScores(rows, zeroToleranceIds(catalogue));
    expect(Object.keys(got)).toEqual(Object.keys(expected));
    for (const [key, scores] of Object.entries(expected))
      for (const [field, value] of Object.entries(scores))
        expect(close(got[key]![field as keyof (typeof got)[string]], value), `${key}.${field}`).toBe(true);
    expect(headlineCounts(rows)).toEqual(counts);
  });

  it("zero-tolerance ids come from the catalogue (R39)", () => {
    expect([...zeroToleranceIds(catalogue)].sort()).toEqual([
      "relationship.orphan_rate",
      "table.identity_duplicate_rate",
      "table.pk_duplicate_rate",
    ]);
  });
});
