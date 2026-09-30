/**
 * Degenerate data (Review Focus 3): null metrics and not_evaluated rows, empty
 * profiles, a run with no metrics, a single run to compare, a 200-column
 * table. Every case renders a labelled state — never a crash, never "NaN".
 */
import { configure, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ecdfChart, histogramOverlay, qqChart } from "./lib/charts";
import { fmtMetric, fmtSig } from "./lib/format";
import { interpretRow } from "./lib/interpret";
import { columnSummaries, familyCards, heatmapModel } from "./lib/model";
import { readingDomain } from "./lib/scale";
import { readingOf } from "./lib/reading";
import { FALLBACK_TOKENS } from "./lib/tokens";
import { evaluation, metric, richDetail, rollups, wideDetail } from "./test/fixtures";
import { EMPTY_PAGE, pageOf, renderAt, stubApi } from "./test/harness";

configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

function stub(detail: ReturnType<typeof richDetail>, profiles: unknown[] = []) {
  return stubApi({
    [`/api/evaluations/${detail.evaluation.evaluation_id}`]: detail,
    [`/api/evaluations/${detail.evaluation.evaluation_id}/profiles`]: profiles,
    "/api/evaluations": pageOf(detail.evaluation),
    "/api/relationships": { models: [] },
  });
}

describe("degenerate values", () => {
  it("formats null, NaN and infinite values as a dash, never NaN", () => {
    for (const v of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY])
      for (const kind of ["share", "ratio", "bits", "count", "score", "auc", "distance", null]) {
        expect(fmtMetric(v, kind)).toBe("—");
      }
    expect(fmtSig(4.2682874637e-8)).toBe("4.3e-8");
    expect(fmtMetric(0, "share")).toBe("0%");
  });

  it("reads a null-valued metric as not evaluated with a finite axis", () => {
    const row = metric("column.ks", {
      value: null,
      score: null,
      status: "not_evaluated",
      detail: { reason: "empty column" },
    });
    const domain = readingDomain(readingOf(row));
    expect(Number.isFinite(domain.lo) && Number.isFinite(domain.hi) && domain.hi > domain.lo).toBe(true);
    expect(interpretRow(row)).toBe("KS on users was not evaluated: empty column. Not evaluated is not a pass.");
    const noReason = metric("column.ks", { value: null, score: null, status: "not_evaluated" });
    expect(interpretRow(noReason)).toMatch(/no reason recorded/);
  });

  it("refuses to chart missing or mismatched profiles instead of drawing NaN axes", () => {
    const t = FALLBACK_TOKENS;
    const hist = { edges: [1, 2], counts: [1, 2, 3], min: 0, max: 3, nulls: 0, unit: "value" as const };
    expect(histogramOverlay({ source: hist, synthetic: null }, t)).toBeNull();
    expect(histogramOverlay({ source: hist, synthetic: { ...hist, edges: [1, 5] } }, t)).toBeNull();
    expect(ecdfChart(hist, { ...hist, counts: [0, 0, 0] }, 0.01, t)).toBeNull();
    expect(qqChart({ probs: [], values: [], unit: "value" }, { probs: [], values: [], unit: "value" }, t)).toBeNull();
  });

  it("keeps family cards and summaries honest with no metrics at all", () => {
    const detail = { evaluation: evaluation({ overall_score: null, fidelity_score: null }), metrics: [] };
    const cards = familyCards(detail);
    expect(cards.every((c) => c.worst.length === 0 && c.levels.length === 0)).toBe(true);
    expect(cards[0]!.score).toBeNull();
    expect(columnSummaries([])).toEqual([]);
  });
});

