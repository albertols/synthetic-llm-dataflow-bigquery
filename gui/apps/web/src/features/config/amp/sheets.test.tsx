import { render, screen } from "@testing-library/react";
import { isValidElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { KNOBS } from "../model/knobs";
import { computeScenario, presetById } from "../model/scenario";
import { inputsFrom, ScenarioProvider, stateFromParams } from "../model/state";
import { KnobSheet } from "./KnobSheet";
import { specFor } from "./knobCharts";

vi.mock("@/components/EChartCanvas", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div data-testid="echart-canvas" aria-label={ariaLabel} />,
}));

const BAD = /NaN|undefined|Infinity|\[object Object\]|≈ 0%|≈ 0\.0%/;

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  return isValidElement(node) ? renderToStaticMarkup(node) : "";
}

describe("every knob sheet (fix round 1)", { timeout: 180_000 }, () => {
  const state = stateFromParams({ scenario: "90m-from-1m" });
  const inputs = inputsFrom(state);

  it("each chart description reads true numbers, and its data are finite", () => {
    let charts = 0;
    for (const k of KNOBS) {
      const spec = specFor(k.id, state.settings, inputs);
      if (!spec) continue;
      charts += 1;
      expect(text(spec.description), k.id).not.toMatch(BAD);
      expect(spec.title, k.id).not.toMatch(BAD);
      for (const row of spec.build().data)
        for (const [key, value] of Object.entries(row))
          if (typeof value === "number") expect(Number.isFinite(value), `${k.id}.${key}`).toBe(true);
    }
    expect(charts).toBeGreaterThanOrEqual(20);
  });

  it("the key-margin chart says what the code comment says: ~4.8 % duplicates at 10×", () => {
    const spec = specFor("fk_key_sample_margin", state.settings, inputs)!;
    expect(text(spec.description)).toContain("4.8%");
  });

  it("renders every sheet with no NaN, no undefined and no false ≈ 0 %", () => {
    const { rerender } = render(
      <ScenarioProvider initialParams={{ scenario: "90m-from-1m" }}>
        <KnobSheet knobId={KNOBS[0]!.id} onClose={() => {}} />
      </ScenarioProvider>,
    );
    for (const k of KNOBS) {
      rerender(
        <ScenarioProvider initialParams={{ scenario: "90m-from-1m" }}>
          <KnobSheet knobId={k.id} onClose={() => {}} />
        </ScenarioProvider>,
      );
      const sheet = screen.getByTestId("knob-sheet");
      expect(sheet.textContent ?? "", k.id).not.toMatch(BAD);
      expect(sheet.textContent ?? "", k.id).toContain(k.label);
    }
  });

  it("the census preset's outputs are finite too", () => {
    const out = computeScenario(inputsFrom(stateFromParams({ scenario: "census" })));
    for (const [key, value] of Object.entries(out))
      if (typeof value === "number") expect(Number.isFinite(value) || key === "ampSample", key).toBe(true);
    expect(presetById("census").inputs.N).toBe(5_000);
  });
});
