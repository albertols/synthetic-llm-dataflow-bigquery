/** The evaluation list shows the generation record and sends its filters (ADR 0044). */
import type { Facets } from "@contracts/api";
import { configure, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { evaluation, T0 } from "./test/fixtures";
import { pageOf, renderAt, stubApi } from "./test/harness";

configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

const RECORDED = evaluation({
  evaluation_id: "eval-rec",
  generator_version: "0.6.1-master-edcfdcd",
  generator_config_digest: "0123456789ab",
  generation_job_id: "2026-10-21_01_02_03-123",
  generation_region: "europe-west3",
});

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
    statuses: ["SUCCEEDED"],
    triggers: [],
    runners: [],
    modes: [],
    evaluator_versions: [],
    catalogue_versions: [],
    generator_versions: ["0.6.1-master-edcfdcd"],
    generator_branches: ["master"],
    config_digests: ["0123456789ab"],
    relationship_models: [],
    metric_ids: [],
    source_tables: [],
    rag: [],
    pools: [],
  };
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

beforeEach(() => setWide(true));
afterEach(() => vi.restoreAllMocks());

async function listItem(id: string): Promise<HTMLElement> {
  const link = await screen.findByRole("link", { name: id });
  return link.closest("[data-evaluation]")!;
}

describe("the list shows the generation record (ADR 0044)", () => {
  it("shows the generator version, the job link and the config digest", async () => {
    stubApi({ "/api/facets": facets(null), "/api/evaluations": pageOf(RECORDED) });
    renderAt("/evaluation");
    const row = await listItem("eval-rec");
    expect(within(row).getByText("0.6.1 · edcfdcd")).toBeInTheDocument();
    expect(within(row).getByText("0123456789ab")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "2026-10-21_01_02_03-123" })).toHaveAttribute(
      "href",
      expect.stringContaining("/dataflow/jobs/europe-west3/2026-10-21_01_02_03-123"),
    );
  });

  it("shows the job id as text when the registry records no region (a --run_id evaluation)", async () => {
    stubApi({
      "/api/facets": facets(null),
      "/api/evaluations": pageOf({ ...RECORDED, evaluation_id: "eval-run-id", generation_region: null }),
    });
    renderAt("/evaluation");
    const row = await listItem("eval-run-id");
    const job = within(row).getByText("2026-10-21_01_02_03-123");
    expect(job.closest("a")).toBeNull();
    expect(job).toHaveClass("font-mono");
  });

  it("shows — for a run without a record", async () => {
    stubApi({
      "/api/facets": facets(null),
      "/api/evaluations": pageOf(evaluation({ evaluation_id: "eval-old", generation_job_id: null })),
    });
    renderAt("/evaluation");
    const row = await listItem("eval-old");
    const generatorCell = [...row.querySelectorAll("td")].find((c) => c.textContent === "— · —")!;
    expect(generatorCell).toBeDefined();
    expect(generatorCell.querySelector("[data-slot=badge]")).toBeNull();
    expect(within(generatorCell).queryByRole("link")).toBeNull();
  });

  it("sends the branch, version and digest filters from the URL", async () => {
    const { calls } = stubApi({ "/api/facets": facets(null), "/api/evaluations": pageOf(RECORDED) });
    const list = (value: string) => encodeURIComponent(JSON.stringify([value]));
    renderAt(
      `/evaluation?generator_branch=${list("master")}&generator_version=${list("0.6.1-master-edcfdcd")}&config_digest=${list("0123456789ab")}`,
    );
    await listItem("eval-rec");
    const sent = calls.find((u) => u.pathname === "/api/evaluations")!;
    expect(sent.searchParams.get("generator_branch")).toBe("master");
    expect(sent.searchParams.get("generator_version")).toBe("0.6.1-master-edcfdcd");
    expect(sent.searchParams.get("config_digest")).toBe("0123456789ab");
  });
});
