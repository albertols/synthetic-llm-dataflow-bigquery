/**
 * An evaluation that can no longer be running reads Stale, never Running
 * (the stale rule, lib/runState.ts): its last registry event is RUNNING and
 * more than 24 hours old. Every view that shows or words the Running state is
 * rendered here on both sides of that boundary, with the clock faked: the
 * list (table and phone cards), the "Latest evaluation" tile, the run view's
 * header, callout and empty state, the reading sentence and the compare view.
 * The registry's own status stays RUNNING wherever the registry is quoted (the
 * status filter still offers it). Stale is an inference from the event's age,
 * so no text states as fact that the evaluation is over.
 */
import type { Facets } from "@contracts/api";
import { configure, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CLOSE_BY_HAND, STALE_TITLE } from "@/lib/runState";

import { headline } from "./lib/interpret";
import { comparison, evaluation, richDetail, T0 } from "./test/fixtures";
import { EMPTY_PAGE, pageOf, renderAt, stubApi } from "./test/harness";

configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

const HOUR = 3_600_000;
const RECORDED_MS = Date.parse(T0);
/** The clock, this many hours after the RUNNING event was recorded. */
const WITHIN = RECORDED_MS + 23 * HOUR;
const PAST = RECORDED_MS + 25 * HOUR;
const RECORDED_TEXT = "2026-09-20 10:00 UTC";

/** A registry row whose last event is RUNNING, recorded at T0. */
function runningRow(id = "eval-t001") {
  return evaluation({
    evaluation_id: id,
    status: "RUNNING",
    event: "RUNNING",
    recorded_at: T0,
    evaluated_at: T0,
    finished_at: null,
    overall_score: null,
    fidelity_score: null,
    privacy_score: null,
    integrity_score: null,
    diversity_score: null,
    metrics_total: null,
  });
}

function facets(latest: Facets["latest"]): Facets {
  return {
    counts: { evaluations: 2, runs: 2, tables: 1, relationship_models: 1, metrics: 0 },
    latest,
    date_range: { min: T0, max: T0 },
    tables: ["users"],
    engines: ["b1_rag"],
    llm_models: [],
    embedders: [],
    retrieval_methods: [],
    seeds: [],
    similarities: [],
    reference_rows_limits: [],
    num_rows: [],
    source_stats_tiers: [],
    profiler_versions: [],
    envs: ["dev"],
    statuses: ["RUNNING", "SUCCEEDED"],
    triggers: [],
    runners: [],
    modes: [],
    evaluator_versions: [],
    catalogue_versions: [],
    generator_versions: [],
    generator_branches: [],
    config_digests: [],
    relationship_models: [],
    metric_ids: [],
    source_tables: [],
    rag: [],
    pools: [],
  };
}

function setClock(now: number) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(now);
}

/** The list lays out a table from 768 px up and cards below; jsdom has no layout, so the test says which. */
function setWide(wide: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string): MediaQueryList => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

function stubList(latest: Facets["latest"] = null) {
  const done = evaluation({ evaluation_id: "eval-t000" });
  return stubApi({
    "/api/facets": facets(latest),
    "/api/evaluations": pageOf(runningRow(), done),
  });
}

function stubRun(metrics: boolean) {
  const detail = richDetail();
  const row = runningRow();
  return stubApi({
    "/api/evaluations/eval-t001": metrics
      ? { ...detail, evaluation: row }
      : { ...detail, evaluation: row, metrics: [], flags: [] },
    "/api/evaluations/eval-t001/profiles": [],
    "/api/evaluations": pageOf(row),
    "/api/relationships": { models: [] },
  });
}

async function listItem(id: string): Promise<HTMLElement> {
  const link = await screen.findByRole("link", { name: id });
  return link.closest("[data-evaluation]")!;
}

