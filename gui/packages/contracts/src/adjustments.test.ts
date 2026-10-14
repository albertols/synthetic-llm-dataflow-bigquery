/**
 * ADR 0038 adjustments as the BFF derives them: the six verdicts off one
 * `validation_runs` row, and the join of an evaluation's tables to those rows
 * (exact run id, launch base + table suffix, landing table inside the
 * generation window, else unknown) — never across launches.
 */
import { describe, expect, it } from "vitest";

import {
  adjustmentVerdict,
  dottedTable,
  isEvaluatorJobName,
  joinModelAdjustments,
  joinWindow,
  parseExcludedRules,
  tableModelAdjustmentOf,
  type AdjustmentRun,
  type JoinableEvaluation,
} from "./adjustments";

const LANDING = "demo-project.synthetic_data";

function run(table: string, overrides: Partial<AdjustmentRun> = {}): AdjustmentRun {
  return {
    run_id: `sdfb-launch-a-01-${table}`,
    landing_table: `${LANDING}.${table}`,
    created_at: "2026-09-13T08:30:00.000000Z",
    excluded_blocker_rules: "",
    source_repeat_share: null,
    landing_repeat_share: null,
    repeat_share_delta: null,
    repeat_share_within_tolerance: null,
    dlq_by_rule_map: {},
    ...overrides,
  };
}

const adjusted = (source: number, landing: number, within: boolean): Partial<AdjustmentRun> => ({
  excluded_blocker_rules: "pk.duplicate",
  source_repeat_share: source,
  landing_repeat_share: landing,
  repeat_share_delta: landing - source,
  repeat_share_within_tolerance: within,
});

function evaluation(overrides: Partial<JoinableEvaluation> = {}): JoinableEvaluation {
  return {
    base_run_id: "sdfb-launch-a",
    generation_started_at: "2026-09-13T06:10:00.000000Z",
    generation_finished_at: "2026-09-13T08:40:00.000000Z",
    tables: [
      { name: "E_TABLE", landing_table: `${LANDING}.E_TABLE`, run_id: "sdfb-launch-a-01-E_TABLE", role: "driven" },
    ],
    ...overrides,
  };
}

describe("parseExcludedRules (validation_runs.excluded_blocker_rules is comma-separated text)", () => {
  it("splits, trims and drops empties; NULL and the unadjusted table's empty string are no rule", () => {
    expect(parseExcludedRules("pk.duplicate")).toEqual(["pk.duplicate"]);
    expect(parseExcludedRules(" pk.duplicate , row.duplicate,")).toEqual(["pk.duplicate", "row.duplicate"]);
    expect(parseExcludedRules("")).toEqual([]);
    expect(parseExcludedRules(null)).toEqual([]);
    expect(parseExcludedRules(undefined)).toEqual([]);
  });
});

describe("adjustmentVerdict", () => {
  it("unknown: no validation_runs row could be joined", () => {
    expect(adjustmentVerdict(null)).toBe("unknown");
  });

  it("not_adjusted: a row whose gate still weighs pk.duplicate", () => {
    expect(adjustmentVerdict(run("B_TABLE"))).toBe("not_adjusted");
    expect(adjustmentVerdict(run("B_TABLE", { excluded_blocker_rules: null }))).toBe("not_adjusted");
    // Another rule left out of the gate is not this adjustment.
    expect(adjustmentVerdict(run("B_TABLE", { excluded_blocker_rules: "row.duplicate" }))).toBe("not_adjusted");
  });

  it("faithful: excluded and the generator's own verdict is within tolerance", () => {
    expect(adjustmentVerdict(run("E_TABLE", adjusted(0.5027, 0.5026, true)))).toBe("faithful");
  });

  it("under / over: excluded, outside tolerance, by the sign of the delta", () => {
    expect(adjustmentVerdict(run("C_TABLE", adjusted(0.6617, 0.4197, false)))).toBe("under");
    expect(adjustmentVerdict(run("A_TABLE", adjusted(0.5087, 0, false)))).toBe("under");
    expect(adjustmentVerdict(run("D_TABLE", adjusted(0.3312, 0.4583, false)))).toBe("over");
    // A row that kept the two shares but lost its delta still has a direction.
    expect(adjustmentVerdict(run("D_TABLE", { ...adjusted(0.3312, 0.4583, false), repeat_share_delta: null }))).toBe(
      "over",
    );
  });

  it("unmeasured: excluded, but the launch wrote no share (ADR 0038 D4)", () => {
    expect(adjustmentVerdict(run("G_TABLE", { excluded_blocker_rules: "pk.duplicate" }))).toBe("unmeasured");
    // Outside tolerance with no direction to read is not a verdict either.
    expect(
      adjustmentVerdict(
        run("G_TABLE", { excluded_blocker_rules: "pk.duplicate", repeat_share_within_tolerance: false }),
      ),
    ).toBe("unmeasured");
  });
});

