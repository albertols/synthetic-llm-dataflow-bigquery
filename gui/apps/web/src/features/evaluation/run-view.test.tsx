/**
 * The run view end to end in jsdom: the real routes, a stubbed BFF. Pins the
 * honesty rules — status explained with inclusive thresholds and the noise
 * floor, lifts gated on ci_low, documented edges INFO, not_evaluated with its
 * reason — and that profiles load only when a drawer opens.
 */
import type { RowFlag } from "@contracts/api";
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { isRollup } from "./lib/catalogue";
import { buildGraph } from "./lib/graph";
import { findings, headline, interpretRow } from "./lib/interpret";
import { columnSummaries, countStatuses, countsTotal, familyCards } from "./lib/model";
import { explainStatus, readingOf } from "./lib/reading";
import {
  adjustedAdjustments,
  adjustedDetail,
  adjustmentOf,
  countsDetail,
  evaluation,
  flag,
  metric,
  profile,
  richDetail,
  tableEntry,
} from "./test/fixtures";
import { pageOf, renderAt, stubApi } from "./test/harness";

// The first render lazy-loads the route chunk and the concept batch: give async queries room.
configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

function stubRun(detail = richDetail(), extra: Record<string, unknown> = {}) {
  const id = detail.evaluation.evaluation_id;
  return stubApi({
    [`/api/evaluations/${id}`]: detail,
    "/api/evaluations": pageOf(detail.evaluation),
    "/api/relationships": { models: [] },
    [`/api/evaluations/${id}/profiles`]: [],
    // No table of the launch was adjusted (ADR 0038) unless a test says otherwise.
    [`/api/evaluations/${id}/adjustments`]: {
      evaluation_id: id,
      model_adjustments: [],
      generation_job_evaluation_id: null,
    },
    ...extra,
  });
}

const ADJUSTMENTS = "/api/evaluations/eval-t001/adjustments";
const ABOUT_ADJUSTED = "About: Adjusted model (ADR 0038)";
const ABOUT_SHARES = "About: Key-repeat share and duplicate rate";

