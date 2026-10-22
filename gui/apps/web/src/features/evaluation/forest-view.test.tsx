/**
 * The run view of a whole-model launch of 120 unrelated tables (ADR 0036, ADR 0042) in jsdom: the
 * real routes, a stubbed BFF. Tasks 3–8 each add their part of the views here.
 */
import type { EvaluationDetail } from "@contracts/api";
import type { GenerationRecord } from "@contracts/record";
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  adjustmentOf,
  evaluation,
  forestDetail,
  forestRecord,
  noRecord,
  pkRow,
  rollups,
  tableEntry,
} from "./test/fixtures";
import { pageOf, renderAt, stubApi } from "./test/harness";

configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

const ID = "eval-t001";

function stubForest(
  detail: EvaluationDetail = forestDetail(),
  record: GenerationRecord | null = forestRecord(),
  models: unknown[] = [],
  adjustments: unknown[] = [],
) {
  return stubApi({
    [`/api/evaluations/${ID}`]: detail,
    "/api/evaluations": pageOf(detail.evaluation),
    "/api/relationships": { models },
    [`/api/evaluations/${ID}/profiles`]: [],
    [`/api/evaluations/${ID}/adjustments`]: {
      evaluation_id: ID,
      model_adjustments: adjustments,
      generation_job_evaluation_id: null,
    },
    [`/api/evaluations/${ID}/record`]: record ?? noRecord(ID),
  });
}

const tiles = (scope: HTMLElement) => [...scope.querySelectorAll<HTMLElement>('[data-slot="table-tile"]')];
const tile = (scope: HTMLElement, name: string) =>
  scope.querySelector<HTMLElement>(`[data-slot="table-tile"][data-table="${name}"]`)!;

describe("a forest of isolated tables (ADR 0036)", () => {
  it("is a bounded tile grid, not a column of layered nodes", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const figure = await screen.findByRole("figure", { name: /^Model graph · isolated_forest_demo/ });
    expect(within(figure).queryByRole("group", { name: /^Relationship model with/ })).toBeNull();
    const grid = within(figure).getByRole("region", { name: "Isolated tables: 120" });
    expect(grid).toHaveStyle("max-height: 738px"); // 46.125 rem at the 16 px root: 12 rows of 3.5 rem and 11 gaps of 0.375 rem
    expect(grid).toHaveAttribute("tabindex", "0");
    expect(tiles(grid)).toHaveLength(120);
    // The graph takes the full row of the overview beside a forest.
    expect(figure.parentElement!.className).not.toMatch(/xl:grid-cols/);
  });

  it("a tile scopes the run to its table", async () => {
    stubForest();
    const { router } = renderAt(`/evaluation/${ID}`);
    const grid = await screen.findByRole("region", { name: "Isolated tables: 120" });
    await userEvent.click(tile(grid, "T_005"));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ table: "T_005" }));
    expect(tile(grid, "T_005")).toHaveAttribute("aria-pressed", "true");
  });

  it("the Tables in scope frame scrolls inside itself above twelve tables", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const frame = await screen.findByRole("region", { name: "Tables in scope" });
    expect(frame).toHaveClass("max-h-96");
    expect(frame).toHaveAttribute("tabindex", "0");
    // The frame holds every table of the run (sticky header row + 120 rows) and scrolls inside itself.
    expect(within(frame).getByRole("columnheader", { name: "Table" })).toBeInTheDocument();
    expect(within(frame).getAllByRole("row")).toHaveLength(121);
    expect(within(frame).getByRole("cell", { name: "T_120" })).toBeInTheDocument();
  });

  it("an isolated table whose key the launch adjusted keeps its ADR 0038 reading on the tile", async () => {
    const detail: EvaluationDetail = {
      evaluation: evaluation({
        relationship_model: "adjusted_keys_demo",
        model_adjusted: null,
        tables: [tableEntry("E_TABLE", { role: "isolated", run_id: "run-adj-E_TABLE" })],
        run_ids: ["run-adj-E_TABLE"],
      }),
      events: [],
      metrics: [pkRow("E_TABLE"), ...rollups("E_TABLE", "adjusted_keys_demo", 0.8)],
      profiles: [],
      flags: [],
    };
    stubForest(detail, null, [], [adjustmentOf("E_TABLE")]);
    renderAt(`/evaluation/${ID}`);
    const grid = await screen.findByRole("region", { name: "Isolated tables: 1" });
    const only = tile(grid, "E_TABLE");
    expect(only).toHaveAttribute("data-adjustment");
    expect(within(only).getByText(/^PK adj\. /)).toBeInTheDocument();
    expect(only.getAttribute("aria-label")).toMatch(/, primary key: .+ \(ADR 0038\)\./);
  });
});
