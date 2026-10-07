/**
 * The run view end to end in jsdom: the real routes, a stubbed BFF. Pins the
 * honesty rules — status explained with inclusive thresholds and the noise
 * floor, lifts gated on ci_low, documented edges INFO, not_evaluated with its
 * reason — and that profiles load only when a drawer opens.
 */
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { isRollup } from "./lib/catalogue";
import { buildGraph } from "./lib/graph";
import { findings, headline, interpretRow } from "./lib/interpret";
import { columnSummaries, countStatuses, countsTotal, familyCards } from "./lib/model";
import { explainStatus, readingOf } from "./lib/reading";
import { countsDetail, evaluation, metric, profile, richDetail, tableEntry } from "./test/fixtures";
import { pageOf, renderAt, stubApi } from "./test/harness";

// The first render lazy-loads the route chunk and the concept batch: give async queries room.
configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

function stubRun(detail = richDetail(), extra: Record<string, unknown> = {}) {
  return stubApi({
    [`/api/evaluations/${detail.evaluation.evaluation_id}`]: detail,
    "/api/evaluations": pageOf(detail.evaluation),
    "/api/relationships": { models: [] },
    [`/api/evaluations/${detail.evaluation.evaluation_id}/profiles`]: [],
    ...extra,
  });
}

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
