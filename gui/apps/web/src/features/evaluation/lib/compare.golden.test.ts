/**
 * The compare view's verdict against `sdfb-eval compare` itself: each case in
 * golden/compare.json is one metric row of run A and of run B, with the verdict
 * the evaluator's `report.render.compare` gave the pair (written by
 * scripts/gui/export_golden_fixtures.py). `diffMetric` must give the same one,
 * judged against the same thing, so the two tools never disagree on a pair.
 */
import { describe, expect, it } from "vitest";

import type { MetricCell } from "@contracts/api";
import golden from "@contracts/generated/golden/compare.json";

import { diffMetric, type DiffResult } from "./compare";

/** A golden number: finite, or one of the strings "+inf", "-inf", "nan". */
type Num = number | string | null;
interface Side {
  value: Num;
  noise_floor: Num;
  ci_low: Num;
  ci_high: Num;
  status: string;
}
interface Case {
  id: string;
  note: string;
  metric_id: string;
  a: Side;
  b: Side;
  python: { delta: number | null; noise: string | null; verdict: string };
}
const cases: Case[] = golden.cases;

/** "+inf" / "-inf" / "nan" → the non-finite number; everything else as is. */
function num(value: Num): number | null {
  if (value === "+inf") return Number.POSITIVE_INFINITY;
  if (value === "-inf") return Number.NEGATIVE_INFINITY;
  if (value === "nan") return Number.NaN;
  return value as number | null;
}

function cell(row: Side): MetricCell {
  return {
    value: num(row.value),
    score: null,
    status: row.status as MetricCell["status"],
    baseline_value: null,
    noise_floor: num(row.noise_floor),
    ci_low: num(row.ci_low),
    ci_high: num(row.ci_high),
    threshold_warn: null,
    threshold_fail: null,
    n_source: null,
    n_synthetic: null,
    encoding_plan_digest: null,
    noise_downgraded_from: null,
  };
}

/** `sdfb-eval compare`'s verdict in the compare view's words. */
const VERDICT: Record<string, DiffResult["verdict"]> = {
  "=": "same",
  "≈": "approx",
  worse: "worse",
  better: "better",
  // No direction to judge by (an unknown metric, a target metric without a target).
  changed: "unjudged",
  // A side has no finite value: nothing to judge.
  "n/a": "unjudged",
};

/** What the evaluator judged the delta against, in `DiffResult.noise`'s words. */
function noiseOf(text: string | null): DiffResult["noise"] {
  if (text === null || text === "no noise floor") return "none";
  if (text === "CI overlap") return "ci_overlap";
  if (text.startsWith("floor ")) return "floor";
  throw new Error(`unknown noise basis ${text}`);
}

const diff = (c: Case) =>
  diffMetric({ metric_id: c.metric_id, table_name: "users", level: "column", cells: [cell(c.a), cell(c.b)] }, 0, 1);

describe("compare.ts matches sdfb-eval compare (golden/compare.json)", () => {
  it("covers every verdict and every way a delta is judged, at the boundaries", () => {
    expect(golden.generated_by).toBe("scripts/gui/export_golden_fixtures.py");
    expect([...new Set(cases.map((c) => c.python.verdict))].sort()).toEqual(Object.keys(VERDICT).sort());
    const judged = new Set(cases.map((c) => noiseOf(c.python.noise)));
    expect([...judged].sort()).toEqual(["ci_overlap", "floor", "none"]);
    // One floor missing, a NaN floor, a non-finite bound, an open interval.
    const lacksOneFloor = (c: Case) => (c.a.noise_floor === null) !== (c.b.noise_floor === null);
    expect(cases.some(lacksOneFloor)).toBe(true);
    expect(cases.some((c) => c.a.noise_floor === "nan" || c.b.noise_floor === "nan")).toBe(true);
    expect(cases.some((c) => c.b.ci_high === "+inf")).toBe(true);
    expect(cases.some((c) => c.a.ci_low !== null && c.b.ci_low !== null && c.b.ci_high === null)).toBe(true);
    // A delta between the larger floor and the hypotenuse of the two, and one exactly on it.
    const between = cases.filter((c) => {
      const [fa, fb, d] = [num(c.a.noise_floor), num(c.b.noise_floor), c.python.delta];
      return (
        fa !== null && fb !== null && d !== null && Math.abs(d) > Math.max(fa, fb) && Math.abs(d) < Math.hypot(fa, fb)
      );
    });
    expect(between.map((c) => c.python.verdict)).toEqual(["≈"]);
    expect(
      cases.some((c) => {
        const [fa, fb] = [num(c.a.noise_floor), num(c.b.noise_floor)];
        return fa !== null && fb !== null && c.python.delta === Math.hypot(fa, fb) && c.python.verdict === "≈";
      }),
    ).toBe(true);
  });

  it("gives every pair the evaluator's verdict", () => {
    const wrong = cases
      .filter((c) => diff(c).verdict !== VERDICT[c.python.verdict])
      .map((c) => `${c.id} ${c.note}: got ${diff(c).verdict}, sdfb-eval compare says ${c.python.verdict}`);
    expect(wrong).toEqual([]);
  });

  it("judges every delta against what the evaluator judged it against", () => {
    for (const c of cases) {
      const got = diff(c);
      const where = `${c.id} ${c.note}`;
      if (c.python.delta === null) expect(got.delta, where).toBeNull();
      else expect(got.delta, where).toBeCloseTo(c.python.delta, 12);
      expect(got.noise, where).toBe(noiseOf(c.python.noise));
      if (got.noise === "floor") {
        // Python prints the hypotenuse with four significant digits ("floor 0.04243").
        expect(Number(got.floor!.toPrecision(4)), where).toBe(Number(c.python.noise!.slice("floor ".length)));
      } else expect(got.floor, where).toBeNull();
      if (got.verdict === "approx") expect(got.basis, where).toBe(got.noise === "floor" ? "noise_floor" : "ci_overlap");
    }
  });
});
