/**
 * The run view of a whole-model launch of 120 unrelated tables (ADR 0036, ADR 0042) in jsdom: the
 * real routes, a stubbed BFF. Tasks 3–8 each add their part of the views here.
 */
import type { EvaluationDetail } from "@contracts/api";
import type { GenerationRecord, GeneratorSnapshot } from "@contracts/record";
import { configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  adjustmentOf,
  evaluation,
  forestDetail,
  forestJobId,
  forestRecord,
  metric,
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

  it("the headline and the Not evaluated footnote tell not applicable from not evaluated", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    // Every not-evaluated row of the forest is a key check with no key: nothing else is left to call "not a pass".
    expect(await screen.findByTestId("run-headline")).toHaveTextContent(
      "160 not evaluated (160 not applicable: no key declared).",
    );
    expect(screen.getByText("160 not applicable (no key declared): nothing to check.")).toBeInTheDocument();
    expect(screen.queryByText(/The rest is not a pass/)).toBeNull();
  });

  it("keeps 'The rest is not a pass.' while other rows were not evaluated", async () => {
    const detail = forestDetail();
    detail.metrics = [
      ...detail.metrics,
      metric("column.ks", {
        table_name: "T_001",
        column_name: "COL_002",
        value: null,
        score: null,
        status: "not_evaluated",
        detail: { reason: "no rows" },
      }),
    ];
    stubForest(detail);
    renderAt(`/evaluation/${ID}`);
    expect(await screen.findByTestId("run-headline")).toHaveTextContent(
      "161 not evaluated (160 not applicable: no key declared).",
    );
    expect(
      screen.getByText("160 not applicable (no key declared): nothing to check. The rest is not a pass."),
    ).toBeInTheDocument();
  });
});

/**
 * The forest with b05 FAILED after writing its rows: the evaluation does not list b05's job, so the record
 * narrows its rows out (buildGenerationRecord) and the evaluator never read its tables — 110 tables and 11 of
 * the 12 planned batch jobs.
 */