describe("degenerate runs", () => {
  it("renders a run whose metrics are all null or not evaluated", async () => {
    const detail = richDetail();
    detail.metrics = detail.metrics.map((m) => ({
      ...m,
      value: null,
      score: null,
      ci_low: null,
      ci_high: null,
      status: "not_evaluated" as const,
      detail: { reason: "reference not verified" },
    }));
    stub(detail);
    renderAt("/evaluation/eval-t001");
    expect(await screen.findByRole("region", { name: "Tables in scope" })).toBeInTheDocument();
    expect((await screen.findAllByText(/reference not verified/)).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/NaN|undefined|Infinity/);
  });

  it("says a RUNNING evaluation has no metrics yet and still draws its tables", async () => {
    const detail = { ...richDetail(), metrics: [], flags: [] };
    detail.evaluation = evaluation({ status: "RUNNING", event: "RUNNING", finished_at: null, overall_score: null });
    stub(detail);
    renderAt("/evaluation/eval-t001");
    expect(await screen.findByText("Metrics are still being computed")).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: /Model graph/ })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Columns" }));
    expect(await screen.findByText("No column metrics")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Privacy" }));
    expect(await screen.findByText("No privacy metrics")).toBeInTheDocument();
  });

  it("shows an empty-profile drawer as a labelled state, with the metrics still listed", async () => {
    stub(richDetail(), []);
    renderAt("/evaluation/eval-t001?tab=columns&column=users.age");
    const drawer = await screen.findByRole("dialog", { name: /users\.age/ });
    expect(await within(drawer).findByText("No profiles stored for this column")).toBeInTheDocument();
    expect(within(drawer).getByRole("heading", { name: /All metrics \(2\)/ })).toBeInTheDocument();
  });

  it("answers an unknown column in the URL with a labelled state", async () => {
    stub(richDetail());
    renderAt("/evaluation/eval-t001?tab=columns&column=users.nope");
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Column not found")).toBeInTheDocument();
  });

  it("answers an unknown evaluation id without requesting its detail", async () => {
    const { calls } = stubApi({ "/api/evaluations": EMPTY_PAGE, "/api/relationships": { models: [] } });
    renderAt("/evaluation/eval-nope");
    expect(await screen.findByText("No evaluation with this id")).toBeInTheDocument();
    expect(calls.some((u) => u.pathname === "/api/evaluations/eval-nope")).toBe(false);
  });

  it("asks for a second evaluation when compare has a single id", async () => {
    stubApi({ "/api/evaluations": EMPTY_PAGE, "/api/compare": {} });
    renderAt(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-a"]))}`);
    expect(await screen.findByText("Add one more evaluation")).toBeInTheDocument();
  });

  it("says so when fewer than two of the compared ids exist", async () => {
    stubApi({
      "/api/evaluations": EMPTY_PAGE,
      "/api/compare": {
        evaluations: [],
        missing: ["a", "b"],
        metrics: [],
        params: [],
        comparability: {
          catalogue_version_same: true,
          evaluator_version_same: true,
          encoding_plans: [],
          not_comparable: [],
        },
      },
    });
    renderAt(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["a", "b"]))}`);
    expect(await screen.findByText("Fewer than two of these evaluations exist")).toBeInTheDocument();
    expect(screen.getByText(/a, b — not in the registry/)).toBeInTheDocument();
  });

  it("renders the 200-column table 50 rows at a time, worst first, without loading profiles", async () => {
    const detail = wideDetail(200);
    const summaries = columnSummaries(detail.metrics);
    expect(summaries).toHaveLength(200);
    expect(summaries[0]!.worst).toBe("fail");
    const model = heatmapModel(summaries, { problemsOnly: true });
    expect(model.rows).toHaveLength(6);

    const { calls } = stub(detail);
    const user = userEvent.setup();
    const started = performance.now();
    renderAt("/evaluation/eval-wide?tab=columns");
    const table = await screen.findByRole("table", { name: /Columns by metric/ });
    expect(within(table).getAllByRole("rowheader")).toHaveLength(50);
    expect(performance.now() - started).toBeLessThan(20_000);
    expect(screen.getByText(/200 of 200 columns/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show 50 more" }));
    expect(within(table).getAllByRole("rowheader")).toHaveLength(100);
    expect(calls.some((u) => u.pathname.endsWith("/profiles"))).toBe(false);
    // Roll-ups exist for a single isolated table.
    expect(rollups("user_features").length).toBe(10);
  });
});
