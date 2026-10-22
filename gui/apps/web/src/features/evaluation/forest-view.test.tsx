/**
 * The run view of a whole-model launch of 120 unrelated tables (ADR 0036, ADR 0042) in jsdom: the
 * real routes, a stubbed BFF. Tasks 3–8 each add their part of the views here.
 */
import type { EvaluationDetail } from "@contracts/api";
import type { GenerationRecord } from "@contracts/record";
import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
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

  it("every tile says whether its table declares a key, reads another source, and which batch job built it", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const grid = await screen.findByRole("region", { name: "Isolated tables: 120" });
    await waitFor(() => expect(tile(grid, "T_003")).toHaveAttribute("data-pk", "none"));
    expect(tile(grid, "T_003")).toHaveTextContent("no PK");
    expect(tile(grid, "T_003")).toHaveAccessibleName(/no primary key declared/);
    expect(tile(grid, "T_001")).toHaveTextContent("PK");
    expect(tile(grid, "T_007")).toHaveTextContent("⇄");
    expect(tile(grid, "T_007")).toHaveAccessibleName(/source override example_archive\.T_007_V2/);
    expect(tile(grid, "T_033")).toHaveTextContent("b03");
    expect(tile(grid, "T_004")).toHaveAttribute("data-status", "fail");
  });

  it("without a record the tiles still read the key and the batch from the evaluator's rows and the run ids", async () => {
    stubForest(forestDetail(), null);
    renderAt(`/evaluation/${ID}`);
    const grid = await screen.findByRole("region", { name: "Isolated tables: 120" });
    await waitFor(() => expect(tile(grid, "T_003")).toHaveAttribute("data-pk", "none"));
    expect(tile(grid, "T_033")).toHaveTextContent("b03");
    expect(tile(grid, "T_007")).not.toHaveTextContent("⇄");
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

  it("a PK-less table's key checks read Not applicable, never empty or FAIL", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const integrity = await screen.findByRole("region", { name: /^Integrity/ });
    const row = integrity.querySelector<HTMLElement>('li[data-metric="table.identity_duplicate_rate"]')!;
    expect(within(row).getByText("Not applicable")).toHaveAttribute("data-tone", "neutral");
    expect(row).toHaveTextContent("Not applicable — no identity columns declared");
    expect(within(row).queryByText("Not evaluated")).toBeNull();
    const findingsList = screen.getByRole("list", { name: "Findings, most severe first" });
    const finding = within(findingsList)
      .getByText(/^40 tables declare no primary key/)
      .closest("li")!;
    expect(within(finding).getByText("Not applicable")).toBeInTheDocument();
    expect(screen.getByText(/160 not applicable \(no key declared\)/)).toBeInTheDocument();
  });
});

const stat = (card: HTMLElement, label: string) => card.querySelector<HTMLElement>(`[data-stat="${label}"]`)!;
/** A numeric stat is asserted on its own `dd`, exactly: "0" would also match "10" by substring. */
const statValue = (card: HTMLElement, label: string) => stat(card, label).querySelector("dd")!;

function servedForest(enabledOf = 120) {
  return {
    model: "isolated_forest_demo",
    description: "",
    source: "",
    sha12: "f0f0f0f0f0f0",
    generation_order: [],
    origin: "mock",
    tables: Array.from({ length: 125 }, (_, k) => ({
      name: `T_${String(k + 1).padStart(3, "0")}`,
      pk: [],
      identity: [],
      enabled: k < enabledOf,
      note: "",
      fk: [],
    })),
  };
}