describe("tableModelAdjustmentOf", () => {
  it("carries the generator's numbers, the parsed rules and the pk.duplicate count", () => {
    const row = run("E_TABLE", { ...adjusted(0.5027, 0.5026, true), dlq_by_rule_map: { "pk.duplicate": 1_010_415 } });
    expect(tableModelAdjustmentOf("E_TABLE", { run: row, joinedBy: "run_id" })).toEqual({
      table: "E_TABLE",
      run_id: "sdfb-launch-a-01-E_TABLE",
      adjusted: true,
      excluded_rules: ["pk.duplicate"],
      source_repeat_share: 0.5027,
      landing_repeat_share: 0.5026,
      repeat_share_delta: 0.5026 - 0.5027,
      within_tolerance: true,
      pk_duplicate_dlq: 1_010_415,
      verdict: "faithful",
      joined_by: "run_id",
    });
  });

  it("an unjoined table says so: adjusted null, no numbers, verdict unknown", () => {
    expect(tableModelAdjustmentOf("F_TABLE", null)).toEqual({
      table: "F_TABLE",
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
    });
  });

  it("a row whose map has no pk.duplicate entry has no count, not zero", () => {
    const plain = tableModelAdjustmentOf("B_TABLE", { run: run("B_TABLE"), joinedBy: "run_id" });
    expect([plain.adjusted, plain.pk_duplicate_dlq, plain.verdict]).toEqual([false, null, "not_adjusted"]);
    const zero = tableModelAdjustmentOf("A_TABLE", {
      run: run("A_TABLE", { ...adjusted(0.5087, 0, false), dlq_by_rule_map: { "pk.duplicate": 0 } }),
      joinedBy: "run_id",
    });
    expect(zero.pk_duplicate_dlq).toBe(0);
  });
});

