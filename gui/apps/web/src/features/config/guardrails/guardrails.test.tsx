/**
 * The guardrails' list of runs whose gate left a rule out (ADR 0038): the same
 * verdict pill the EVALUATION run view shows, next to `excluded_blocker_rules`.
 */
import type { ValidationRun } from "@contracts/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configure, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ScenarioProvider } from "../model/state";
import { Guardrails } from "./Guardrails";

configure({ asyncUtilTimeout: 15_000 });
vi.setConfig({ testTimeout: 60_000 });

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart" aria-label={ariaLabel} />,
}));

function run(table: string, overrides: Partial<ValidationRun> = {}): ValidationRun {
  return {
    run_id: `sdfb-launch-a-01-${table}`,
    reference_digest: "d".repeat(64),
    reference_table: `demo-project.synthetic_source.${table}`,
    landing_table: `demo-project.synthetic_data.${table}`,
    engine: "b1_rag",
    model_uri: "gs://synthetic-platform-demo/synthetic/models/gemma4/gemma4-e4b/v1/",
    env: "dev",
    num_rows_requested: 1000,
    valid_count: 1000,
    dlq_count: 0,
    blocker_count: 0,
    dlq_by_rule: "{}",
    blocker_failure_ratio: 0.2,
    observed_blocker_ratio: 0,
    status: "PASSED",
    excluded_blocker_rules: "",
    source_repeat_share: null,
    landing_repeat_share: null,
    repeat_share_delta: null,
    repeat_share_within_tolerance: null,
    created_at: "2026-09-13T08:30:00.000000Z",
    dlq_by_rule_map: {},
    ...overrides,
  };
}

const adjusted = (source: number, landing: number): Partial<ValidationRun> => ({
  excluded_blocker_rules: "pk.duplicate",
  source_repeat_share: source,
  landing_repeat_share: landing,
  repeat_share_delta: landing - source,
  repeat_share_within_tolerance: Math.abs(landing - source) <= 0.05,
});

function renderGuardrails(runs: ValidationRun[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(runs), {
          status: 200,
          headers: { "content-type": "application/json", "x-data-source": "mock" },
        }),
      ),
    ),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ScenarioProvider initialParams={{}}>
        <Guardrails onOpenKnob={() => {}} />
      </ScenarioProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("guardrails: runs whose gate excluded a rule (ADR 0038)", () => {
  it("lists them with both shares and the generator's verdict, each explained by an (i)", async () => {
    renderGuardrails([
      run("B_TABLE"),
      run("E_TABLE", adjusted(0.5027, 0.5026)),
      run("C_TABLE", adjusted(0.6617, 0.4197)),
      run("D_TABLE", adjusted(0.3312, 0.4583)),
      run("G_TABLE", { excluded_blocker_rules: "pk.duplicate" }),
      run("H_TABLE", { excluded_blocker_rules: "row.duplicate" }),
    ]);
    const card = await screen.findByTestId("adjusted-runs");
    const table = await within(card).findByRole("region", { name: "Runs whose gate excluded a rule" });
    const cells = (name: string) =>
      within(within(table).getByText(name).closest("tr")!)
        .getAllByRole("cell")
        .map((cell) => cell.textContent);
    expect(cells("E_TABLE")).toEqual([
      "E_TABLEsdfb-launch-a-01-E_TABLE",
      "PASSED",
      "pk.duplicate",
      "50.3%",
      "50.3%",
      "−0.0 pt",
      expect.stringContaining("Adjusted · faithful"),
    ]);
    expect(cells("C_TABLE").slice(3)).toEqual([
      "66.2%",
      "42.0%",
      "−24.2 pt",
      expect.stringContaining("Adjusted · under"),
    ]);
    expect(cells("D_TABLE").slice(3)).toEqual([
      "33.1%",
      "45.8%",
      "+12.7 pt",
      expect.stringContaining("Adjusted · over"),
    ]);
    // Adjusted, with no share measured: a question, not a verdict.
    expect(cells("G_TABLE").slice(3)).toEqual(["—", "—", "—", expect.stringContaining("Adjusted?")]);
    // Another rule left out of the gate is not a key adjustment.
    expect(cells("H_TABLE").slice(2)).toEqual(["row.duplicate", "—", "—", "—", "not a key adjustment"]);
    // A run whose gate weighed every rule is not listed.
    expect(within(table).queryByText("B_TABLE")).toBeNull();
    expect(within(table).getAllByRole("row")).toHaveLength(6);

    // The card's title, every column header but the first, and every pill carry their (i).
    // The concept batch is lazy: until it lands each (i) is an aria-hidden placeholder, so wait for the first one.
    const adjustedHint = { name: "About: Adjusted model (ADR 0038)" };
    expect(
      (
        await within(card.querySelector<HTMLElement>('[data-slot="card-title"]') ?? card).findAllByRole(
          "button",
          adjustedHint,
        )
      ).length,
    ).toBeGreaterThan(0);
    const headers = within(table).getAllByRole("columnheader");
    expect(headers.map((h) => h.textContent?.trim())).toEqual([
      "Landing table · run_id",
      "Gate",
      "excluded_blocker_rules",
      "Source share",
      "Landing share",
      "Δ",
      "Verdict",
    ]);
    for (const header of headers.slice(1))
      expect(within(header).getByRole("button", { name: /^About: / }), header.textContent ?? "").toBeInTheDocument();
    for (const i of [3, 4, 5])
      expect(
        within(headers[i]!).getByRole("button", { name: "About: Key-repeat share and duplicate rate" }),
      ).toBeInTheDocument();
    const pills = [...card.querySelectorAll<HTMLElement>('[data-slot="adjustment"]')];
    expect(pills).toHaveLength(4);
    for (const pill of pills) expect(within(pill).getByRole("button", adjustedHint)).toBeInTheDocument();
    expect(card).toHaveTextContent("within ±5 pt");
  });

  it("says so when no run excluded a rule", async () => {
    renderGuardrails([run("B_TABLE"), run("E_TABLE", { excluded_blocker_rules: null })]);
    const card = await screen.findByTestId("adjusted-runs");
    expect(await within(card).findByText(/No run in validation_runs excluded a rule/)).toBeInTheDocument();
    expect(within(card).queryByRole("region", { name: "Runs whose gate excluded a rule" })).toBeNull();
  });
});