describe("the model summary card (ADR 0044, ADR 0045)", () => {
  it("counts what the launch declared, links each batch job, and says where the counts come from", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const card = await screen.findByRole("region", { name: "Model summary" });
    expect(card).toHaveTextContent("isolated_forest_demo");
    expect(card).toHaveTextContent("sha12 f0f0f0f0f0f0");
    expect(card).toHaveTextContent("from the generation record (ADR 0044)");
    expect(statValue(card, "Tables")).toHaveTextContent(/^120$/);
    expect(stat(card, "Tables")).toHaveTextContent("disabled: not recorded");
    expect(statValue(card, "Primary key")).toHaveTextContent(/^80 declared$/);
    expect(stat(card, "Primary key")).toHaveTextContent("40 none");
    expect(statValue(card, "Edges")).toHaveTextContent(/^0$/);
    expect(statValue(card, "Isolated")).toHaveTextContent(/^120$/);
    expect(statValue(card, "Source overrides")).toHaveTextContent(/^6$/);
    expect(statValue(card, "Batch jobs")).toHaveTextContent(/^12$/);
    const links = within(within(card).getByRole("list", { name: "Batch jobs" })).getAllByRole("link");
    expect(links).toHaveLength(12);
    expect(links[3]).toHaveAccessibleName(/^b03/);
    expect(links[3]).toHaveAttribute("href", expect.stringContaining("/dataflow/jobs/europe-west3/"));
    expect(links[3]).toHaveAttribute("href", expect.stringContaining("project=demo-project"));
  });

  it("counts disabled tables when the whole model is served", async () => {
    stubForest(forestDetail(), forestRecord(), [servedForest()]);
    renderAt(`/evaluation/${ID}`);
    const card = await screen.findByRole("region", { name: "Model summary" });
    await waitFor(() => expect(stat(card, "Tables")).toHaveTextContent("of 125 declared · 5 disabled"));
  });

  it("without a record it says it reads the registry, and shows no source overrides", async () => {
    stubForest(forestDetail(), null);
    renderAt(`/evaluation/${ID}`);
    const card = await screen.findByRole("region", { name: "Model summary" });
    expect(card).toHaveTextContent("from the registry and the metric rows");
    expect(statValue(card, "Source overrides")).toHaveTextContent(/^—$/);
  });

  it("the Relational tab says why it has nothing to draw", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tab=relational`);
    expect(
      await screen.findByRole("heading", { name: "Isolated tables — no relationships declared" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Integrity is scored per table: columns, row count, keys where declared."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No relationship metrics")).toBeNull();
  });
});

describe("the Generator panel's relationships card (ADR 0044)", () => {
  it("lists every table the launch declared, not only batch 0's snapshot", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tab=params`);
    const card = (await screen.findByText("120 tables · 12 batch jobs")).closest('[data-slot="card"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(within(card).getAllByRole("listitem")).toHaveLength(120);
    expect(within(card).getByText("T_120")).toBeInTheDocument();
  });
});

describe("per-table scorecards at scale (ADR 0036)", () => {
  it("render a window of rows inside a focusable frame, every row reachable by scrolling", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const frame = await screen.findByRole("region", { name: "Per-table scores, Integrity: 120 tables" });
    expect(frame).toHaveAttribute("tabindex", "0");
    const list = within(frame).getByRole("list", { name: "Integrity score per table" });
    expect(list.querySelectorAll("li").length).toBeLessThan(30);
    fireEvent.scroll(frame, { target: { scrollTop: 24 * 110 } });
    expect(list.querySelector('li[aria-posinset="120"]')).not.toBeNull();
  });

  it("a table's integrity score lists the units that counted and the ones that were not applicable", async () => {
    stubForest();
    // Selected, so its row is scrolled into the window whatever the list's order (Task 8 sorts it).
    renderAt(`/evaluation/${ID}?table=T_003`);
    await userEvent.click(await screen.findByRole("button", { name: /^Integrity units of T_003/ }));
    const units = await screen.findByRole("dialog", { name: "Integrity units of T_003" });
    expect(units).toHaveTextContent("Counted (1)");
    expect(units).toHaveTextContent("Row count");
    expect(units).toHaveTextContent("Not applicable (2)");
    expect(units).toHaveTextContent("Not applicable — no primary key declared");
    expect(units).toHaveTextContent("Not applicable — no identity columns declared");
    expect(units).not.toHaveTextContent("Fail");
  });

  it("scrolls the selected table's row into view", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?table=T_100`);
    const frame = await screen.findByRole("region", { name: "Per-table scores, Integrity: 120 tables" });
    await waitFor(() => expect(within(frame).getByText("T_100")).toBeInTheDocument());
  });
});