beforeEach(() => {
  setWide(true);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("the list", () => {
  it("shows Running for a RUNNING event up to 24 hours old", async () => {
    setClock(WITHIN);
    stubList();
    renderAt("/evaluation");
    const row = await listItem("eval-t001");
    expect(row.tagName).toBe("TR");
    const pill = within(row).getByText("Running").closest("[data-status]")!;
    expect(pill).toHaveAttribute("data-status", "RUNNING");
    expect(pill).not.toHaveAttribute("title");
    expect(within(row).queryByText("Stale")).toBeNull();
  });

  it("shows Stale, not Running, once the RUNNING event is more than 24 hours old", async () => {
    setClock(PAST);
    stubList();
    renderAt("/evaluation");
    const row = await listItem("eval-t001");
    const pill = within(row).getByText("Stale").closest("[data-status]")!;
    expect(pill).toHaveAttribute("data-status", "STALE");
    expect(pill).toHaveAttribute("data-tone", "warn");
    // What the registry itself holds is one hover (or one screen-reader phrase) away.
    expect(pill).toHaveAttribute("title", STALE_TITLE);
    expect(within(row).queryByText("Running")).toBeNull();
    // A finished evaluation is untouched by the clock.
    expect(within(await listItem("eval-t000")).getByText("Succeeded")).toBeInTheDocument();
  });

  it("still offers the registry's RUNNING in the status filter, and no Stale", async () => {
    setClock(PAST);
    stubList();
    renderAt("/evaluation");
    await listItem("eval-t001");
    await userEvent.setup().click(screen.getByRole("combobox", { name: "Status" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["RUNNING", "SUCCEEDED"]);
  });

  it("shows the same pill on the phone cards", async () => {
    setWide(false);
    setClock(PAST);
    stubList();
    renderAt("/evaluation");
    const card = await listItem("eval-t001");
    expect(card.tagName).toBe("LI");
    expect(within(card).getByText("Stale").closest("[data-status]")).toHaveAttribute("title", STALE_TITLE);
    expect(within(card).queryByText("Running")).toBeNull();
  });

  it("keeps the phone cards Running inside 24 hours", async () => {
    setWide(false);
    setClock(WITHIN);
    stubList();
    renderAt("/evaluation");
    const card = await listItem("eval-t001");
    expect(within(card).getByText("Running")).toBeInTheDocument();
    expect(within(card).queryByText("Stale")).toBeNull();
  });

  it("quotes the registry's RUNNING on the latest tile and adds that it is stale", async () => {
    // Only a registry with no FINAL row at all has a RUNNING latest evaluation.
    const latest = { evaluation_id: "eval-t001", evaluated_at: T0, status: "RUNNING" as const, overall_score: null };
    setClock(PAST);
    stubList(latest);
    renderAt("/evaluation");
    const tile = await screen.findByRole("group", { name: "Latest evaluation" });
    expect(await within(tile).findByRole("link")).toHaveTextContent(`eval-t001 · RUNNING (stale) · ${RECORDED_TEXT}`);
  });

  it("leaves the latest tile at RUNNING inside 24 hours", async () => {
    const latest = { evaluation_id: "eval-t001", evaluated_at: T0, status: "RUNNING" as const, overall_score: null };
    setClock(WITHIN);
    stubList(latest);
    renderAt("/evaluation");
    const tile = await screen.findByRole("group", { name: "Latest evaluation" });
    expect(await within(tile).findByRole("link")).toHaveTextContent(`eval-t001 · RUNNING · ${RECORDED_TEXT}`);
  });
});

/** What the stale state must never state as fact: it is inferred from the event's age. */
const CATEGORICAL = /is not running|have expired|has expired|will be written|cannot be running/;

/** True when `a` comes before `b` in the document. */
function precedes(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("the run view", () => {
  it("says running since, still running and metrics still being computed inside 24 hours", async () => {
    setClock(WITHIN);
    stubRun(false);
    renderAt("/evaluation/eval-t001");
    expect(await screen.findByText("Metrics are still being computed")).toBeInTheDocument();
    const header = screen.getByRole("region", { name: "Run status and scope" });
    expect(within(header).getByText("Running")).toBeInTheDocument();
    expect(header).toHaveTextContent(`running since ${RECORDED_TEXT}`);
    const callout = screen.getByText("Still running").closest('[role="note"]')!;
    expect(callout).toHaveAttribute("data-tone", "info");
    // The note stays where it was, below the sections.
    expect(precedes(screen.getByRole("tablist", { name: "Evaluation sections" }), callout)).toBe(true);
    expect(document.body.textContent).not.toMatch(/stale/i);
  });

  it("says stale in the header, the callout and the empty state once the event is more than 24 hours old", async () => {
    setClock(PAST);
    stubRun(false);
    renderAt("/evaluation/eval-t001");
    const title = await screen.findByText("No metrics, and no result recorded for more than 24 hours");
    // The empty state says what the registry holds and what to do next.
    const empty = title.closest('[data-slot="empty-state"]')!;
    expect(empty).toHaveTextContent(`The registry has a RUNNING event from ${RECORDED_TEXT} and no FINAL event.`);
    expect(empty).toHaveTextContent("most likely ended without recording a result, so no metrics are expected");
    expect(empty).toHaveTextContent("evaluate the launch again to get them");
    // The header: the pill, and the registry's last event in place of "running since".
    const header = screen.getByRole("region", { name: "Run status and scope" });
    expect(within(header).getByText("Stale").closest("[data-status]")).toHaveAttribute("title", STALE_TITLE);
    expect(header).toHaveTextContent(`RUNNING event recorded ${RECORDED_TEXT} · no FINAL event since`);
    expect(header).not.toHaveTextContent("running since");
    // The callout: what the registry holds, what that most likely means, and what to check before closing.
    const callout = screen.getByText("Stale: no result recorded for more than 24 hours").closest('[role="note"]')!;
    expect(callout).toHaveAttribute("data-tone", "warn");
    expect(callout).toHaveTextContent(
      `The registry has had a RUNNING event since ${RECORDED_TEXT} and no FINAL event.`,
    );
    expect(callout).toHaveTextContent("The launch or the job most likely ended without recording a result");
    expect(callout).toHaveTextContent("the tables an evaluation prepares expire 24 hours after it creates them");
    expect(callout).toHaveTextContent("The row stays as it is until someone closes it.");
    expect(callout).toHaveTextContent("Before closing it, check that no Dataflow job is still running for it");
    expect(callout).not.toHaveTextContent("may be incomplete");
    expect(within(callout as HTMLElement).getByRole("link", { name: new RegExp(CLOSE_BY_HAND.title) })).toHaveAttribute(
      "href",
      "https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/master/docs/RUN_PLAYBOOK.md#a-running-row-that-never-closes",
    );
    // It is the first thing under the header: above the table scope and the sections.
    expect(precedes(header, callout)).toBe(true);
    expect(precedes(callout, screen.getByRole("group", { name: "Scope" }))).toBe(true);
    expect(precedes(callout, screen.getByRole("tablist", { name: "Evaluation sections" }))).toBe(true);
    // Nothing on the page still claims it is running, and nothing states as fact that it is over.
    expect(screen.queryByText("Still running")).toBeNull();
    expect(screen.queryByText("Metrics are still being computed")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Reload later/);
    expect(document.body.textContent).not.toMatch(CATEGORICAL);
    // The tables it was to evaluate are still drawn.
    expect(screen.getByRole("figure", { name: /Model graph/ })).toBeInTheDocument();
  });

  it("keeps the stale callout on top of every section, a deep link included", async () => {
    setClock(PAST);
    stubRun(true);
    renderAt("/evaluation/eval-t001?tab=privacy");
    const callout = (await screen.findByText("Stale: no result recorded for more than 24 hours")).closest(
      '[role="note"]',
    )!;
    expect(precedes(callout, screen.getByRole("tablist", { name: "Evaluation sections" }))).toBe(true);
    expect(screen.getByRole("tab", { name: "Privacy" })).toHaveAttribute("aria-selected", "true");
  });

  it("warns that the metrics of a stale evaluation may be incomplete", async () => {
    setClock(PAST);
    stubRun(true);
    renderAt("/evaluation/eval-t001");
    const callout = (await screen.findByText("Stale: no result recorded for more than 24 hours")).closest(
      '[role="note"]',
    )!;
    expect(callout).toHaveTextContent("The metrics on this page are what was written so far: they may be incomplete.");
    expect(screen.queryByText("Still running")).toBeNull();
    expect(document.body.textContent).not.toMatch(CATEGORICAL);
  });

  it("keeps the numbers-may-change note for a running evaluation that already has metrics", async () => {
    setClock(WITHIN);
    stubRun(true);
    renderAt("/evaluation/eval-t001");
    const callout = (await screen.findByText("Still running")).closest('[role="note"]')!;
    expect(callout).toHaveTextContent("Numbers may change: the FINAL event has not been recorded yet");
    expect(document.body.textContent).not.toMatch(/stale/i);
  });
});

describe("the reading sentence", () => {
  const detail = { evaluation: runningRow(), metrics: [] };

  it("says still running inside 24 hours and stale after, with the clock injected", () => {
    expect(headline(detail, RECORDED_MS + 24 * HOUR)).toBe(
      "The evaluation is still running: no metrics have been written yet.",
    );
    expect(headline(detail, RECORDED_MS + 24 * HOUR + 1)).toBe(
      "The evaluation is stale: its last registry event is RUNNING, recorded more than 24 hours ago, so it most likely ended without recording a result. No metrics have been written.",
    );
  });

  it("reads the clock when none is given", () => {
    setClock(WITHIN);
    expect(headline(detail)).toMatch(/still running/);
    vi.setSystemTime(PAST);
    expect(headline(detail)).toMatch(/is stale/);
  });

  it("leaves a finished evaluation with no metrics as it was", () => {
    const failed = { evaluation: evaluation({ status: "FAILED", status_reason: "quota" }), metrics: [] };
    expect(headline(failed, PAST)).toBe("No metrics were written: quota");
  });
});

describe("the compare view", () => {
  function stubCompare() {
    const body = comparison({ sameVersions: true });
    const { generation_params: _g, evaluation_params: _e, ...open } = runningRow("eval-b");
    body.evaluations[1] = open;
    stubApi({ "/api/compare": body, "/api/evaluations": EMPTY_PAGE });
  }
  const url = `/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-a", "eval-b"]))}`;

  it("shows a compared evaluation as Stale once its RUNNING event is more than 24 hours old", async () => {
    setClock(PAST);
    stubCompare();
    renderAt(url);
    const table = await screen.findByRole("region", { name: "Compared evaluations" });
    const row = within(table).getByRole("link", { name: "eval-b" }).closest("tr")!;
    expect(within(row).getByText("Stale").closest("[data-status]")).toHaveAttribute("title", STALE_TITLE);
    expect(within(row).queryByText("Running")).toBeNull();
  });

  it("shows it as Running inside 24 hours", async () => {
    setClock(WITHIN);
    stubCompare();
    renderAt(url);
    const table = await screen.findByRole("region", { name: "Compared evaluations" });
    const row = within(table).getByRole("link", { name: "eval-b" }).closest("tr")!;
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).queryByText("Stale")).toBeNull();
  });
});
