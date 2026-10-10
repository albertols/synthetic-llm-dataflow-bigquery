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

import { metric, richDetail } from "../test/fixtures";
import { fmtCompared } from "./format";
import { buildGraph } from "./graph";
import { interpretRow } from "./interpret";
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
