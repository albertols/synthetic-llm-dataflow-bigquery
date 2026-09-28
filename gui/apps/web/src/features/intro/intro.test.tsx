/**
 * INTRO in the browser (jsdom): a stage click deep-links to its tab with that
 * tab's params; the counters render what the BFF answers in mock mode; the
 * hero rests fully lit, with no autoplay, under reduced motion; the glossary
 * searches the registry and mirrors the query in the URL.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LazyMotion, domAnimation } from "motion/react";
import type { ReactNode } from "react";

import type { EvaluationSummary, Facets, Health } from "@contracts/api";

import { useFacets } from "@/lib/api";

import { Glossary } from "./components/Glossary";
import { LiveCounters } from "./components/LiveCounters";
import { PipelineHero } from "./components/PipelineHero";
import { IntroPage } from "./IntroPage";
import { searchSchema } from "./route";

/** The hero alone, fed by the facets as the page feeds it (sections render alone to keep jsdom fast). */
function HeroOnly() {
  const facets = useFacets();
  return (
    <LazyMotion features={domAnimation}>
      <PipelineHero facets={facets.data?.data} />
    </LazyMotion>
  );
}

const WAIT = { timeout: 20_000 };

vi.mock("@/components/Mermaid", () => ({
  Mermaid: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />,
}));

const LATEST = "eval-0039";

const FACETS: Facets = {
  counts: { evaluations: 40, runs: 133, tables: 5, relationship_models: 1, metrics: 17_907 },
  latest: {
    evaluation_id: LATEST,
    evaluated_at: "2026-09-24T09:25:00.084431Z",
    status: "SUCCEEDED",
    overall_score: 0.961,
  },
  date_range: { min: "2026-08-03T07:12:00.104729Z", max: "2026-09-27T18:45:00.189160Z" },
  tables: ["order_items", "orders", "products", "user_features", "users"],
  engines: ["b1_rag", "b2_library"],
  llm_models: [],
  embedders: ["hashing-384"],
  retrieval_methods: ["centroid"],
  seeds: [],
  similarities: [],
  reference_rows_limits: [10_000],
  num_rows: [],
  source_stats_tiers: ["sample", "exact"],
  profiler_versions: ["2"],
  envs: ["dev"],
  statuses: ["SUCCEEDED"],
  triggers: [],
  runners: [],
  modes: [],
  evaluator_versions: [],
  catalogue_versions: [],
  relationship_models: ["thelook_demo"],
  metric_ids: [],
  source_tables: ["users", "orders", "order_items", "products", "user_features"].map((t) => ({
    table_fqn: `demo-project.synthetic_source.${t}`,
    tiers: ["sample"],
    latest_computed_at: "2026-09-27T18:45:00.189160Z",
    columns: 8,
  })),
  rag: [],
  pools: [],
};

/** Registry rows for the trend: only the fields the counters read matter. */
function summary(id: string, day: number, score: number | null, event: "FINAL" | "RUNNING"): EvaluationSummary {
  return {
    evaluation_id: id,
    evaluated_at: `2026-09-${String(day).padStart(2, "0")}T09:00:00.000000Z`,
    event,
    overall_score: score,
  } as EvaluationSummary;
}

const EVALUATIONS = [
  summary("eval-0040", 27, null, "RUNNING"),
  summary(LATEST, 24, 0.961, "FINAL"),
  summary("eval-0038", 21, 0.927, "FINAL"),
  summary("eval-0037", 18, 0.95, "FINAL"),
];

const HEALTH = { status: "ok", mode: "mock", project: null } as unknown as Health;

function respond(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json", "x-data-source": "mock" },
  });
}

function stubBff() {
  const fetch = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url.startsWith("/api/health")) return Promise.resolve(respond(HEALTH));
    if (url.startsWith("/api/facets")) return Promise.resolve(respond(FACETS));
    if (url.startsWith("/api/evaluations"))
      return Promise.resolve(respond({ items: EVALUATIONS, total: 40, offset: 0, limit: 24 }));
    if (url.startsWith("/api/relationships")) return Promise.resolve(respond({ models: [] }));
    if (url.startsWith("/assets/provenance.json")) return Promise.resolve(respond({ note: "", assets: [] }));
    return Promise.resolve(new Response("not found", { status: 404 }));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

