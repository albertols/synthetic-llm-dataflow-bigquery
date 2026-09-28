/**
 * The EVALUATION tab's explanations against the evaluator itself: each case in
 * golden/scoring.json (Python `to_metric_row` output) becomes the row the BFF
 * would serve, and the explanation's re-read rule must agree with the stored
 * status — never "the stored status wins" — and name what the evaluator
 * recorded: the reason, the noise downgrade, a missing noise input, an
 * infinite value, a documented edge.
 */
import { describe, expect, it } from "vitest";

import type { MetricRow } from "@contracts/api";
import { catalogueById, type MetricId } from "@contracts/generated/catalogue";
import golden from "@contracts/generated/golden/scoring.json";

import { metric } from "../test/fixtures";
import { explainStatus, readingOf, ruleStatus } from "./reading";

function rowOf(c: (typeof golden.cases)[number]): MetricRow {
  const meta = catalogueById[c.input.metric_id as MetricId];
  return metric(c.input.metric_id, {
    table_name: "users",
    edge: meta.level === "relationship" ? "orders.user_id->users.id" : null,
    value: c.row.value,
    ci_low: c.row.ci_low,
    ci_high: c.row.ci_high,
    noise_floor: c.row.noise_floor,
    source_value: c.row.source_value,
    noise_floor_method: (meta.noise_floor === "none" ? null : meta.noise_floor) as MetricRow["noise_floor_method"],
    status: c.row.status as MetricRow["status"],
    score: c.row.score,
    detail: c.row.detail as MetricRow["detail"],
  });
}

describe("explanations re-read the evaluator's rule (golden/scoring.json)", () => {
  const cases = golden.cases.map((c) => ({ c, row: rowOf(c) }));

  it("the re-read status is the stored one for every case", () => {
    const wrong = cases.filter(({ row }) => ruleStatus(row) !== row.status).map(({ c }) => `${c.id} ${c.note}`);
    expect(wrong).toEqual([]);
    for (const { c, row } of cases) expect(explainStatus(row), c.id).not.toMatch(/stored status wins/);
  });

  it("each sentence names what the evaluator recorded", () => {
    for (const { c, row } of cases) {
      const text = explainStatus(row);
      const detail = (c.row.detail ?? {}) as Record<string, string>;
      if (c.row.status === "not_evaluated") expect(text, c.id).toBe(`Not evaluated: ${detail.reason}.`);
      else if (detail.noise_downgraded_from)
        expect(text, c.id).toContain(`≈ within noise, was ${detail.noise_downgraded_from.toUpperCase()}`);
      else if (detail.noise_check === "unavailable") expect(text, c.id).toMatch(/nothing was downgraded/);
      else if (detail.nonfinite) expect(text, c.id).toMatch(/past the bad side .* → FAIL/);
      else if (c.input.metric_id === "relationship.orphan_rate" && c.row.status === "info")
        expect(text, c.id).toMatch(/^Documented edge/);
      else if (c.row.status !== "info") expect(text, c.id).toMatch(new RegExp(`→ ${c.row.status.toUpperCase()}`));
    }
  });

  it("a lift with no events reads its bound, not 'not evaluated' (R38)", () => {
    const { row } = cases.find(({ c }) => c.note.startsWith("no copies either side"))!;
    expect(row.value).toBeNull();
    expect(readingOf(row)).toMatchObject({ gateSource: "ci_low", gate: 0, valueUndefined: true });
    expect(explainStatus(row)).toMatch(/→ PASS\.$/);
  });
});
