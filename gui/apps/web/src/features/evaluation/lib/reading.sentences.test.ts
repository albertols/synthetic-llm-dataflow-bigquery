/**
 * Every rule sentence the reviewer found false, pinned against the evaluator's
 * own rows (golden/scoring.json): the R45 "never downgraded" note only on
 * Wilson metrics at an edge reference, the reason for a missing lift value
 * read from ci_low, no "no copies" wording on a row full of copies, R42 held in
 * the edge views, and numbers printed with enough digits to show the
 * comparison they make.
 */
import { describe, expect, it } from "vitest";

import type { MetricRow } from "@contracts/api";
import { catalogueById, type MetricId } from "@contracts/generated/catalogue";
import golden from "@contracts/generated/golden/scoring.json";

import { adjustedAdjustments, adjustedDetail, adjustmentOf, metric, pkRow, richDetail } from "../test/fixtures";
import { readAdjustedPk } from "./adjustment";
import { fmtCompared } from "./format";
import { buildGraph } from "./graph";
import { findings, headline, interpretRow } from "./interpret";
import { explainStatus, isDocumentedEdge, readingOf, ruleStatus } from "./reading";

/** The row the BFF would serve for golden case `id` (Python's to_metric_row output). */
function goldenRow(id: string): MetricRow {
  const c = golden.cases.find((x) => x.id === id)!;
  const meta = catalogueById[c.input.metric_id as MetricId];
  return metric(c.input.metric_id, {
    column_name: meta.level === "field" || meta.level === "column" ? "email" : null,
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

describe("lift sentences read the reason from ci_low (R38)", () => {
  it("s36 — no copies on either side: value NULL, ci_low 0 → PASS", () => {
    const row = goldenRow("s36");
    expect(explainStatus(row)).toBe(
      "ci_low 0× (the gate reads the 95% CI bound; the value is undefined: no copies on either side) < warn 2× → PASS.",
    );
    expect(interpretRow(row)).toMatch(/^Memorization lift \(undefined: no copies on either side\) on users /);
  });

  it("s44 — a field lift with no copies: the same, at field level", () => {
    const row = goldenRow("s44");
    expect(explainStatus(row)).toMatch(/^ci_low 0× \(.*undefined: no copies on either side\) < warn 2× → PASS\.$/);
  });

  it("s80 — copies in R, none in the holdout: an infinite lift that FAILs is never called 'no copies'", () => {
    const row = goldenRow("s80");
    expect(row.value).toBeNull();
    expect(row.status).toBe("fail");
    const text = explainStatus(row);
    expect(text).toBe(
      "ci_low 6.1× (the gate reads the 95% CI bound; the value is infinite: copies in the reference sample, none in the holdout) ≥ fail 5× (inclusive) → FAIL.",
    );
    const sentence = interpretRow(row);
    expect(sentence).toMatch(
      /^Memorization lift \(infinite: copies in the reference sample, none in the holdout\) on users/,
    );
    expect(`${text} ${sentence}`).not.toMatch(/no copies/);
  });

  it("s37 — the same infinite lift under warn passes, still without 'no copies'", () => {
    const text = explainStatus(goldenRow("s37"));
    expect(text).toMatch(/infinite: copies in the reference sample, none in the holdout\) < warn 2× → PASS\.$/);
  });
});

describe("the R45 'never downgraded' note is for Wilson metrics at an edge reference only", () => {
  it("s61 — a Newcombe delta whose CI reaches 0 IS downgraded, and says so", () => {
    const row = goldenRow("s61");
    expect(readingOf(row).edgeReference).toBe(false);
    expect(explainStatus(row)).toBe(
      "value 0.03 ≥ warn 0.02 (inclusive) would be WARN, but its 95% CI 0–0.06 covers the reference 0 → PASS (≈ within noise, was WARN): indistinguishable from sampling noise at this n, so it is scored as no effect (1.0).",
    );
  });

  it("s62 — a Newcombe FAIL whose CI excludes 0 carries no 'never downgraded' note", () => {
    const row = goldenRow("s62");
    const text = explainStatus(row);
    expect(text).toBe("value 0.06 ≥ fail 0.05 (inclusive) → FAIL. Its 95% CI 0.04–0.08 excludes the reference 0.");
    expect(text).not.toMatch(/never downgraded/);
  });

  it("s70 / s67 — a copy rate and an adherence share (Wilson, edge reference) do carry it", () => {
    for (const id of ["s70", "s67"]) {
      const row = goldenRow(id);
      expect(readingOf(row).edgeReference, id).toBe(true);
      expect(explainStatus(row), id).toMatch(/never downgraded as noise\.$/);
    }
  });

  it("s79 — a DeLong AUC past warn without its CI says the noise check had none", () => {
    expect(explainStatus(goldenRow("s79"))).toMatch(
      /→ WARN\. The noise check had no confidence interval, so nothing was downgraded\.$/,
    );
  });
});

describe("R42 in the edge views: only the orphan rate of a documented edge is INFO", () => {
  const edge = "orders(buyer_id) -> users(id)";
  const documented = { enforced: false, role: "documented" };
  const orphan = metric("relationship.orphan_rate", {
    table_name: "orders",
    edge,
    value: 0.002,
    status: "info",
    score: null,
    detail: { ...documented, reason: "documented edge (enforced: false): reported, not gated" },
  });
  const fanout = metric("relationship.fanout_tvd", {
    table_name: "orders",
    edge,
    value: 0.3,
    noise_floor: 0.02,
    noise_floor_method: "tvd_null",
    status: "fail",
    score: 0,
    detail: documented,
  });

  it("the fan-out row of a documented edge is graded and explained as a FAIL", () => {
    expect(isDocumentedEdge(orphan)).toBe(true);
    expect(isDocumentedEdge(fanout)).toBe(false);
    expect(readingOf(fanout).documented).toBe(false);
    expect(ruleStatus(fanout)).toBe("fail");
    expect(explainStatus(fanout)).toBe("value 0.3 ≥ fail 0.2 (inclusive) → FAIL. It clears the noise floor 0.02.");
    expect(interpretRow(fanout)).not.toMatch(/informational|documented/);
    expect(explainStatus(orphan)).toMatch(/^Documented edge \(enforced: false\)/);
  });

  it("the graph keeps the edge documented, its orphan INFO, and its status the worst graded row", () => {
    const detail = richDetail();
    detail.metrics = [...detail.metrics.filter((m) => m.edge !== edge), orphan, fanout];
    const graphEdge = buildGraph(detail).edges.find((e) => e.label === edge)!;
    expect(graphEdge.documented).toBe(true);
    expect(graphEdge.orphan?.status).toBe("info");
    expect(graphEdge.status).toBe("fail");
    // Without a graded row the edge reads INFO.
    detail.metrics = detail.metrics.filter((m) => m !== fanout);
    expect(buildGraph(detail).edges.find((e) => e.label === edge)!.status).toBe("info");
  });
});

describe("R66 in the views: a copy rate is gated only on free text", () => {
  const reason =
    "domain-size collision: a numeric or temporal column meets a dense source by domain size, not by copying; " +
    "reported, not gated (field.value_memorization_lift is the fair test)";
  const copyRate = (column_kind: MetricRow["column_kind"], status: MetricRow["status"], detail: MetricRow["detail"]) =>
    metric("field.substantive_copy_rate", {
      column_name: "age",
      column_kind,
      value: 0.4,
      ci_low: 0.39,
      ci_high: 0.41,
      noise_floor_method: "wilson",
      status,
      score: status === "info" ? null : 0,
      detail,
    });

  it("a numeric column's rate past fail is INFO, explained with the evaluator's reason, never as a FAIL", () => {
    const row = copyRate("numeric", "info", { copies: 40, reason });
    expect(ruleStatus(row)).toBe("info");
    expect(readingOf(row).ungated).toBe(reason);
    expect(explainStatus(row)).toBe(`INFO on a numeric column, whatever the rate — ${reason}.`);
    expect(interpretRow(row)).toMatch(
      /is informational: the copy rate is gated only on free text, and this column is numeric\.$/,
    );
    expect(interpretRow(row)).not.toMatch(/no thresholds/);
  });

  it("the same rate on a free-text column is gated and explained as a FAIL", () => {
    const row = copyRate("text", "fail", { copies: 40 });
    expect(ruleStatus(row)).toBe("fail");
    expect(readingOf(row).ungated).toBeNull();
    expect(explainStatus(row)).toMatch(/→ FAIL\./);
  });
});

describe("an adjusted key reads the generator's verdict, in its own numbers (ADR 0038)", () => {
  const sentence = (table: string) => readAdjustedPk(pkRow(table), adjustmentOf(table))!.sentence;

  it("what the evaluator's row says on its own: a FAIL whose rule expects a unique key", () => {
    expect(interpretRow(pkRow("E_TABLE"))).toBe(
      "PK duplicates 99.999% on E_TABLE — FAIL (any value above 0 fails). Any value above 0 fails: joins on this key fan out and downstream counts double.",
    );
  });

  it("faithful — the copy matched: both shares, the delta and the tolerance", () => {
    expect(sentence("E_TABLE")).toBe(
      "E_TABLE's declared key repeats in the source (50.3% of rows), so the launch adjusted the model (ADR 0038) and copied that share: synthetic 50.3% (Δ −0.0 pt, within ±5 pt). The evaluator's 99.999% counts every row on a repeated key, first rows included.",
    );
  });

  it("under — the copy missed in the direction opposite to the FAIL text: more unique than the source", () => {
    expect(sentence("C_TABLE")).toBe(
      "C_TABLE's declared key repeats in 66.2% of source rows but only 42.0% of synthetic rows (Δ −24.2 pt, outside ±5 pt): the synthetic table is more unique than its source. The evaluator's 67.7% counts every row on a repeated key, first rows included.",
    );
    expect(sentence("F_TABLE")).toBe(
      "F_TABLE's declared key repeats in 66.4% of source rows but only 53.6% of synthetic rows (Δ −12.8 pt, outside ±5 pt): the synthetic table is more unique than its source. The evaluator's 78.6% counts every row on a repeated key, first rows included.",
    );
  });

  it("under, fully unique — the evaluator PASSES what the generator calls a failed copy", () => {
    expect(sentence("A_TABLE")).toBe(
      "A_TABLE's declared key repeats in 50.9% of source rows but in none of the synthetic rows (Δ −50.9 pt, outside ±5 pt): the synthetic table is fully unique where its source is not. The evaluator's 0% passes its own rule, which expects a unique key.",
    );
  });

  it("over — more repeated than the source", () => {
    const landing = adjustmentOf("E_TABLE").landing_repeat_share!;
    const over = adjustmentOf("E_TABLE", {
      source_repeat_share: 0.3312,
      repeat_share_delta: landing - 0.3312,
      within_tolerance: false,
      verdict: "over",
    });
    expect(readAdjustedPk(pkRow("E_TABLE"), over)!.sentence).toBe(
      "E_TABLE's declared key repeats in 33.1% of source rows but 50.3% of synthetic rows (Δ +17.1 pt, outside ±5 pt): the synthetic table repeats its key more than its source. The evaluator's 99.999% counts every row on a repeated key, first rows included.",
    );
  });

  it("unmeasured and unknown — a question added to the evaluator's own sentence, never a verdict", () => {
    const unmeasured = adjustmentOf("E_TABLE", {
      source_repeat_share: null,
      landing_repeat_share: null,
      repeat_share_delta: null,
      within_tolerance: null,
      verdict: "unmeasured",
    });
    expect(readAdjustedPk(pkRow("E_TABLE"), unmeasured)!.sentence).toBe(
      "The launch adjusted this key (ADR 0038: pk.duplicate is left out of E_TABLE's gate) but measured no source repeat share, so whether these repeats copy the source is not known.",
    );
    const unknown = adjustmentOf("E_TABLE", { verdict: "unknown", adjusted: null, run_id: null, joined_by: null });
    expect(readAdjustedPk(pkRow("E_TABLE"), unknown)!.sentence).toBe(
      "No validation_runs row could be joined to E_TABLE, so whether the launch adjusted this key (ADR 0038) is not known: read the launch log's model_adjustment milestones.",
    );
  });

  it("the numbers come from the rows: another launch, another sentence", () => {
    const row = pkRow("E_TABLE", {
      value: 0.6,
      n_synthetic: 1000,
      detail: { duplicate_rows: 600, distinct_keys: 700, null_key_rows: 0, key_columns: ["COL_001"] },
    });
    const adjustment = adjustmentOf("E_TABLE", {
      source_repeat_share: 0.31,
      landing_repeat_share: 0.3,
      repeat_share_delta: 0.3 - 0.31,
      within_tolerance: true,
      verdict: "faithful",
    });
    expect(readAdjustedPk(row, adjustment)!.sentence).toBe(
      "E_TABLE's declared key repeats in the source (31.0% of rows), so the launch adjusted the model (ADR 0038) and copied that share: synthetic 30.0% (Δ −1.0 pt, within ±5 pt). The evaluator's 60% counts every row on a repeated key, first rows included.",
    );
  });
});

describe("findings with the generator's half (ADR 0038)", () => {
  const detail = adjustedDetail();
  const { model_adjustments } = adjustedAdjustments();

  it("without it, the evaluator's three FAILs read as defects and A_TABLE's failed copy is invisible", () => {
    const list = findings(detail);
    const pk = list.filter((f) => f.metricId === "table.pk_duplicate_rate");
    expect(pk.map((f) => [f.target.table, f.severity, f.adjusted])).toEqual([
      ["C_TABLE", "fail", null],
      ["E_TABLE", "fail", null],
      ["F_TABLE", "fail", null],
    ]);
  });

  it("the three FAILs become Adjusted findings, A_TABLE gains a WARN, and the evaluator's status rides along", () => {
    const list = findings(detail, model_adjustments);
    const pk = list.filter((f) => f.metricId === "table.pk_duplicate_rate");
    expect(pk.map((f) => [f.target.table, f.severity, f.adjusted?.label, f.adjusted?.evaluatorStatus])).toEqual([
      ["A_TABLE", "warn", "Adjusted · under", "pass"],
      ["C_TABLE", "warn", "Adjusted · under", "fail"],
      ["F_TABLE", "warn", "Adjusted · under", "fail"],
      ["E_TABLE", "info", "Adjusted · faithful", "fail"],
    ]);
    expect(pk.find((f) => f.target.table === "E_TABLE")!.text).toBe(
      readAdjustedPk(pkRow("E_TABLE"), adjustmentOf("E_TABLE"))!.sentence,
    );
    // The unadjusted root keeps the normal rule, and passes: no finding.
    expect(pk.some((f) => f.target.table === "B_TABLE")).toBe(false);
    // The evidence is on the Relational tab.
    expect(new Set(pk.map((f) => f.target.tab))).toEqual(new Set(["relational"]));
  });

  it("ranks them after the real FAILs and before the documented INFO", () => {
    const order = findings(detail, model_adjustments).map((f) =>
      f.adjusted ? `adjusted:${f.severity}` : `${f.metricId}:${f.severity}`,
    );
    expect(order).toEqual([
      "column.hour_tvd:fail",
      "adjusted:warn",
      "adjusted:warn",
      "adjusted:warn",
      "adjusted:info",
      "relationship.orphan_rate:info",
    ]);
  });

  it("a key whose adjustment cannot be read keeps the evaluator's FAIL, with the question appended", () => {
    const open = model_adjustments.map((a) =>
      a.table === "F_TABLE" ? adjustmentOf("F_TABLE", { verdict: "unknown", adjusted: null, run_id: null }) : a,
    );
    const f = findings(detail, open).find((x) => x.target.table === "F_TABLE")!;
    expect([f.severity, f.adjusted?.kind, f.adjusted?.label]).toEqual(["fail", "question", "Adjusted?"]);
    expect(f.text).toBe(
      `${interpretRow(pkRow("F_TABLE"))} No validation_runs row could be joined to F_TABLE, so whether the launch adjusted this key (ADR 0038) is not known: read the launch log's model_adjustment milestones.`,
    );
    // It ranks with the FAILs it still is.
    expect(findings(detail, open).findIndex((x) => x === f)).toBeLessThan(2);
  });

  it("does not touch the evaluator's rows, the headline or its counts", () => {
    const before = JSON.stringify(detail.metrics);
    findings(detail, model_adjustments);
    expect(JSON.stringify(detail.metrics)).toBe(before);
    // Three PK FAILs and one real one: the adjusted reading moves none of them out of the count.
    expect(headline(detail)).toMatch(/over 11 measured metrics: 4 fail, 6 pass, 1 info\./);
  });
});

describe("rule sentences print the digits the comparison needs", () => {
  it("never 'value 100% ≤ warn 100%'", () => {
    const row = metric("field.type_validity", { value: 0.99985, status: "warn", score: 0.85 });
    const text = explainStatus(row);
    // 0.99985 rounds to 99.98% (four digits already tell it from 99.99%); never to "100%".
    expect(text).toBe("value 99.98% ≤ warn 99.99% (inclusive) → WARN.");
    expect(interpretRow(row)).toMatch(/^Type valid 99\.98% on users .*WARN \(value ≤ warn 99\.99%\)/);
  });

  it("never 'value 0 < warn 1' for a count metric whose warn is 0.5", () => {
    const row = metric("column.distinct_ceiling_hit", { value: 0, status: "pass", score: null });
    expect(explainStatus(row)).toBe("value 0 < warn 0.5 → PASS.");
  });

  it("fmtCompared only adds digits when two different numbers would print alike", () => {
    expect(fmtCompared([0.25, 0.2, 0.1])).toEqual(["0.25", "0.2", "0.1"]);
    expect(fmtCompared([0.9999, 0.99999], "share")).toEqual(["99.99%", "99.999%"]);
    expect(fmtCompared([0.3, 0.3], "share")).toEqual(["30%", "30%"]);
    expect(fmtCompared([0, 0.5], "count")).toEqual(["0", "0.5"]);
    // Not an edge, so never printed as one: 0.99999 is not "100%", 1e-7 is not "0".
    expect(fmtCompared([0.99999, 0.95], "share")).toEqual(["99.999%", "95%"]);
    expect(fmtCompared([1, 0.95], "share")).toEqual(["100%", "95%"]);
    expect(fmtCompared([null, 0.1])).toEqual(["—", "0.1"]);
  });
});