describe("reading a metric row", () => {
  it("explains a PASS under warn by the threshold, and says it is indistinguishable at this n", () => {
    const row = richDetail().metrics.find((m) => m.metric_id === "column.ks")!;
    // The noise check applies to WARN/FAIL crossings only (Ruling R40): under warn is a plain PASS.
    expect(explainStatus(row)).toBe("value 0.004 < warn 0.1 → PASS.");
    expect(interpretRow(row, 10_000)).toBe(
      "KS 0.004 on users.age is below the noise floor 0.005 and near the 10k-sample baseline 0.003 — indistinguishable at this n.",
    );
  });

  it("shows a noise-downgraded crossing as ≈ within noise, was FAIL (scalar floor, Ruling R40)", () => {
    const row = metric("column.ks", {
      column_name: "age",
      value: 0.25,
      noise_floor: 0.3,
      noise_floor_method: "ks_two_sample",
      score: 1,
      detail: { noise_downgraded_from: "fail" },
    });
    const reading = readingOf(row);
    expect(reading.downgradedFrom).toBe("fail");
    expect(explainStatus(row)).toBe(
      "value 0.25 ≥ fail 0.2 (inclusive) would be FAIL, but it is within the noise floor 0.3 of 0 → PASS (≈ within noise, was FAIL): indistinguishable from sampling noise at this n, so it is scored as no effect (1.0).",
    );
    expect(interpretRow(row)).toMatch(
      /— ≈ within noise, was FAIL: indistinguishable at this n, scored as no effect \(1\.0\)\.$/,
    );
    const list = findings({ ...richDetail(), metrics: [row] });
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ severity: "pass", downgradedFrom: "fail" });
    expect(headline({ ...richDetail(), metrics: [row] })).toMatch(/1 pass is ≈ within noise/);
  });

  it("downgrades an interval metric when its CI covers the reference, and says why not when it does not (R41, R45)", () => {
    const covered = metric("column.null_rate_delta", {
      column_name: "age",
      value: 0.03,
      ci_low: 0,
      ci_high: 0.06,
      noise_floor_method: "newcombe",
      detail: { noise_downgraded_from: "warn" },
    });
    expect(explainStatus(covered)).toMatch(
      /^value 0\.03 ≥ warn 0\.02 \(inclusive\) would be WARN, but its 95% CI 0–0\.06 covers the reference 0 → PASS \(≈ within noise, was WARN\)/,
    );
    const copies = metric("field.substantive_copy_rate", {
      column_name: "email",
      value: 0.002,
      ci_low: 0.001295,
      ci_high: 0.003087,
      noise_floor_method: "wilson",
      status: "fail",
      score: 0,
    });
    expect(explainStatus(copies)).toMatch(
      /→ FAIL\. Its 95% CI 0\.13%–0\.309% excludes the reference 0%\. An observed copy .* never downgraded as noise\.$/,
    );
    const noCi = metric("row.near_match_rate", {
      value: 0.002,
      noise_floor_method: "wilson",
      status: "warn",
      score: 0.89,
      detail: { noise_check: "unavailable" },
    });
    expect(explainStatus(noCi)).toMatch(
      /→ WARN\. The noise check had no confidence interval, so nothing was downgraded\.$/,
    );
  });

  it("explains an infinite value past the bad side as FAIL (R43)", () => {
    const row = metric("column.psi", { value: null, status: "fail", score: 0, detail: { nonfinite: "+inf" } });
    expect(explainStatus(row)).toBe("value +∞ lies past the bad side of every threshold → FAIL.");
    expect(interpretRow(row)).toMatch(/FAIL \(value \+∞, past the bad side\)/);
  });

  it("explains a FAIL by the inclusive threshold and the cleared noise floor", () => {
    const row = richDetail().metrics.find((m) => m.metric_id === "column.hour_tvd")!;
    expect(explainStatus(row)).toBe("value 0.25 ≥ fail 0.2 (inclusive) → FAIL. It clears the noise floor 0.006.");
    expect(interpretRow(row, 10_000)).toMatch(
      /^Hour 0\.25 on users\.created_at is above the noise floor 0\.006 and 25× the 10k-sample baseline 0\.01 — FAIL \(value ≥ fail 0\.2\)\./,
    );
  });

  it("gates a lift on its CI lower bound, not on the point estimate", () => {
    const row = richDetail().metrics.find((m) => m.metric_id === "row.near_match_lift")!;
    const reading = readingOf(row);
    expect(reading.gateSource).toBe("ci_low");
    expect(reading.gate).toBe(0.03);
    expect(explainStatus(row)).toMatch(
      /^ci_low 0\.03× \(the gate reads the 95% CI bound; not the value 3×\) < warn 2× → PASS\./,
    );
  });

  it("passes a lift with no copies on either side on ci_low 0, never 'not evaluated' (Ruling R38)", () => {
    const row = richDetail().metrics.find((m) => m.metric_id === "row.exposure_lift")!;
    const reading = readingOf(row);
    expect(reading.valueUndefined).toBe(true);
    expect(reading.gate).toBe(0);
    expect(explainStatus(row)).toBe(
      "ci_low 0× (the gate reads the 95% CI bound; the value is undefined: no copies on either side) < warn 2× → PASS.",
    );
    expect(interpretRow(row)).toMatch(
      /^Exposure lift \(undefined: no copies on either side\) on users \(95% CI 0×–∞; the gate reads ci_low 0×\) — /,
    );
  });

  it("treats a crossing exactly at the threshold as crossed (inclusive)", () => {
    const row = metric("column.tvd", { value: 0.1, status: "warn", noise_floor: 0.01 });
    expect(explainStatus(row)).toMatch(/value 0\.1 ≥ warn 0\.1 \(inclusive\) → WARN/);
  });

  it("reports a documented edge as INFO, never FAIL, and not_evaluated with its reason", () => {
    const detail = richDetail();
    const documented = detail.metrics.find(
      (m) => m.edge === "orders(buyer_id) -> users(id)" && m.metric_id === "relationship.orphan_rate",
    )!;
    expect(explainStatus(documented)).toMatch(/Documented edge \(enforced: false\).*INFO, never as a FAIL/);
    const skipped = detail.metrics.find((m) => m.metric_id === "row.memorization_lift")!;
    expect(explainStatus(skipped)).toBe(
      "Not evaluated: reference not verified: R and H are not the generator's sample.",
    );
    const edge = buildGraph(detail).edges.find((e) => e.label === "orders(buyer_id) -> users(id)")!;
    expect(edge.documented).toBe(true);
    expect(edge.status).toBe("info");
  });

  it("ranks findings FAIL first and says privacy that did not run is unproven", () => {
    const detail = richDetail();
    const list = findings(detail);
    expect(list[0]!.severity).toBe("fail");
    expect(list.map((f) => f.severity)).toContain("not_evaluated");
    expect(headline(detail)).toMatch(/privacy metrics did not run — privacy is unproven here, not passed\./);
  });

  it("badges zero-tolerance integrity FAILs only: duplicate keys and enforced orphans (Ruling R39)", () => {
    const detail = richDetail();
    detail.metrics.push(
      metric("table.pk_duplicate_rate", { value: 0.001, status: "fail", score: 0 }),
      // An integrity FAIL that is not zero-tolerance: graded, but not a key failure.
      metric("field.type_validity", { column_name: "age", value: 0.99, status: "fail", score: 0 }),
    );
    const cards = familyCards(detail);
    expect(cards.find((c) => c.family === "integrity")!.keyFailures).toBe(1);
    expect(cards.find((c) => c.family === "fidelity")!.keyFailures).toBe(0);
  });

  it("builds family cards from the model roll-ups with level counts", () => {
    const cards = familyCards(richDetail());
    expect(cards.map((c) => c.family)).toEqual(["overall", "fidelity", "privacy", "integrity", "diversity"]);
    const privacy = cards.find((c) => c.family === "privacy")!;
    expect(privacy.score).toBe(0.9);
    expect(privacy.levels.map((l) => l.level)).toEqual(["row"]);
    expect(privacy.worst[0]!.status).toBe("not_evaluated");
  });
});

