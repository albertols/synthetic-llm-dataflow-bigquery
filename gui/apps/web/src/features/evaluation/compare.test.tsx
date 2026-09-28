/**
 * The compare view's two promises: a delta below the noise floor (or between
 * overlapping CIs) reads "≈" and is never judged; runs with a different
 * catalogue, evaluator or encoding plan are flagged "not comparable".
 */
import { configure, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { MetricCell } from "@contracts/api";

import { diffMetric, encodingDiffers, paretoFrontier, pairReasons, slotMap } from "./lib/compare";
import { radarSpec, scoreAxisFloor, topMovers } from "./lib/compareCharts";
import { FALLBACK_TOKENS } from "./lib/tokens";
import { comparison } from "./test/fixtures";
import { EMPTY_PAGE, renderAt, stubApi } from "./test/harness";

configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

function cell(value: number | null, overrides: Partial<MetricCell> = {}): MetricCell {
  return {
    value,
    score: 1,
    status: "pass",
    baseline_value: null,
    noise_floor: null,
    ci_low: null,
    ci_high: null,
    threshold_warn: 0.1,
    threshold_fail: 0.2,
    n_source: 100,
    n_synthetic: 100,
    encoding_plan_digest: "p",
    ...overrides,
  };
}

const ks = (a: MetricCell | null, b: MetricCell | null) => ({
  metric_id: "column.ks",
  table_name: "users",
  level: "column" as const,
  cells: [a, b],
});

describe("noise-aware diff", () => {
  it("reads a delta at or below the larger noise floor as ≈", () => {
    expect(diffMetric(ks(cell(0.03, { noise_floor: 0.01 }), cell(0.045, { noise_floor: 0.02 })), 0, 1).verdict).toBe(
      "approx",
    );
    expect(diffMetric(ks(cell(0.03, { noise_floor: 0.02 }), cell(0.05, { noise_floor: 0.01 })), 0, 1)).toMatchObject({
      verdict: "approx",
      basis: "noise_floor",
      floor: 0.02,
    });
  });

  it("judges a delta beyond the floor by the metric's direction", () => {
    expect(diffMetric(ks(cell(0.03, { noise_floor: 0.01 }), cell(0.09, { noise_floor: 0.01 })), 0, 1).verdict).toBe(
      "worse",
    );
    expect(diffMetric(ks(cell(0.09, { noise_floor: 0.01 }), cell(0.03, { noise_floor: 0.01 })), 0, 1).verdict).toBe(
      "better",
    );
    const coverage = {
      metric_id: "row.coverage",
      table_name: "users",
      level: "row" as const,
      cells: [cell(0.8), cell(0.95)],
    };
    expect(diffMetric(coverage, 0, 1).verdict).toBe("better");
    const target = {
      metric_id: "column.std_ratio",
      table_name: "users",
      level: "column" as const,
      cells: [cell(1.3), cell(0.95)],
    };
    expect(diffMetric(target, 0, 1).verdict).toBe("better");
  });

  it("reads overlapping confidence intervals as ≈ even without a floor", () => {
    const lift = {
      metric_id: "row.memorization_lift",
      table_name: "users",
      level: "row" as const,
      cells: [cell(3, { ci_low: 0.5, ci_high: 12 }), cell(1.1, { ci_low: 0.4, ci_high: null })],
    };
    expect(diffMetric(lift, 0, 1)).toMatchObject({ verdict: "approx", basis: "ci_overlap" });
  });

  it("flags different encoding plans and versions and never judges them", () => {
    const moved = ks(cell(0.02, { noise_floor: 0.005 }), cell(0.15, { noise_floor: 0.005, encoding_plan_digest: "q" }));
    const diff = diffMetric(moved, 0, 1, { versionReasons: ["catalogue 1.0.0 vs 0.9.0"] });
    expect(diff.verdict).toBe("unjudged");
    expect(diff.notComparable).toEqual(["catalogue 1.0.0 vs 0.9.0", "encoding plan of users differs"]);
    const c = comparison();
    expect(pairReasons(c.evaluations[0], c.evaluations[1])).toEqual([
      "catalogue 1.0.0 vs 0.9.0",
      "evaluator 0.2.0 vs 0.1.0",
    ]);
    expect([...encodingDiffers(c, 0, 1)]).toEqual(["users"]);
    const model = {
      metric_id: "model.overall_score",
      table_name: "m",
      level: "model" as const,
      cells: [cell(0.9), cell(0.8)],
    };
    expect(diffMetric(model, 0, 1, { encodingTables: new Set(["users"]) }).notComparable).toEqual([
      "encoding plan differs (users)",
    ]);
  });

  it("says missing and not evaluated instead of inventing a delta", () => {
    expect(diffMetric(ks(cell(0.1), null), 0, 1)).toMatchObject({ verdict: "missing", delta: null });
    expect(diffMetric(ks(cell(0.1), cell(null, { status: "not_evaluated", score: null })), 0, 1)).toMatchObject({
      verdict: "not_evaluated",
      delta: null,
    });
    // Evaluated without a point value (a clean lift gated on ci_low, Ruling R38): no delta, but not "not evaluated".
    expect(diffMetric(ks(cell(0.1), cell(null, { ci_low: 0 })), 0, 1)).toMatchObject({
      verdict: "unjudged",
      delta: null,
    });
  });

  it("keeps colour slots by first appearance and finds the Pareto frontier", () => {
    expect([...slotMap(["b2", "b1", "b2", null])]).toEqual([
      ["b2", 1],
      ["b1", 2],
      ["—", 3],
    ]);
    const a = { x: 0.9, y: 0.5 };
    const b = { x: 0.8, y: 0.9 };
    const c = { x: 0.7, y: 0.4 };
    expect([...paretoFrontier([a, b, c])]).toEqual([a, b]);
  });

  it("never clamps a low family score onto the radar's floor", () => {
    expect(scoreAxisFloor([0.9, 0.5])).toBe(0.5);
    expect(scoreAxisFloor([0.9, 0.23])).toBe(0.2);
    expect(scoreAxisFloor([0.04])).toBe(0);
    expect(scoreAxisFloor([null, undefined])).toBe(0.5);
    const c = comparison({ sameVersions: true });
    c.evaluations[0] = { ...c.evaluations[0]!, privacy_score: 0.2 };
    const low = radarSpec(c, [0, 1], FALLBACK_TOKENS)!;
    expect(low.floor).toBe(0.2);
    const radar = low.option.radar as { indicator: { min: number }[] };
    expect(radar.indicator.every((axis) => axis.min === 0.2)).toBe(true);
    expect(low.data[0]).toMatchObject({ privacy: 0.2 });
    const high = radarSpec(comparison({ sameVersions: true }), [0, 1], FALLBACK_TOKENS)!;
    expect(high.floor).toBe(0.5);
  });

  it("picks the metrics that moved beyond noise, status changes first", () => {
    const c = comparison({ sameVersions: true });
    expect(topMovers(c).map((m) => m.metric_id)).toEqual(["column.tvd"]);
  });
});

describe("the compare view", () => {
  it("puts a not-comparable banner up front and flags rows in the A/B diff", async () => {
    stubApi({ "/api/compare": comparison(), "/api/evaluations": EMPTY_PAGE });
    renderAt(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-a", "eval-b"]))}`);
    expect(await screen.findByText("Not directly comparable")).toBeInTheDocument();
    const reasons = screen.getByTestId("not-comparable-reasons");
    expect(within(reasons).getAllByRole("listitem")).toHaveLength(2);
    const diff = screen.getAllByRole("region", { name: /^A\/B diff/ }).at(-1)!;
    const tvd = within(diff)
      .getByText(/TVD · users\.state/)
      .closest("tr")!;
    expect(tvd).toHaveAttribute("data-verdict", "unjudged");
    expect(within(tvd as HTMLElement).getByText("not comparable")).toBeInTheDocument();
    expect(screen.getByTestId("ab-summary")).toHaveTextContent(/0 better, 0 worse, 1 ≈ within noise/);
  });

  it("shows ≈ for a delta inside the noise floor and judges the rest when comparable", async () => {
    stubApi({ "/api/compare": comparison({ sameVersions: true }), "/api/evaluations": EMPTY_PAGE });
    const user = userEvent.setup();
    renderAt(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-a", "eval-b"]))}`);
    expect(await screen.findByText("Comparable")).toBeInTheDocument();
    expect(screen.queryByText("Not directly comparable")).toBeNull();
    expect(screen.getByTestId("ab-summary")).toHaveTextContent(/0 better, 1 worse, 1 ≈ within noise/);
    const diff = screen.getAllByRole("region", { name: /^A\/B diff/ }).at(-1)!;
    await user.click(
      within(screen.getByRole("radiogroup", { name: "Rows to show" })).getByRole("radio", { name: "All" }),
    );
    const ksRow = within(diff)
      .getByText(/KS · users\.age/)
      .closest("tr")!;
    expect(ksRow).toHaveAttribute("data-verdict", "approx");
    expect(within(ksRow as HTMLElement).getByText(/≈ within noise/)).toBeInTheDocument();
    const tvd = within(diff)
      .getByText(/TVD · users\.state/)
      .closest("tr")!;
    expect(tvd).toHaveAttribute("data-verdict", "worse");
  });
});
