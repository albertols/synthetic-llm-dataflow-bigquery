import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { KNOBS, kindOf } from "../model/knobs";
import { ScenarioProvider } from "../model/state";
import { AmpPanel } from "./AmpPanel";
import { KnobSheet } from "./KnobSheet";

// ECharts needs a real canvas; the sheet's chart frame is what matters here.
vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart-canvas" aria-label={ariaLabel} />,
}));

function Harness({ channel = "all" }: { channel?: string }) {
  const [knob, setKnob] = useState<string | undefined>();
  return (
    <ScenarioProvider initialPreset="90m-from-1m">
      <AmpPanel onOpenKnob={setKnob} initialChannel={channel} />
      <KnobSheet knobId={knob} onClose={() => setKnob(undefined)} />
    </ScenarioProvider>
  );
}

const meter = (label: string) => {
  const bridge = screen.getByTestId("meter-bridge");
  const term = within(bridge).getByText(label);
  return term.closest("div")!.querySelector("dd")!.textContent;
};

// The amp renders all 84 knobs: jsdom needs more than the default 5 s per test.
describe("the pipeline amp", { timeout: 30_000 }, () => {
  it("renders every knobs.json knob, one slider per turnable knob and none for constants", () => {
    render(<Harness />);
    // role queries walk the whole 84-knob tree; a selector is enough to count.
    const sliders = document.querySelectorAll('[role="slider"]');
    const turnable = KNOBS.filter((k) => ["dial", "selector"].includes(kindOf(k)));
    expect(sliders).toHaveLength(turnable.length);
    for (const k of KNOBS) expect(document.querySelector(`[data-knob="${k.id}"]`), k.id).not.toBeNull();
    for (const k of KNOBS.filter((k) => k.settable_via.includes("constant"))) {
      const tile = document.querySelector(`[data-knob="${k.id}"]`)!;
      expect(tile.querySelector('[role="slider"]'), k.id).toBeNull();
      expect(tile.getAttribute("data-kind")).toBe("screw");
    }
  });

  it("announces a constant as a fixed screw that does not turn", async () => {
    const user = userEvent.setup();
    render(<Harness channel="free_text" />);
    const screw = screen.getByRole("button", { name: /^Pool cap: 512 values$/ });
    expect(screw).toHaveAccessibleDescription(/Fixed constant: not settable/);
    screw.focus();
    await user.keyboard("{ArrowUp}{ArrowUp}{End}");
    expect(screen.getByRole("button", { name: /^Pool cap: 512 values$/ })).toBeInTheDocument();
    expect(meter("Rows / pool value")).toBe("175,781");
  });

  it("turns a dial with the keyboard, with aria-valuetext, and the meter bridge follows", async () => {
    const user = userEvent.setup();
    render(<Harness channel="sampling" />);
    const dial = screen.getByRole("slider", { name: "Reference rows (n)" });
    expect(dial).toHaveAttribute("aria-valuetext", "10,000 rows");
    expect(meter("DKW ε(n)")).toBe("0.0136");

    dial.focus();
    await user.keyboard("{ArrowUp}");
    expect(dial).toHaveAttribute("aria-valuetext", "20,000 rows");
    expect(meter("Sample n")).toBe("20,000");
    expect(meter("DKW ε(n)")).toBe("0.0096");

    await user.keyboard("{Home}");
    expect(dial).toHaveAttribute("aria-valuetext", "1,000 rows");
    await user.keyboard("{End}");
    expect(dial).toHaveAttribute("aria-valuetext", "1,000,000 rows");
    // The source has 1M rows: a 1M sample is a census.
    expect(meter("DKW ε(n)")).toBe("0 (census)");
    await user.keyboard("{PageDown}{ArrowLeft}");
    expect(dial.getAttribute("aria-valuetext")).not.toBe("1,000,000 rows");
  });

  it("steps a selector through the code's choices", async () => {
    const user = userEvent.setup();
    render(<Harness channel="sampling" />);
    const tier = screen.getByRole("slider", { name: "Source-stats tier" });
    expect(tier).toHaveAttribute("aria-valuetext", "sample");
    tier.focus();
    await user.keyboard("{ArrowRight}");
    expect(tier).toHaveAttribute("aria-valuetext", "exact");
    expect(meter("Stats tier")).toBe("exact");
    await user.keyboard("{ArrowRight}");
    expect(tier).toHaveAttribute("aria-valuetext", "exact");
  });

  it("opens a knob's sheet on click and on Enter, with how to set it", async () => {
    const user = userEvent.setup();
    render(<Harness channel="sampling" />);
    await user.click(screen.getByRole("slider", { name: "Reference rows (n)" }));
    let sheet = await screen.findByRole("dialog", { name: "Reference rows (n)" });
    expect(within(sheet).getByText("--reference_rows_limit")).toBeInTheDocument();
    expect(within(sheet).getByTestId("knob-sheet-value")).toHaveTextContent("10,000 rows");
    expect(within(sheet).getByRole("link", { name: /run_pipeline\.py:281/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/blob\/[0-9a-f]{40}\/packages\/sdfb-beam\/src\/sdfb_beam\/cli\/run_pipeline\.py#L281$/),
    );
    expect(within(sheet).getByRole("figure", { name: /DKW band/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("radio", { name: "GENERATION" }));

    const similarity = screen.getByRole("slider", { name: "Similarity" });
    similarity.focus();
    await user.keyboard("{Enter}");
    sheet = await screen.findByRole("dialog", { name: "Similarity" });
    expect(within(sheet).getByText(/Docs differ: similarity means different things/)).toBeInTheDocument();
  });

  it("shows a constant's sheet as not settable, and a planned knob as planned", async () => {
    const user = userEvent.setup();
    render(<Harness channel="free_text" />);
    await user.click(screen.getByRole("button", { name: /^Pool cap: 512 values$/ }));
    const sheet = await screen.findByRole("dialog", { name: "Pool cap" });
    expect(within(sheet).getByText(/Fixed constant: not settable, change it in code/)).toBeInTheDocument();
    expect(within(sheet).queryByRole("slider")).toBeNull();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("radio", { name: "EVALUATION" }));

    const planned = screen.getByRole("button", { name: /^Evaluation mode: exact$/ });
    expect(planned).toHaveAccessibleDescription(/Planned: the evaluator CLI is not shipped yet/);
  });
});