describe("the run view", () => {
  it("shows the header, scope, scorecards with level chips and the interpretation", async () => {
    stubRun();
    renderAt("/evaluation/eval-t001");
    expect(await screen.findByRole("heading", { level: 1, name: "eval-t001" })).toBeInTheDocument();
    const header = await screen.findByRole("region", { name: "Tables in scope" });
    expect(within(header).getAllByText("Scope OK")).toHaveLength(2);
    expect(within(header).getAllByText("Verified")).toHaveLength(2);
    const privacy = await screen.findByRole("region", { name: /Privacy/ });
    expect(within(privacy).getAllByText("0.90").length).toBeGreaterThan(0);
    expect(within(privacy).getByText("ROW")).toBeInTheDocument();
    expect(screen.getByTestId("run-headline")).toHaveTextContent(/Overall score 0\.90/);
    expect(screen.getAllByText(/indistinguishable at this n|FAIL \(value ≥ fail 0\.2\)/).length).toBeGreaterThan(0);
    expect(screen.getByRole("note", { name: "Legend: how metric bars read" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/NaN/);
  });

  it("loads a column's profiles only when its drawer opens", async () => {
    const detail = richDetail();
    const histogram = { edges: [20, 40, 60], counts: [10, 40, 40, 10], min: 12, max: 80, nulls: 0, unit: "value" };
    const { calls } = stubRun(detail, {
      "/api/evaluations/eval-t001/profiles": [
        profile({ column_name: "age", profile_kind: "histogram", side: "source", payload: histogram }),
        profile({
          column_name: "age",
          profile_kind: "histogram",
          side: "synthetic",
          payload: { ...histogram, counts: [11, 39, 41, 9] },
        }),
      ],
    });
    const user = userEvent.setup();
    renderAt("/evaluation/eval-t001?tab=columns");
    const heatmap = await screen.findByRole("table", { name: /Columns by metric/ });
    const rows = within(heatmap).getAllByRole("rowheader");
    expect(rows[0]).toHaveTextContent("users.created_at");
    expect(calls.some((u) => u.pathname.endsWith("/profiles"))).toBe(false);

    await user.click(within(heatmap).getByRole("button", { name: /Open users\.age/ }));
    const drawer = await screen.findByRole("dialog", { name: /users\.age/ });
    await waitFor(() =>
      expect(calls.some((u) => u.pathname.endsWith("/profiles") && u.searchParams.get("column") === "age")).toBe(true),
    );
    expect(await within(drawer).findByRole("figure", { name: /ECDF with the KS gap/ })).toBeInTheDocument();
    expect(within(drawer).getByText(/bracket 0\.004–0\.02/)).toBeInTheDocument();
    expect(
      within(drawer).getByRole("figure", { name: /Null, empty and zero rates with Wilson intervals/ }),
    ).toBeInTheDocument();
  });

  it("puts the privacy disclaimer first and gates lifts on ci_low", async () => {
    stubRun();
    renderAt("/evaluation/eval-t001?tab=privacy");
    expect(await screen.findByText("Risk indicators, not guarantees")).toBeInTheDocument();
    const lifts = screen.getByRole("figure", { name: /Memorization lifts/ });
    expect(within(lifts).getByText(/3× · ci_low 0\.03 · open above/)).toBeInTheDocument();
    // No copies on either side: undefined point, still gated (and passed) on ci_low 0 (Ruling R38).
    expect(within(lifts).getByText(/undefined · ci_low 0 · open above · m_R\/m_H 0\/0/)).toBeInTheDocument();
    expect(within(lifts).getByText(/not evaluated — reference not verified/)).toBeInTheDocument();
    const flags = screen.getAllByRole("region", { name: /^Flagged rows/ }).at(-1)!;
    // The evaluator's keyed-hash label, whole: `h:` and eight hex digits.
    expect(within(flags).getAllByText("h:0123abcd")).toHaveLength(2);
  });

  it("reads a flagged row as the evaluator writes it: the key, the hash, the distance and its detail", async () => {
    // Three rows an offline run of the evaluator wrote (`sdfb-eval run --fixture_dir`, invented thelook
    // rows with half of the synthetic side copied from the source), with the evaluation's own ids
    // replaced; the fourth has the shape `beam/privacy.py` gives a detectable row (none was flagged there).
    const real: Partial<RowFlag>[] = [
      {
        check: "exact_copy",
        rank: 1,
        synthetic_key: { order_id: 10212 },
        source_key_hash: "h:97e4cc53",
        source_set: "E",
        distance: 0.0,
        score: 1.0,
        detail: { full_row: true },
      },
      {
        check: "near_copy",
        rank: 1,
        synthetic_key: { order_id: 54387 },
        source_key_hash: "h:28253258",
        source_set: "R",
        distance: 0.2,
        score: 0.8,
        detail: { differs_in: "created_at" },
      },
      {
        check: "nearest_record",
        rank: 1,
        synthetic_key: { order_id: 12431 },
        source_key_hash: "h:4c62fd40",
        source_set: "R",
        distance: 0.0,
        score: 1.0,
        detail: { holdout_distance: 0.20552501678466797, multiplicity: 1, nndr: 0.0 },
      },
      {
        check: "detectable",
        rank: 1,
        synthetic_key: { order_id: 70001 },
        source_key_hash: null,
        source_set: null,
        distance: null,
        score: 0.97,
        detail: { p_synthetic: 0.97, multiplicity: 3 },
      },
    ];
    const detail = richDetail();
    detail.flags = real.map((f) => flag({ table_name: "orders", ...f }));
    stubRun(detail);
    renderAt("/evaluation/eval-t001?tab=privacy");
    const region = (await screen.findAllByRole("region", { name: /^Flagged rows/ })).at(-1)!;
    const cells = (name: string) =>
      within(within(region).getByText(name).closest("tr")!)
        .getAllByRole("cell")
        .map((cell) => cell.textContent);
    expect(cells("exact copy")).toEqual([
      "exact copy",
      "1",
      "orders",
      "order_id=10212",
      "h:97e4cc53",
      "E",
      "0",
      "whole row, key included",
    ]);
    expect(cells("near copy")).toEqual([
      "near copy",
      "1",
      "orders",
      "order_id=54387",
      "h:28253258",
      "R",
      "0.2",
      "differs in created_at",
    ]);
    expect(cells("nearest record")).toEqual([
      "nearest record",
      "1",
      "orders",
      "order_id=12431",
      "h:4c62fd40",
      "R",
      "0",
      "NNDR 0 · nearest holdout 0.206",
    ]);
    expect(cells("detectable")).toEqual([
      "detectable",
      "1",
      "orders",
      "order_id=70001",
      "—",
      "—",
      "—",
      "p(synthetic) 0.97 · 3 identical rows",
    ]);
  });

  it("shows documented edges as INFO next to the source orphan rate", async () => {
    stubRun();
    renderAt("/evaluation/eval-t001?tab=relational");
    const table = await screen.findByRole("region", { name: "Orphan rate per foreign key" });
    const documented = within(table).getByText("orders(buyer_id) -> users(id)").closest("tr")!;
    expect(within(documented as HTMLElement).getByText("Info · documented")).toBeInTheDocument();
    expect(within(documented as HTMLElement).getByText("1.5%")).toBeInTheDocument();
    expect(within(table).queryByText("Fail")).toBeNull();
  });

  it("surfaces scope problems and an unverified reference", async () => {
    const detail = richDetail();
    detail.evaluation = evaluation({
      status: "PARTIAL",
      status_reason: "reference digest did not match",
      tables: [
        tableEntry("users", { reference_verified: false }),
        tableEntry("orders", { scope_status: "contaminated", scope_ok: false, scope_reason: "rows from another run" }),
      ],
    });
    stubRun(detail);
    renderAt("/evaluation/eval-t001");
    expect(await screen.findByText("Scope needs attention")).toBeInTheDocument();
    expect(screen.getByText(/rows from another run/)).toBeInTheDocument();
    expect(screen.getByText("Reference not verified")).toBeInTheDocument();
    expect(screen.getAllByText("Contaminated").length).toBeGreaterThan(0);
  });

  it("summarises columns worst first", () => {
    const summaries = columnSummaries(richDetail().metrics);
    expect(summaries.map((s) => s.key)).toEqual(["users.created_at", "users.age"]);
    expect(summaries[1]!.worst).toBe("pass");
  });
});

describe("adjusted keys in the run view (ADR 0038)", () => {
  const findingOf = (list: HTMLElement, table: string) =>
    within(list)
      .getByText(new RegExp(`^${table}'s declared key`))
      .closest("li")!;

  it("the three FAIL findings render as Adjusted, A_TABLE gains a WARN, and the evaluator's status stays", async () => {
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: adjustedAdjustments() });
    renderAt("/evaluation/eval-t001");
    const list = await screen.findByRole("list", { name: "Findings, most severe first" });

    // The line under the headline; the headline itself still counts the evaluator's four FAILs.
    expect(screen.getByTestId("adjusted-summary")).toHaveTextContent(
      "3 PK-duplicate FAILs are on keys the launch adjusted (ADR 0038) — 1 faithful, 2 under-reproduced. 1 more adjusted key passes the evaluator's rule and is under-reproduced.",
    );
    expect(screen.getByTestId("run-headline")).toHaveTextContent(/over 11 measured metrics: 4 fail, 6 pass, 1 info\./);
    expect(
      within(screen.getByTestId("adjusted-summary").closest("p")!).getByRole("button", { name: ABOUT_ADJUSTED }),
    ).toBeInTheDocument();

    // E_TABLE: faithful — an Adjusted pill at info tone where the Fail pill was.
    const e = findingOf(list, "E_TABLE");
    expect(e).toHaveAttribute("data-severity", "info");
    expect(e).toHaveAttribute("data-evaluator-status", "fail");
    const ePill = within(e).getByText("Adjusted · faithful").closest("[data-status]")!;
    expect(ePill).toHaveAttribute("data-status", "adjusted");
    expect(ePill).toHaveAttribute("data-tone", "info");
    expect(within(e).getByText("evaluator: FAIL (threshold 0)")).toBeInTheDocument();
    expect(within(e).queryByText("Fail")).toBeNull();
    expect(e).toHaveTextContent("copied that share: synthetic 50.3% (Δ −0.0 pt, within ±5 pt)");

    // C_TABLE and F_TABLE: under-reproduced — Adjusted at warn tone.
    for (const table of ["C_TABLE", "F_TABLE"]) {
      const item = findingOf(list, table);
      expect(item, table).toHaveAttribute("data-severity", "warn");
      const pill = within(item).getByText("Adjusted · under").closest("[data-status]")!;
      expect(pill, table).toHaveAttribute("data-tone", "warn");
      expect(within(item).getByText("evaluator: FAIL (threshold 0)")).toBeInTheDocument();
      expect(within(item).queryByText("Fail")).toBeNull();
    }

    // A_TABLE: the evaluator passed it; the generator's verdict is a finding all the same.
    const a = findingOf(list, "A_TABLE");
    expect(a).toHaveAttribute("data-severity", "warn");
    expect(a).toHaveAttribute("data-evaluator-status", "pass");
    expect(within(a).getByText("Adjusted · under")).toBeInTheDocument();
    expect(within(a).getByText("evaluator: PASS (threshold 0)")).toBeInTheDocument();
    expect(a).toHaveTextContent("in none of the synthetic rows (Δ −50.9 pt, outside ±5 pt)");

    // Every Adjusted pill has its (i); the real FAIL and the documented INFO keep their own pills.
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.getAttribute("data-severity"))).toEqual([
      "fail",
      "warn",
      "warn",
      "warn",
      "info",
      "info",
    ]);
    for (const item of items.slice(1, 5))
      expect(within(item).getByRole("button", { name: ABOUT_ADJUSTED })).toBeInTheDocument();
    expect(within(items[0]!).getByText("Fail")).toBeInTheDocument();
    expect(within(items[5]!).getByText("Info")).toBeInTheDocument();

    // The integrity card still counts the evaluator's three key failures, and says where they sit.
    const integrity = screen.getByRole("region", { name: /^Integrity/ });
    expect(within(integrity).getByText("3 key failures")).toBeInTheDocument();
    expect(within(integrity).getByTestId("adjusted-key-failures")).toHaveTextContent("3 on adjusted keys");
    expect(document.body.textContent).not.toMatch(/NaN/);
  });

  it("the scorecard's PK row shows the Adjusted pill, both shares, the delta, the tolerance and the raw value", async () => {
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: adjustedAdjustments() });
    renderAt("/evaluation/eval-t001");
    const integrity = await screen.findByRole("region", { name: /^Integrity/ });
    const row = integrity.querySelector<HTMLElement>('li[data-adjusted="true"]')!;
    expect(row).not.toBeNull();
    // "Look first" lists one row per metric id: the first PK row by key is C_TABLE.
    expect(within(row).getByText("Adjusted · under")).toBeInTheDocument();
    expect(within(row).getByText("evaluator: FAIL (threshold 0)")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: ABOUT_ADJUSTED })).toBeInTheDocument();
    expect(row).toHaveTextContent(
      "source 66.2% · synthetic 42.0% · Δ −24.2 pt · tolerance ±5 pt · evaluator value 67.7%",
    );
  });

  it("the Relational tab lists every table above the orphan rates, with the strip and the readouts", async () => {
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: adjustedAdjustments() });
    renderAt("/evaluation/eval-t001?tab=relational");
    const section = await screen.findByRole("region", { name: /^Model adjustments \(ADR 0038\)/ });
    const orphans = screen.getByRole("region", { name: /^Orphan rates/ });
    expect(section.compareDocumentPosition(orphans) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(section).getAllByRole("button", { name: ABOUT_ADJUSTED }).length).toBeGreaterThan(1);
    expect(within(section).getAllByRole("button", { name: ABOUT_SHARES }).length).toBeGreaterThan(1);

    const table = within(section).getByRole("region", { name: "Model adjustments per table" });
    const cells = (name: string) =>
      within(within(table).getByText(name).closest("tr")!)
        .getAllByRole("cell")
        .map((cell) => cell.textContent);
    expect(cells("E_TABLE")).toEqual([
      "E_TABLEjoined by run id",
      "COL_001",
      "50.3%",
      "50.3%",
      "−0.0 pt",
      expect.stringContaining("Adjusted · faithful"),
      "99.999%",
      "Fail",
      "pk.duplicate1,010,415 counted, none removed",
    ]);
    expect(cells("C_TABLE").slice(1, 8)).toEqual([
      "COL_001, COL_003",
      "66.2%",
      "42.0%",
      "−24.2 pt",
      expect.stringContaining("Adjusted · under"),
      "67.7%",
      "Fail",
    ]);
    // The evaluator PASSED A_TABLE; the generator did not.
    expect(cells("A_TABLE").slice(2, 8)).toEqual([
      "50.9%",
      "0.0%",
      "−50.9 pt",
      expect.stringContaining("Adjusted · under"),
      "0%",
      "Pass",
    ]);
    // The root was not adjusted: the normal rule, and no share.
    expect(cells("B_TABLE").slice(2, 9)).toEqual([
      "—",
      "—",
      "—",
      "not adjusted: a unique key is expected",
      "0%",
      "Pass",
      "—",
    ]);
    // Every column header but the table's name explains itself, and so does every pill.
    const headers = within(table).getAllByRole("columnheader");
    expect(headers.map((h) => h.textContent?.trim())).toEqual([
      "Table",
      "Declared PK",
      "Source share",
      "Synthetic share",
      "Δ (syn − src)",
      "Verdict",
      "Evaluator duplicate rate",
      "Evaluator status",
      "Excluded rules",
    ]);
    for (const header of headers.slice(1))
      expect(within(header).getByRole("button", { name: /^About: / }), header.textContent ?? "").toBeInTheDocument();
    for (const i of [2, 3, 4]) expect(within(headers[i]!).getByRole("button", { name: ABOUT_SHARES })).toBeVisible();
    for (const i of [1, 5, 8]) expect(within(headers[i]!).getByRole("button", { name: ABOUT_ADJUSTED })).toBeVisible();
    const pills = [...section.querySelectorAll<HTMLElement>('[data-slot="adjustment"]')];
    expect(pills.length).toBeGreaterThanOrEqual(12);
    for (const pill of pills) expect(within(pill).getByRole("button", { name: ABOUT_ADJUSTED })).toBeInTheDocument();

    // The strip: one row per adjusted table, the source's share as the tick, ±5 pt as the band.
    const strip = within(section).getByRole("figure", { name: /Synthetic key-repeat share against the source's/ });
    expect(within(strip).getByRole("button", { name: ABOUT_SHARES })).toBeInTheDocument();
    expect(strip.querySelectorAll('[data-slot="interval-ref"]')).toHaveLength(4);
    expect(strip.querySelectorAll('[data-slot="interval-band"]')).toHaveLength(4);
    // Each row says its tick and its dot in numbers (source → synthetic) and the difference.
    const stripRow = within(strip).getByText("C_TABLE").closest("div.grid")!;
    expect(stripRow).toHaveTextContent("66.2% → 42.0%");
    expect(stripRow).toHaveTextContent("Δ −24.2 pt");
    expect(within(stripRow as HTMLElement).getByText("Adjusted · under")).toBeInTheDocument();
    expect(within(strip).getByRole("button", { name: "View data" })).toBeInTheDocument();

    // The readout keeps the evaluator's raw value, status and rule next to the generator's half.
    const readout = section.querySelector<HTMLElement>('article[data-metric="table.pk_duplicate_rate"]')!;
    expect(readout).toHaveAttribute("data-status", "fail");
    const figures = Object.fromEntries(
      [...readout.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]),
    );
    expect(figures).toMatchObject({
      Value: "100%",
      "Warn / fail": "0% / 0%",
      "Source share": "50.3%",
      "Synthetic share": "50.3%",
      "Δ (syn − src)": "−0.0 pt",
      Tolerance: "±5 pt",
      Evaluator: "FAIL",
    });
    expect(within(readout).getByText("evaluator: FAIL (threshold 0)")).toBeInTheDocument();
    expect(readout).toHaveTextContent(/Evaluator's rule: value 99\.999% > 0 → FAIL/);
    expect(within(readout).getByRole("button", { name: ABOUT_SHARES })).toBeInTheDocument();

    // Orphan rates are another question: unchanged, and nothing fails there.
    expect(within(orphans).queryByText("Fail")).toBeNull();
    expect(within(orphans).getByText("Info · documented")).toBeInTheDocument();
  });

  it("marks adjusted tables in the model graph and explains the chip in its legend", async () => {
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: adjustedAdjustments() });
    renderAt("/evaluation/eval-t001");
    const graph = await screen.findByRole("figure", { name: /^Model graph/ });
    const node = (table: string) => graph.querySelector<SVGGElement>(`g[data-table="${table}"]`)!;
    expect(node("E_TABLE").querySelector('[data-slot="pk-adjusted"]')).toHaveTextContent("PK adj. ≈");
    expect(node("C_TABLE").querySelector('[data-slot="pk-adjusted"]')).toHaveTextContent("PK adj. ↓");
    expect(node("B_TABLE").querySelector('[data-slot="pk-adjusted"]')).toBeNull();
    expect(node("E_TABLE")).toHaveAccessibleName(/primary key: Adjusted · faithful \(ADR 0038\)/);
    const legend = within(graph).getByTestId("pk-adjusted-legend");
    expect(legend).toHaveTextContent("PK adj.: the launch adjusted the declared key");
    expect(within(legend).getByRole("button", { name: ABOUT_ADJUSTED })).toBeInTheDocument();
  });

  it("Parameters: a NULL model_adjusted is filled from validation_runs, and says where from", async () => {
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: adjustedAdjustments() });
    renderAt("/evaluation/eval-t001?tab=params");
    const inferred = await screen.findByTestId("model-adjusted-inferred");
    expect(inferred).toHaveTextContent("— (validation_runs says adjusted on 4 tables)");
    expect(within(inferred).getByRole("button", { name: ABOUT_ADJUSTED })).toBeInTheDocument();
  });

  it("a key whose adjustment cannot be read keeps the evaluator's FAIL, with an Adjusted? badge", async () => {
    const adjustments = adjustedAdjustments();
    adjustments.model_adjustments = adjustments.model_adjustments.map((a) =>
      a.table === "F_TABLE"
        ? adjustmentOf("F_TABLE", { verdict: "unknown", adjusted: null, run_id: null, joined_by: null })
        : a,
    );
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: adjustments });
    renderAt("/evaluation/eval-t001");
    const list = await screen.findByRole("list", { name: "Findings, most severe first" });
    const f = within(list)
      .getByText(/^PK duplicates 78\.6% on F_TABLE — FAIL/)
      .closest("li")!;
    expect(f).toHaveAttribute("data-severity", "fail");
    expect(within(f).getByText("Fail")).toBeInTheDocument();
    const badge = within(f).getByText("Adjusted?");
    expect(badge.closest("[data-slot='adjustment']")).toHaveAttribute("data-kind", "question");
    expect(within(f).getByRole("button", { name: ABOUT_ADJUSTED })).toBeInTheDocument();
    expect(f).toHaveTextContent("No validation_runs row could be joined to F_TABLE");
  });

  it("without the adjustments route the page still renders, and says the generator's half is missing", async () => {
    // No stub for the adjustments route: the harness answers 404.
    stubRun(adjustedDetail(), { [ADJUSTMENTS]: undefined });
    renderAt("/evaluation/eval-t001?tab=relational");
    expect(await screen.findByText("The launch's model adjustments could not be loaded")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /^Model adjustments/ })).toBeNull();
    expect(screen.getByRole("region", { name: /^Orphan rates/ })).toBeInTheDocument();
  });

  it("warns when the registry row names an evaluator job as its generation job", async () => {
    const detail = adjustedDetail();
    detail.evaluation.generation_job_name = "sdfb-evaluation-v0-5-3-20260913t090211";
    stubRun(detail, { [ADJUSTMENTS]: adjustedAdjustments({ generation_job_evaluation_id: "eval-t000" }) });
    renderAt("/evaluation/eval-t001");
    const banner = (await screen.findByText(/^Wrong generation job/)).closest("[role='note']")!;
    expect(banner).toHaveAttribute("data-tone", "danger");
    expect(banner).toHaveTextContent(
      "This evaluation points at an evaluator job, not a generation launch; scope and adjustments cannot be joined.",
    );
    expect(banner).toHaveTextContent("sdfb-evaluation-v0-5-3-20260913t090211");
    expect(within(banner as HTMLElement).getByRole("link", { name: "eval-t000" })).toHaveAttribute(
      "href",
      "/evaluation/eval-t000",
    );
  });

  it("the same banner on the other evaluation's job id alone, and none on a generation launch", async () => {
    const detail = adjustedDetail();
    stubRun(detail, { [ADJUSTMENTS]: adjustedAdjustments({ generation_job_evaluation_id: "eval-t000" }) });
    const first = renderAt("/evaluation/eval-t001");
    expect(await screen.findByText(/^Wrong generation job/)).toBeInTheDocument();
    first.unmount();
    stubRun(detail, { [ADJUSTMENTS]: adjustedAdjustments() });
    renderAt("/evaluation/eval-t001");
    await screen.findByTestId("run-headline");
    expect(screen.queryByText(/^Wrong generation job/)).toBeNull();
  });

  it("an evaluation whose launch adjusted nothing shows none of it", async () => {
    stubRun();
    renderAt("/evaluation/eval-t001?tab=relational");
    await screen.findByRole("region", { name: /^Orphan rates/ });
    expect(screen.queryByRole("region", { name: /^Model adjustments/ })).toBeNull();
    expect(screen.queryByText(/Adjusted/)).toBeNull();
    expect(screen.queryByText("The launch's model adjustments could not be loaded")).toBeNull();
  });
});

