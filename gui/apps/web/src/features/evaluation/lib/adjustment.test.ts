/**
 * The adjusted reading of `table.pk_duplicate_rate` (ADR 0038): the evaluator's
 * row is never changed; the generator's verdict is read on top of it. Pins the
 * six verdicts, the evaluator ↔ generator cross-check (1e-6) and that the raw
 * evaluator status survives every reading, and the four join fallbacks from
 * the registry row to the reading. The join's own edge cases are pinned where
 * it lives (`@contracts/adjustments`, adjustments.test.ts).
 */
import { describe, expect, it } from "vitest";

import {
  adjustmentVerdicts,
  joinModelAdjustments,
  type AdjustmentRun,
  type AdjustmentVerdict,
} from "@contracts/adjustments";

import { ADJUSTED_TABLES, adjustedAdjustments, adjustedDetail, adjustmentOf, metric, pkRow } from "../test/fixtures";
import {
  adjustedSummary,
  adjustedTableCount,
  adjustmentFor,
  adjustmentRows,
  CROSS_CHECK_TOLERANCE,
  measuresAgree,
  PK_METRIC,
  readAdjustedPk,
  repeatShareFromEvaluator,
} from "./adjustment";

const share = (table: string) => {
  const t = ADJUSTED_TABLES.find((x) => x.table === table)!;
  return 1 - t.distinct / t.rows;
};

describe("adjustmentFor", () => {
  it("finds a table's adjustment by name, and nothing for a table the payload does not hold", () => {
    const { model_adjustments } = adjustedAdjustments();
    expect(adjustmentFor("E_TABLE", model_adjustments)?.verdict).toBe("faithful");
    expect(adjustmentFor("Z_TABLE", model_adjustments)).toBeUndefined();
    expect(adjustmentFor("E_TABLE", undefined)).toBeUndefined();
    expect(adjustmentFor("E_TABLE", [])).toBeUndefined();
  });
});

describe("repeatShareFromEvaluator: 1 − detail.distinct_keys ÷ rows", () => {
  it("is the generator's measure, computed from the evaluator's own counts", () => {
    // 1,000,000 keys over 2,010,415 rows: 0.5026 — where the evaluator's own rate reads 0.99999.
    const row = pkRow("E_TABLE");
    expect(repeatShareFromEvaluator(row, 2_010_415)).toBeCloseTo(0.5026, 4);
    expect(row.value!).toBeCloseTo(0.99999, 5);
    expect(repeatShareFromEvaluator(pkRow("A_TABLE"), 16_126_514)).toBe(0);
    // The row's own n_synthetic is the denominator when none is given.
    expect(repeatShareFromEvaluator(row)).toBe(repeatShareFromEvaluator(row, row.n_synthetic));
  });

  it("is null without the counts: no detail, no distinct_keys, no rows", () => {
    expect(repeatShareFromEvaluator(pkRow("E_TABLE", { detail: null }), 100)).toBeNull();
    expect(repeatShareFromEvaluator(pkRow("E_TABLE", { detail: { duplicate_rows: 3 } }), 100)).toBeNull();
    expect(repeatShareFromEvaluator(pkRow("E_TABLE"), 0)).toBeNull();
    expect(repeatShareFromEvaluator(pkRow("E_TABLE", { n_synthetic: null }))).toBeNull();
    expect(repeatShareFromEvaluator(null, 100)).toBeNull();
  });
});