/** The INTRO page under a router whose other tabs are stubs that print their path. */
function renderIntro(initial = "/", Page: () => ReactNode = IntroPage) {
  const root = createRootRoute({ component: () => <Outlet /> });
  const stub = (label: string) => () => <p>{label}</p>;
  const anySearch = (search: Record<string, unknown>) => search;
  const routeTree = root.addChildren([
    createRoute({ getParentRoute: () => root, path: "/", validateSearch: searchSchema, component: Page }),
    createRoute({ getParentRoute: () => root, path: "rag", validateSearch: anySearch, component: stub("RAG tab") }),
    createRoute({
      getParentRoute: () => root,
      path: "config",
      validateSearch: anySearch,
      component: stub("Config tab"),
    }),
    createRoute({
      getParentRoute: () => root,
      path: "evaluation",
      validateSearch: anySearch,
      component: stub("Evaluation tab"),
    }),
    createRoute({
      getParentRoute: () => root,
      path: "evaluation/$evaluationId",
      validateSearch: anySearch,
      component: stub("Evaluation run"),
    }),
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [initial] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function setReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string): MediaQueryList =>
      ({
        matches: reduce && query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

beforeEach(() => {
  stubBff();
  setReducedMotion(false);
});
afterEach(() => vi.unstubAllGlobals());

describe("INTRO pipeline hero", { timeout: 90_000 }, () => {
  it("renders the whole page, then deep-links a stage to its tab", async () => {
    const user = userEvent.setup();
    const router = renderIntro();
    expect(await screen.findByRole("heading", { level: 1 }, WAIT)).toHaveTextContent("Synthetic BigQuery data");
    for (const section of ["How it works", "Packages", "Relationship shapes", "Glossary"]) {
      expect(screen.getByRole("navigation", { name: "On this page" })).toHaveTextContent(section);
    }
    const stages = await screen.findByRole("list", { name: "Pipeline stages" }, WAIT);
    const titles = within(stages)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(titles).toEqual([
      "Source table",
      "Reference sample",
      "source_table_stats",
      "RAG retrieval",
      "Generation on L4",
      "Mode A guardrails",
      "WriteLanding",
      "Evaluation",
      "Registry",
    ]);

    await user.click(within(stages).getByRole("link", { name: "Reference sample" }));
    await screen.findByText("Config tab", {}, WAIT);
    expect(router.state.location.pathname).toBe("/config");
    expect(router.state.location.search).toEqual({ knob: "reference_rows_limit" });

    await act(() => router.navigate({ to: "/" }));
    await user.click(
      within(await screen.findByRole("list", { name: "Pipeline stages" }, WAIT)).getByRole("link", {
        name: "RAG retrieval",
      }),
    );
    await screen.findByText("RAG tab", {}, WAIT);
    expect(router.state.location.pathname).toBe("/rag");
  });

  it("sends the registry stage to the latest evaluation once the facets arrive", async () => {
    const user = userEvent.setup();
    const router = renderIntro("/", HeroOnly);
    await screen.findByText("40 evaluations", {}, WAIT);
    const stages = screen.getByRole("list", { name: "Pipeline stages" });
    await user.click(within(stages).getByRole("link", { name: "Registry" }));
    await screen.findByText("Evaluation run", {}, WAIT);
    expect(router.state.location.pathname).toBe(`/evaluation/${LATEST}`);
  });

  it("rests fully lit with no autoplay when the reader asks for reduced motion", async () => {
    setReducedMotion(true);
    renderIntro("/", HeroOnly);
    const stages = await screen.findByRole("list", { name: "Pipeline stages" }, WAIT);
    expect(screen.getByText("Animation off: your system asks for reduced motion.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    for (const item of within(stages)
      .getAllByRole("listitem")
      .filter((li) => li.dataset.stage)) {
      expect(item.dataset.lit).toBe("true");
    }
  });

  it("plays on load, pauses when asked, and steps by hand", async () => {
    const user = userEvent.setup();
    renderIntro("/", HeroOnly);
    await user.click(await screen.findByRole("button", { name: "Pause" }, WAIT));
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    const step = () => Number(/Step (\d) of 9/.exec(screen.getByTestId("stage-detail").textContent ?? "")?.[1]);
    const paused = step();
    expect(paused).toBeGreaterThanOrEqual(1);
    await user.click(screen.getByRole("button", { name: "Previous step" }));
    await waitFor(() => expect(step()).toBe(Math.max(1, paused - 1)));
    await user.click(screen.getByRole("button", { name: "Next step" }));
    await waitFor(() => expect(step()).toBe(Math.max(1, paused - 1) + 1));
  });
});

describe("INTRO live counters", { timeout: 90_000 }, () => {
  it("render the BFF's mock-mode facets and the latest score with its delta", async () => {
    renderIntro("/", LiveCounters);
    const tiles = await screen.findByTestId("live-counters", {}, WAIT);
    const tile = (label: string) => within(tiles).getByRole("group", { name: label });
    expect(tile("Generation runs")).toHaveTextContent("133");
    expect(tile("Evaluations")).toHaveTextContent("40");
    expect(tile("Tables evaluated")).toHaveTextContent("5");
    expect(tile("Tables evaluated")).toHaveTextContent("order_items · orders · products · user_features · users");
    await waitFor(() => expect(tile("Latest overall score")).toHaveTextContent("+0.034 vs the previous evaluation"));
    expect(tile("Latest overall score")).toHaveTextContent("0.96");
    expect(within(tile("Latest overall score")).getByRole("link", { name: LATEST })).toBeInTheDocument();
    expect(await screen.findByText("Seeded mock data · no GCP access needed", {}, WAIT)).toBeInTheDocument();
  });

  it("say so, with a retry, when the BFF does not answer", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(new Response("{}", { status: 503, statusText: "Unavailable" })));
    renderIntro("/", LiveCounters);
    expect(await screen.findByText("Live counters are unavailable", {}, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("INTRO glossary", { timeout: 90_000 }, () => {
  it("searches every registered concept and mirrors the query in the URL", async () => {
    const user = userEvent.setup();
    const router = renderIntro("/", Glossary);
    const input = await screen.findByRole("searchbox", { name: "Search the glossary" }, WAIT);
    await user.type(input, "driving edge");
    const results = await screen.findByRole("list", { name: "Glossary results" }, WAIT);
    await waitFor(() =>
      expect(within(results).getAllByRole("heading", { level: 3 })[0]).toHaveTextContent("Driving edge"),
    );
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "driving edge" }));
  });

  it("opens with the query a link carries", async () => {
    renderIntro("/?q=noise%20floor", Glossary);
    expect(await screen.findByRole("searchbox", { name: "Search the glossary" }, WAIT)).toHaveValue("noise floor");
    const results = await screen.findByRole("list", { name: "Glossary results" }, WAIT);
    await waitFor(() =>
      expect(within(results).getAllByRole("heading", { level: 3 })[0]).toHaveTextContent("Noise floor"),
    );
  });
});