describe("joinModelAdjustments: an evaluation's tables to their validation_runs rows", () => {
  const eRow = run("E_TABLE", adjusted(0.5027, 0.5026, true));

  it("1. exact: tables[].run_id = validation_runs.run_id", () => {
    const [e] = joinModelAdjustments(evaluation(), [eRow, run("F_TABLE", { run_id: "sdfb-launch-a-02-F_TABLE" })]);
    expect([e!.table, e!.run_id, e!.joined_by, e!.verdict]).toEqual([
      "E_TABLE",
      "sdfb-launch-a-01-E_TABLE",
      "run_id",
      "faithful",
    ]);
  });

  it("2. launch base + table suffix when the table carries no run id", () => {
    const ev = evaluation({
      tables: [{ name: "E_TABLE", landing_table: `${LANDING}.E_TABLE`, run_id: null, role: "driven" }],
    });
    const [e] = joinModelAdjustments(ev, [eRow]);
    expect([e!.run_id, e!.joined_by, e!.verdict]).toEqual(["sdfb-launch-a-01-E_TABLE", "base_run_id", "faithful"]);
    // A one-table launch runs as the base id itself.
    const single = joinModelAdjustments(ev, [{ ...eRow, run_id: "sdfb-launch-a" }]);
    expect([single[0]!.run_id, single[0]!.joined_by]).toEqual(["sdfb-launch-a", "base_run_id"]);
    // The suffix is the landing table's short name; another table of the launch is not this one.
    const other = joinModelAdjustments(ev, [run("F_TABLE", { run_id: "sdfb-launch-a-02-F_TABLE" })]);
    expect(other[0]!.verdict).toBe("unknown");
  });

  it("3. landing table + created_at inside [generation_started_at, generation_finished_at + 1 h]", () => {
    const ev = evaluation({
      base_run_id: null,
      tables: [{ name: "E_TABLE", landing_table: `${LANDING}.E_TABLE`, run_id: null, role: "driven" }],
    });
    const [e] = joinModelAdjustments(ev, [eRow]);
    expect([e!.run_id, e!.joined_by, e!.verdict]).toEqual(["sdfb-launch-a-01-E_TABLE", "window", "faithful"]);
    // The hour after the job's end is inside; a minute past it is not; neither is before the start.
    const at = (created_at: string) => joinModelAdjustments(ev, [{ ...eRow, created_at }])[0]!.verdict;
    expect(at("2026-09-13T09:40:00.000000Z")).toBe("faithful");
    expect(at("2026-09-13T09:41:00.000000Z")).toBe("unknown");
    expect(at("2026-09-13T06:09:59.999999Z")).toBe("unknown");
    // A Beam table spec (project:dataset.table) names the same landing table.
    const spec = joinModelAdjustments(ev, [{ ...eRow, landing_table: "demo-project:synthetic_data.E_TABLE" }]);
    expect(spec[0]!.joined_by).toBe("window");
    // Without both ends of the window there is nothing to bound the join with.
    expect(joinModelAdjustments({ ...ev, generation_finished_at: null }, [eRow])[0]!.verdict).toBe("unknown");
  });

  it("4. unknown when nothing joins", () => {
    const ev = evaluation({
      base_run_id: null,
      generation_started_at: null,
      generation_finished_at: null,
      tables: [{ name: "E_TABLE", landing_table: `${LANDING}.E_TABLE`, run_id: null, role: "driven" }],
    });
    const [e] = joinModelAdjustments(ev, [eRow]);
    expect([e!.run_id, e!.adjusted, e!.joined_by, e!.verdict]).toEqual([null, null, null, "unknown"]);
    expect(joinModelAdjustments(evaluation(), [])[0]!.verdict).toBe("unknown");
  });

  it("never guesses across launches", () => {
    const otherLaunch = { ...eRow, run_id: "sdfb-launch-b-01-E_TABLE" };
    // A known run id is not replaced by another launch's row for the same landing table and window.
    expect(joinModelAdjustments(evaluation(), [otherLaunch])[0]!.verdict).toBe("unknown");
    // A known base is not replaced either.
    const byBase = evaluation({
      tables: [{ name: "E_TABLE", landing_table: `${LANDING}.E_TABLE`, run_id: null, role: "driven" }],
    });
    expect(joinModelAdjustments(byBase, [otherLaunch])[0]!.verdict).toBe("unknown");
    // With no id at all, two launches inside the window are ambiguous: no verdict, not the newer one.
    const byWindow = { ...byBase, base_run_id: null };
    expect(joinModelAdjustments(byWindow, [eRow, otherLaunch])[0]!.verdict).toBe("unknown");
    // Two rows of ONE run (a retried write) are one launch: the newest is read.
    const retried = { ...eRow, created_at: "2026-09-13T08:35:00.000000Z", ...adjusted(0.5027, 0.4, false) };
    const [e] = joinModelAdjustments(byWindow, [eRow, retried]);
    expect([e!.joined_by, e!.verdict]).toEqual(["window", "under"]);
  });

  it("returns one entry per generated table, in registry order; external parents have no row", () => {
    const ev = evaluation({
      tables: [
        { name: "B_TABLE", landing_table: `${LANDING}.B_TABLE`, run_id: "sdfb-launch-a-00-B_TABLE", role: "root" },
        { name: "E_TABLE", landing_table: `${LANDING}.E_TABLE`, run_id: "sdfb-launch-a-01-E_TABLE", role: "driven" },
        { name: "P_TABLE", landing_table: `${LANDING}.P_TABLE`, run_id: null, role: "external" },
        { name: null, landing_table: `${LANDING}.F_TABLE`, run_id: null, role: "driven" },
      ],
    });
    const out = joinModelAdjustments(ev, [
      eRow,
      run("B_TABLE", { run_id: "sdfb-launch-a-00-B_TABLE" }),
      run("F_TABLE", { run_id: "sdfb-launch-a-02-F_TABLE" }),
    ]);
    expect(out.map((a) => [a.table, a.verdict, a.joined_by])).toEqual([
      ["B_TABLE", "not_adjusted", "run_id"],
      ["E_TABLE", "faithful", "run_id"],
      ["F_TABLE", "not_adjusted", "base_run_id"],
    ]);
  });
});

describe("joinWindow and dottedTable", () => {
  it("adds the hour of slack after the job's end, in the canonical timestamp form", () => {
    expect(joinWindow(evaluation())).toEqual({
      from: "2026-09-13T06:10:00.000000Z",
      to: "2026-09-13T09:40:00.000000Z",
    });
    expect(joinWindow(evaluation({ generation_started_at: null }))).toBeNull();
    // Microseconds survive (a Date alone would drop them).
    expect(joinWindow(evaluation({ generation_finished_at: "2026-09-13T23:30:00.123456Z" }))?.to).toBe(
      "2026-09-14T00:30:00.123456Z",
    );
  });

  it("reads a Beam table spec as the dotted name", () => {
    expect(dottedTable("demo-project:synthetic_data.E_TABLE")).toBe("demo-project.synthetic_data.E_TABLE");
    expect(dottedTable(" demo-project.synthetic_data.E_TABLE ")).toBe("demo-project.synthetic_data.E_TABLE");
  });
});

describe("isEvaluatorJobName", () => {
  it("knows the evaluator's Dataflow job names from a generation launch's", () => {
    expect(isEvaluatorJobName("sdfb-evaluation-v0-5-3-20260913t094200")).toBe(true);
    expect(isEvaluatorJobName("sdfb-eval-0041")).toBe(true);
    expect(isEvaluatorJobName("SDFB-Evaluation-v0-5-3")).toBe(true);
    expect(isEvaluatorJobName("synthetic-sdfb-v0-5-3-gemma4-e4b")).toBe(false);
    expect(isEvaluatorJobName(null)).toBe(false);
    expect(isEvaluatorJobName("")).toBe(false);
  });
});