describe("the evaluator ↔ generator cross-check", () => {
  it("agrees within 1e-6 and disagrees past it", () => {
    expect(CROSS_CHECK_TOLERANCE).toBe(1e-6);
    expect(measuresAgree(0.5026, 0.5026 + 9e-7)).toBe(true);
    expect(measuresAgree(0.5026, 0.5026 + 2e-6)).toBe(false);
    expect(measuresAgree(0, 0)).toBe(true);
    // One side missing: nothing to check, which is not agreement.
    expect(measuresAgree(null, 0.5)).toBeNull();
    expect(measuresAgree(0.5, null)).toBeNull();
  });

  it("holds exactly on every adjusted table of the recorded launch", () => {
    for (const t of ADJUSTED_TABLES.filter((x) => x.source !== null)) {
      const adjustment = adjustmentOf(t.table);
      expect(measuresAgree(repeatShareFromEvaluator(pkRow(t.table)), adjustment.landing_repeat_share), t.table).toBe(
        true,
      );
    }
  });

  it("a disagreement replaces the verdict with a warning, and the evaluator's status stands", () => {
    const row = pkRow("E_TABLE");
    // The generator recorded another landing share than the evaluator's counts give.
    const adjustment = adjustmentOf("E_TABLE", { landing_repeat_share: 0.42, repeat_share_delta: 0.42 - 0.5027 });
    const reading = readAdjustedPk(row, adjustment)!;
    expect(reading).toMatchObject({ kind: "disagree", status: "fail", tone: "warn", label: "Measures disagree" });
    expect(reading.agrees).toBe(false);
    expect(reading.sentence).toBe(
      "Measures disagree on E_TABLE: the evaluator's counts give a synthetic repeat share of 50.3% (1 − distinct keys ÷ rows) where the generator recorded 42.0%, so no adjusted reading is shown. Check that the evaluation read the rows this run landed (its scope).",
    );
    // NULL key parts are the one legitimate cause: the evaluator leaves them out, the generator groups them.
    const withNulls = pkRow("E_TABLE", { detail: { ...(row.detail as object), null_key_rows: 12 } });
    expect(readAdjustedPk(withNulls, adjustment)!.sentence).toMatch(
      /12 rows have a NULL key part: the evaluator leaves them out of the keys, the generator groups NULL as a value\.$/,
    );
  });
});

describe("readAdjustedPk: one reading per verdict, the evaluator's status always kept", () => {
  const cases: Record<AdjustmentVerdict, () => ReturnType<typeof readAdjustedPk>> = {
    not_adjusted: () => readAdjustedPk(pkRow("B_TABLE"), adjustmentOf("B_TABLE")),
    faithful: () => readAdjustedPk(pkRow("E_TABLE"), adjustmentOf("E_TABLE")),
    under: () => readAdjustedPk(pkRow("C_TABLE"), adjustmentOf("C_TABLE")),
    over: () =>
      readAdjustedPk(
        pkRow("E_TABLE"),
        adjustmentOf("E_TABLE", {
          source_repeat_share: 0.3312,
          repeat_share_delta: share("E_TABLE") - 0.3312,
          within_tolerance: false,
          verdict: "over",
        }),
      ),
    unmeasured: () =>
      readAdjustedPk(
        pkRow("E_TABLE"),
        adjustmentOf("E_TABLE", {
          source_repeat_share: null,
          landing_repeat_share: null,
          repeat_share_delta: null,
          within_tolerance: null,
          verdict: "unmeasured",
        }),
      ),
    unknown: () =>
      readAdjustedPk(pkRow("E_TABLE"), {
        ...adjustmentOf("E_TABLE"),
        run_id: null,
        adjusted: null,
        excluded_rules: [],
        source_repeat_share: null,
        landing_repeat_share: null,
        repeat_share_delta: null,
        within_tolerance: null,
        pk_duplicate_dlq: null,
        verdict: "unknown",
        joined_by: null,
      }),
  };

  it("covers the six verdicts of the contract", () => {
    expect(Object.keys(cases).sort()).toEqual([...adjustmentVerdicts].sort());
  });

  it("not_adjusted: the normal rule, no re-reading", () => {
    expect(cases.not_adjusted()).toBeNull();
  });

  it("faithful: an Adjusted pill at info tone; the finding is informational", () => {
    expect(cases.faithful()).toMatchObject({
      kind: "adjusted",
      verdict: "faithful",
      status: "info",
      tone: "info",
      label: "Adjusted · faithful",
      evaluatorStatus: "fail",
      evaluatorText: "evaluator: FAIL (threshold 0)",
      agrees: true,
    });
  });

  it("under / over: an Adjusted pill at warn tone; the finding is a warning", () => {
    expect(cases.under()).toMatchObject({
      kind: "adjusted",
      verdict: "under",
      status: "warn",
      tone: "warn",
      label: "Adjusted · under",
      evaluatorStatus: "fail",
    });
    expect(cases.over()).toMatchObject({
      kind: "adjusted",
      verdict: "over",
      status: "warn",
      tone: "warn",
      label: "Adjusted · over",
      evaluatorStatus: "fail",
    });
  });

  it("unmeasured / unknown: the evaluator's status unchanged, with an Adjusted? badge", () => {
    for (const verdict of ["unmeasured", "unknown"] as const)
      expect(cases[verdict](), verdict).toMatchObject({
        kind: "question",
        verdict,
        status: "fail",
        tone: null,
        label: "Adjusted?",
        evaluatorStatus: "fail",
      });
    expect(cases.unmeasured()!.sentence).not.toBe(cases.unknown()!.sentence);
  });

  it("unknown stays silent when the registry says the launch adjusted nothing", () => {
    const unknown = { ...adjustmentOf("E_TABLE"), verdict: "unknown" as const, adjusted: null };
    expect(readAdjustedPk(pkRow("E_TABLE"), unknown, { modelAdjusted: false })).toBeNull();
    expect(readAdjustedPk(pkRow("E_TABLE"), unknown, { modelAdjusted: null })?.kind).toBe("question");
    expect(readAdjustedPk(pkRow("E_TABLE"), unknown, { modelAdjusted: true })?.kind).toBe("question");
    // No adjustment at all (the payload never arrived) is not a question to raise on every key.
    expect(readAdjustedPk(pkRow("E_TABLE"), undefined)).toBeNull();
  });

  it("an adjusted table the evaluator PASSED still gets its warning (A_TABLE: fully unique)", () => {
    const reading = readAdjustedPk(pkRow("A_TABLE"), adjustmentOf("A_TABLE"))!;
    expect(reading).toMatchObject({
      kind: "adjusted",
      verdict: "under",
      status: "warn",
      evaluatorStatus: "pass",
      evaluatorText: "evaluator: PASS (threshold 0)",
    });
  });

  it("reads the generator's verdict even where the evaluator wrote no usable PK row", () => {
    const adjustment = adjustmentOf("C_TABLE");
    const none = readAdjustedPk(null, adjustment)!;
    expect(none).toMatchObject({ kind: "adjusted", status: "warn", evaluatorStatus: null, evaluatorText: null });
    expect(none.agrees).toBeNull();
    const skipped = pkRow("C_TABLE", {
      value: null,
      score: null,
      status: "not_evaluated",
      detail: { reason: "sampled mode cannot measure primary-key duplicates" },
    });
    const reading = readAdjustedPk(skipped, adjustment)!;
    expect(reading).toMatchObject({ kind: "adjusted", status: "warn", evaluatorStatus: "not_evaluated" });
    expect(reading.evaluatorText).toBe("evaluator: NOT EVALUATED");
    expect(reading.sentence).toMatch(
      /The evaluator did not grade this key: sampled mode cannot measure primary-key duplicates\.$/,
    );
  });

  it("never mutates the evaluator's row or the adjustment", () => {
    const row = pkRow("E_TABLE");
    const adjustment = adjustmentOf("E_TABLE");
    const before = JSON.stringify([row, adjustment]);
    Object.freeze(row);
    Object.freeze(row.detail);
    Object.freeze(adjustment);
    for (const _ of [1, 2]) readAdjustedPk(row, adjustment);
    expect(JSON.stringify([row, adjustment])).toBe(before);
  });

  it("only re-reads the PK duplicate rate", () => {
    expect(PK_METRIC).toBe("table.pk_duplicate_rate");
    const identity = metric("table.identity_duplicate_rate", { table_name: "E_TABLE", value: 0.2, status: "fail" });
    expect(readAdjustedPk(identity, adjustmentOf("E_TABLE"))).toBeNull();
  });
});