function withoutB05(): [EvaluationDetail, GenerationRecord] {
  const record = forestRecord();
  const dropped = new Set(record.batches[5]!.tables);
  record.batches = record.batches.filter((b) => b.index !== 5);
  record.declared_tables = record.declared_tables.filter((t) => !dropped.has(t.name));
  const detail = forestDetail();
  const tables = detail.evaluation.tables.filter((t) => !dropped.has(t.name!));
  detail.evaluation = {
    ...detail.evaluation,
    tables,
    run_ids: tables.map((t) => t.run_id!),
    generation_job_ids: detail.evaluation.generation_job_ids.filter((j) => j !== forestJobId(5)),
  };
  detail.metrics = detail.metrics.filter((m) => !dropped.has(m.table_name));
  return [detail, record];
}

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

  it("a one-batch launch reads b00 under Batch jobs, not a range", async () => {
    stubForest(forestDetail(9), forestRecord(9));
    renderAt(`/evaluation/${ID}`);
    const card = await screen.findByRole("region", { name: "Model summary" });
    expect(statValue(card, "Batch jobs")).toHaveTextContent(/^1$/);
    expect(stat(card, "Batch jobs").querySelectorAll("dd")[1]).toHaveTextContent(/^b00$/);
  });

  it("says how many batch jobs were recorded when one wrote no row: 12 · 11 recorded", async () => {
    stubForest(...withoutB05());
    renderAt(`/evaluation/${ID}`);
    const card = await screen.findByRole("region", { name: "Model summary" });
    expect(statValue(card, "Tables")).toHaveTextContent(/^110$/);
    expect(statValue(card, "Batch jobs")).toHaveTextContent(/^12$/);
    expect(stat(card, "Batch jobs")).toHaveTextContent("11 recorded");
    expect(within(within(card).getByRole("list", { name: "Batch jobs" })).getAllByRole("link")).toHaveLength(11);
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

  it("bounds the declared tables in a focusable, labelled frame above twelve, as Tables in scope", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tab=params`);
    const frame = await screen.findByRole("region", { name: "Declared tables: 120" });
    expect(frame).toHaveClass("max-h-96");
    expect(frame).toHaveClass("overflow-y-auto");
    expect(frame).toHaveAttribute("tabindex", "0");
    expect(within(frame).getAllByRole("listitem")).toHaveLength(120);
  });

  it("counts batch jobs as the model card does: planned, then how many were recorded", async () => {
    stubForest(...withoutB05());
    renderAt(`/evaluation/${ID}?tab=params`);
    expect(await screen.findByText("110 tables · 12 batch jobs · 11 recorded")).toBeInTheDocument();
  });
});

describe("the batch-job links (a --run_id evaluation records no generation_region)", () => {
  const URL_OF = (region: string, b: number) =>
    `https://console.cloud.google.com/dataflow/jobs/${region}/${forestJobId(b)}?project=demo-project`;
  const batchList = async () =>
    within(await screen.findByRole("region", { name: "Model summary" })).getByRole("list", { name: "Batch jobs" });
  const generatorJobs = async () => within(await screen.findByRole("region", { name: "Generator" }));
  /** The forest with no registry region, its snapshot's `beam` block as given (a record's runs carry job ids). */
  function noRegistryRegion(beam: unknown): [EvaluationDetail, GenerationRecord] {
    const detail = forestDetail();
    detail.evaluation = { ...detail.evaluation, generation_region: null };
    const record = forestRecord();
    record.snapshot = { ...record.snapshot!, beam } as unknown as GeneratorSnapshot;
    record.runs = [0, 3].map((b) => ({
      run_id: `F-b${String(b).padStart(2, "0")}-00-T_${String(b * 10 + 1).padStart(3, "0")}`,
      landing_table: "demo-project.synthetic_data.T_001",
      job_id: forestJobId(b),
      job_name: "synthetic-sdfb-model",
      launch_run_id: "F",
      generator_version: "0.6.1-master-edcfdcd",
      launched_at: "2026-10-21T10:00:00.000000Z",
      config_digest: "0123456789ab",
    }));
    return [detail, record];
  }

  it("link each job in the registry's region", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    expect(within(await batchList()).getAllByRole("link")[3]).toHaveAttribute("href", URL_OF("europe-west3", 3));
  });

  it("fall back to the record snapshot's beam.region when the registry has none", async () => {
    stubForest(...noRegistryRegion({ runner: "DataflowRunner", region: "us-central1" }));
    const { router } = renderAt(`/evaluation/${ID}`);
    const links = within(await batchList()).getAllByRole("link");
    expect(links).toHaveLength(12);
    expect(links[3]).toHaveAttribute("href", URL_OF("us-central1", 3));
    await router.navigate({ to: "/evaluation/$evaluationId", params: { evaluationId: ID }, search: { tab: "params" } });
    expect((await generatorJobs()).getByRole("link", { name: forestJobId(3) })).toHaveAttribute(
      "href",
      URL_OF("us-central1", 3),
    );
  });

  it("show each job id as text, never a link without its region, when neither knows it (an A2-era beam block)", async () => {
    stubForest(...noRegistryRegion({ runner: "DataflowRunner", job_name: "synthetic-sdfb-model" }));
    const { router } = renderAt(`/evaluation/${ID}`);
    const list = await batchList();
    expect(within(list).queryAllByRole("link")).toHaveLength(0);
    const entries = within(list).getAllByRole("listitem");
    expect(entries).toHaveLength(12);
    expect(entries[3]).toHaveTextContent(`b03 ${forestJobId(3)}`);
    expect(within(entries[3]!).getByText(forestJobId(3))).toHaveClass("font-mono");
    await router.navigate({ to: "/evaluation/$evaluationId", params: { evaluationId: ID }, search: { tab: "params" } });
    const panel = await generatorJobs();
    expect(panel.queryByRole("link", { name: forestJobId(3) })).toBeNull();
    expect(panel.getByText(forestJobId(3))).toHaveClass("font-mono");
  });

  it.each([
    ["a string", "europe-west3"],
    ["null", null],
    ["a list", ["europe-west3"]],
    ["a number region", { region: 5 }],
  ])("read a malformed beam block (%s) as no region", async (_, beam) => {
    stubForest(...noRegistryRegion(beam));
    renderAt(`/evaluation/${ID}`);
    const list = await batchList();
    expect(within(list).queryAllByRole("link")).toHaveLength(0);
    expect(list).toHaveTextContent(forestJobId(3));
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
    const trigger = await screen.findByRole("button", { name: /integrity units of T_003:/ });
    // WCAG 2.5.3 label in name: the accessible name starts with the score the button shows.
    expect(trigger).toHaveTextContent(/^\d\.\d\d$/);
    expect(trigger).toHaveAccessibleName(
      `${trigger.textContent} — integrity units of T_003: 1 counted, 2 not applicable`,
    );
    await userEvent.click(trigger);
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

describe("search and filters (ADR 0042)", () => {
  const grid = () => screen.findByRole("region", { name: "Isolated tables: 120" });
  const listOf = (family: string) => screen.getByRole("list", { name: `${family} score per table` });

  it("narrow the tiles and the per-table scorecards together, and live in the URL", async () => {
    stubForest();
    const { router } = renderAt(`/evaluation/${ID}`);
    const search = await screen.findByRole("searchbox", { name: "Find a table" });
    // One change event: the input is controlled by the URL, as the heatmap's search is.
    fireEvent.change(search, { target: { value: "T_11" } });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tq: "T_11" }));
    expect(tiles(await grid())).toHaveLength(10);
    expect(listOf("Integrity").querySelectorAll("li")).toHaveLength(10);
    expect(screen.getByRole("group", { name: "Table filters" })).toHaveTextContent("10 of 120 tables");
  });

  it("filter by key from a shared link", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?pk=no`);
    expect(tiles(await grid()).every((t) => t.dataset.pk === "none")).toBe(true);
    expect(tiles(await grid())).toHaveLength(40);
  });

  it("status and batch narrow as the URL says", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?ts=fail&batch=0`);
    const shown = tiles(await grid()).map((t) => t.dataset.table);
    // Batch b00 is T_001 … T_010; of those, T_004 (key failure) and T_010 (score 0.70) fail.
    expect(shown.sort()).toEqual(["T_004", "T_010"]);
  });

  it("sort worst first by default, by name on request", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tsort=name`);
    expect(tiles(await grid())[0]).toHaveAttribute("data-table", "T_001");
  });

  it("a filter that matches nothing says so, and Clear filters brings every table back", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tq=nothing`);
    const region = await grid();
    expect(region).toHaveTextContent("No table matches the filters.");
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(tiles(region)).toHaveLength(120));
  });

  it("stale values in a shared link are ignored", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?ts=bogus&batch=-1&pk=maybe`);
    expect(tiles(await grid())).toHaveLength(120);
    expect(screen.getByRole("group", { name: "Table filters" })).toHaveTextContent("120 of 120 tables");
  });

  it("search brings any table into a plain list", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tq=T_100`);
    await grid();
    const list = listOf("Integrity");
    expect(list.closest('[role="region"]')).toBeNull();
    expect(within(list).getByRole("button", { name: /integrity units of T_100:/ })).toBeInTheDocument();
  });

  it("below ten tables there is no filter bar", async () => {
    stubForest(forestDetail(9), forestRecord(9));
    renderAt(`/evaluation/${ID}`);
    await screen.findByRole("region", { name: "Model summary" });
    expect(screen.queryByRole("group", { name: "Table filters" })).toBeNull();
  });

  it("a selected table a filter would hide stays shown, and the bar says so", async () => {
    stubForest();
    // T_001 declares a key: No PK would hide it, but it is the table the run view is scoped to.
    renderAt(`/evaluation/${ID}?table=T_001&pk=no`);
    const region = await grid();
    expect(tile(region, "T_001")).toHaveAttribute("aria-pressed", "true");
    expect(tiles(region)).toHaveLength(41);
    expect(tiles(region)[0]).toHaveAttribute("data-table", "T_001");
    expect(screen.getByRole("group", { name: "Table filters" })).toHaveTextContent(
      "40 of 120 tables · plus T_001, selected",
    );
    expect(within(listOf("Integrity")).getByText("T_001")).toBeInTheDocument();
  });

  it("on a forest the bar's count is every table it narrows: no word about the layered graph", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}?tq=T_11`);
    await grid();
    const bar = screen.getByRole("group", { name: "Table filters" });
    expect(bar).toHaveTextContent("10 of 120 tables");
    expect(bar).not.toHaveTextContent("layered graph");
  });

  it("a served model's table the evaluation does not cover is never a tile and never counted as shown", async () => {
    stubForest(forestDetail(), forestRecord(), [servedForest()]);
    renderAt(`/evaluation/${ID}`);
    expect(tiles(await grid())).toHaveLength(120);
    expect(screen.getByRole("group", { name: "Table filters" })).toHaveTextContent("120 of 120 tables");
    expect(screen.queryByText("No table matches the filters.")).toBeNull();
  });
});

/**
 * A launch with a component AND isolated tables: users ──< orders ──< order_items >── products (external),
 * plus twelve forest tables with no foreign key (ADR 0036). No served model, no record: the graph is
 * rebuilt from the metric rows' edge labels.
 */
function mixedDetail(): EvaluationDetail {
  const forest = forestDetail(12);
  const related = ["users", "orders", "order_items"];
  const edge = (table: string, label: string, role: string) =>
    metric("relationship.orphan_rate", {
      table_name: table,
      edge: label,
      value: 0,
      score: 1,
      detail: { orphans: 0, enforced: true, role },
    });
  return {
    ...forest,
    evaluation: {
      ...forest.evaluation,
      relationship_model: "mixed_demo",
      tables: [
        tableEntry("users", { run_id: "F-b00-10-users" }),
        tableEntry("orders", { role: "driven", run_id: "F-b00-11-orders" }),
        tableEntry("order_items", { role: "driven", run_id: "F-b00-12-order_items" }),
        tableEntry("products", { role: "external", run_id: null }),
        ...forest.evaluation.tables,
      ],
    },
    metrics: [
      ...related.flatMap((t) => [
        metric("table.overall_score", { table_name: t, value: 0.9, score: 0.9, status: "pass" }),
        metric("table.pk_duplicate_rate", { table_name: t, value: 0, score: 1, status: "pass" }),
      ]),
      edge("orders", "orders(user_id) -> users(id)", "driving"),
      edge("order_items", "order_items(order_id) -> orders(id)", "driving"),
      edge("order_items", "order_items(product_id) -> synthetic_data.products(id)", "external"),
      ...forest.metrics,
    ],
  };
}

describe("a launch with a component and isolated tables (ADR 0036)", () => {
  it("draws the component as layers and the rest as tiles, side by side in one full-width frame", async () => {
    stubForest(mixedDetail(), null);
    renderAt(`/evaluation/${ID}`);
    const figure = await screen.findByRole("figure", { name: /^Model graph · mixed_demo/ });
    expect(
      within(figure).getByRole("group", { name: /^Relationship model with 4 tables and 3 foreign keys/ }),
    ).toBeInTheDocument();
    expect(tiles(within(figure).getByRole("region", { name: "Isolated tables: 12" }))).toHaveLength(12);
    expect(figure.parentElement!.className).not.toMatch(/xl:grid-cols/);
    // The legends of what is layered show, and the tile's score colour is its left border.
    expect(figure).toHaveTextContent("dashed arrow: documented edge");
    expect(figure).toHaveTextContent("dashed box: external parent");
    expect(figure).toHaveTextContent("left border colour = overall score");
    // The card counts both: three tables in the component (the external parent is not the launch's), twelve isolated.
    const card = screen.getByRole("region", { name: "Model summary" });
    expect(statValue(card, "Tables")).toHaveTextContent(/^15$/);
    expect(statValue(card, "Edges")).toHaveTextContent(/^3$/);
    expect(statValue(card, "Isolated")).toHaveTextContent(/^12$/);
  });

  it("the bar says the layered graph is not filtered: only the tiles and the scorecards narrow", async () => {
    stubForest(mixedDetail(), null);
    renderAt(`/evaluation/${ID}?pk=yes`);
    const figure = await screen.findByRole("figure", { name: /^Model graph · mixed_demo/ });
    const bar = screen.getByRole("group", { name: "Table filters" });
    // Forest tables T_003, T_006, T_009 and T_012 declare no key: 3 related + 8 isolated tables have one.
    expect(bar).toHaveTextContent("11 of 15 tables · the 3 in the layered graph are not filtered");
    expect(tiles(within(figure).getByRole("region", { name: "Isolated tables: 12" }))).toHaveLength(8);
    expect(figure.querySelectorAll('g[role="button"][data-table]')).toHaveLength(4);
  });

  it("the Relational tab shows the component's orphan rates, without the zero-edge note", async () => {
    stubForest(mixedDetail(), null);
    renderAt(`/evaluation/${ID}?tab=relational`);
    expect(await screen.findByRole("region", { name: "Orphan rate per foreign key" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Isolated tables — no relationships declared" })).toBeNull();
  });
});

describe("the model graph's copy on a forest (nothing layered)", () => {
  it("names the tile's left border, and drops the legends of arrows and external parents", async () => {
    stubForest();
    renderAt(`/evaluation/${ID}`);
    const figure = await screen.findByRole("figure", { name: /^Model graph · isolated_forest_demo/ });
    expect(figure).toHaveTextContent("left border colour = overall score");
    expect(figure).not.toHaveTextContent("edge colour");
    expect(figure).not.toHaveTextContent("dashed arrow");
    expect(figure).not.toHaveTextContent("dashed box");
  });
});