describe("count breakdowns add up to their totals", () => {
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

  it("the headline's buckets, INFO and other included, sum to its measured total", () => {
    const detail = countsDetail();
    const measured = detail.metrics.filter((m) => !isRollup(m.metric_id));
    const counts = countStatuses(measured);
    expect(counts.info).toBeGreaterThan(0);
    expect(counts.other).toBe(1);
    const match = /over ([\d,]+) measured metrics: ([^.]+)\./.exec(headline(detail))!;
    const total = Number(match[1]!.replaceAll(",", ""));
    const parts = match[2]!.split(", ").map((part) => Number(part.split(" ")[0]!.replaceAll(",", "")));
    expect(total).toBe(measured.length);
    expect(sum(parts)).toBe(total);
    expect(match[2]).toMatch(/\d+ info/);
    expect(match[2]).toMatch(/1 other status/);
    expect(countsTotal(counts)).toBe(measured.length);
  });

  it("every rendered breakdown — KPI footnote and each level row — sums to its stated total", async () => {
    const detail = countsDetail();
    stubRun(detail);
    renderAt("/evaluation/eval-t001");
    await screen.findByRole("region", { name: "Tables in scope" });
    const headlineText = (await screen.findByTestId("run-headline")).textContent ?? "";
    const headlineTotal = Number(/over ([\d,]+) measured/.exec(headlineText)![1]!.replaceAll(",", ""));
    const breakdowns = [...document.querySelectorAll<HTMLElement>('[data-slot="status-counts"]')];
    // The KPI footnote plus one row per level in each of the five cards.
    expect(breakdowns.length).toBeGreaterThan(5);
    for (const el of breakdowns) {
      const total = Number(el.dataset.total);
      const counts = [...el.querySelectorAll<HTMLElement>("[data-count]")].map((b) => Number(b.dataset.count));
      expect(sum(counts), el.textContent ?? "").toBe(total);
      expect(el.textContent).toContain(`= ${total.toLocaleString("en-US")}`);
    }
    const kpi = breakdowns.find((el) => el.textContent?.includes("measured"))!;
    expect(Number(kpi.dataset.total)).toBe(headlineTotal);
    expect(kpi.querySelector('[data-bucket="info"]')).not.toBeNull();
    expect(kpi.querySelector('[data-bucket="other"]')).not.toBeNull();
    expect(breakdowns.some((el) => el !== kpi && el.querySelector('[data-bucket="info"]'))).toBe(true);
    expect(breakdowns.some((el) => el !== kpi && el.querySelector('[data-bucket="other"]'))).toBe(true);
  });
});