describe("join fallbacks 1–4, from the registry row to the reading", () => {
  // The same validation_runs row reached by a weaker key each time; the reading only changes
  // when no key is left. (The join itself is `joinModelAdjustments`, shared with the BFF.)
  const detail = adjustedDetail();
  const entry = detail.evaluation.tables.find((t) => t.name === "E_TABLE")!;
  const landing = adjustmentOf("E_TABLE").landing_repeat_share!;
  const run: AdjustmentRun = {
    run_id: "sdfb-launch-a-01-E_TABLE",
    landing_table: entry.landing_table,
    created_at: "2026-09-13T08:30:00.000000Z",
    excluded_blocker_rules: "pk.duplicate",
    source_repeat_share: 0.5027,
    landing_repeat_share: landing,
    repeat_share_delta: landing - 0.5027,
    repeat_share_within_tolerance: true,
    dlq_by_rule_map: { "pk.duplicate": 1_010_415 },
  };
  const window = {
    generation_started_at: "2026-09-13T06:10:00.000000Z",
    generation_finished_at: "2026-09-13T08:40:00.000000Z",
  };
  const cases = [
    { step: "1. the table's run id", base: "sdfb-launch-a", runId: run.run_id, ...window, joinedBy: "run_id" },
    { step: "2. the launch's base run id", base: "sdfb-launch-a", runId: null, ...window, joinedBy: "base_run_id" },
    { step: "3. landing table + window", base: null, runId: null, ...window, joinedBy: "window" },
    {
      step: "4. nothing to join on",
      base: null,
      runId: null,
      generation_started_at: null,
      generation_finished_at: null,
      joinedBy: null,
    },
  ] as const;

  it.each(cases)("$step", ({ base, runId, joinedBy, generation_started_at, generation_finished_at }) => {
    const [adjustment] = joinModelAdjustments(
      {
        base_run_id: base,
        generation_started_at,
        generation_finished_at,
        tables: [{ ...entry, run_id: runId }],
      },
      [run],
    );
    expect(adjustment!.joined_by).toBe(joinedBy);
    const reading = readAdjustedPk(pkRow("E_TABLE"), adjustment)!;
    if (joinedBy === null) {
      expect(reading).toMatchObject({ kind: "question", verdict: "unknown", label: "Adjusted?", status: "fail" });
    } else {
      expect(reading).toMatchObject({ kind: "adjusted", verdict: "faithful", status: "info", agrees: true });
      expect(reading.sentence).toBe(readAdjustedPk(pkRow("E_TABLE"), adjustmentOf("E_TABLE"))!.sentence);
    }
  });

  it("another launch's row for the same landing table is never read", () => {
    const [adjustment] = joinModelAdjustments(
      { base_run_id: "sdfb-launch-b", ...window, tables: [{ ...entry, run_id: null }] },
      [run],
    );
    expect(adjustment!.verdict).toBe("unknown");
  });
});

describe("adjustmentRows and the line under the headline", () => {
  const detail = adjustedDetail();
  const rows = adjustmentRows(detail, adjustedAdjustments().model_adjustments);

  it("one row per table: the declared key, both shares, the evaluator's rate and its reading", () => {
    expect(rows.map((r) => [r.table, r.reading?.label ?? null, r.keyColumns.join(",")])).toEqual([
      ["B_TABLE", null, "COL_001"],
      ["E_TABLE", "Adjusted · faithful", "COL_001"],
      ["F_TABLE", "Adjusted · under", "COL_001,COL_002"],
      ["C_TABLE", "Adjusted · under", "COL_001,COL_003"],
      ["A_TABLE", "Adjusted · under", "COL_001,COL_003,COL_004"],
    ]);
    const e = rows.find((r) => r.table === "E_TABLE")!;
    expect(e.row?.status).toBe("fail");
    expect(e.evaluatorShare).toBeCloseTo(0.5026, 4);
    expect(e.adjustment.source_repeat_share).toBe(0.5027);
  });

  it("says how many of the evaluator's FAILs are on adjusted keys, and how they read", () => {
    expect(adjustedSummary(rows)).toBe(
      "3 PK-duplicate FAILs are on keys the launch adjusted (ADR 0038) — 1 faithful, 2 under-reproduced. 1 more adjusted key passes the evaluator's rule and is under-reproduced.",
    );
    const three = rows.filter((r) => r.table !== "A_TABLE");
    expect(adjustedSummary(three)).toBe(
      "3 PK-duplicate FAILs are on keys the launch adjusted (ADR 0038) — 1 faithful, 2 under-reproduced.",
    );
    const one = rows.filter((r) => r.table === "E_TABLE");
    expect(adjustedSummary(one)).toBe("1 PK-duplicate FAIL is on a key the launch adjusted (ADR 0038) — 1 faithful.");
    // Nothing adjusted: no line at all.
    expect(adjustedSummary(rows.filter((r) => r.table === "B_TABLE"))).toBeNull();
    expect(adjustedSummary([])).toBeNull();
  });

  it("counts the keys whose adjustment cannot be read as a question, not as adjusted", () => {
    const unknown = adjustedAdjustments().model_adjustments.map((a) =>
      a.table === "F_TABLE" ? { ...a, verdict: "unknown" as const, adjusted: null, run_id: null } : a,
    );
    expect(adjustedSummary(adjustmentRows(detail, unknown))).toBe(
      "2 PK-duplicate FAILs are on keys the launch adjusted (ADR 0038) — 1 faithful, 1 under-reproduced. 1 more adjusted key passes the evaluator's rule and is under-reproduced. 1 more PK-duplicate FAIL is on a key whose adjustment could not be read (Adjusted?).",
    );
  });

  it("counts the tables validation_runs says were adjusted (the Parameters tab)", () => {
    expect(adjustedTableCount(adjustedAdjustments().model_adjustments)).toBe(4);
    expect(adjustedTableCount([adjustmentOf("B_TABLE")])).toBe(0);
    expect(adjustedTableCount(undefined)).toBe(0);
  });
});
