import { describe, expect, it } from "vitest";

import { computeScenario, presetById } from "./scenario";
import { inputsFrom, paramsFromState, SCENARIO_PARAM_KEYS, stateFromParams } from "./state";

describe("scenario ↔ URL", () => {
  it("a preset alone writes only its id", () => {
    const params = paramsFromState(stateFromParams({ scenario: "census" }));
    expect(params.scenario).toBe("census");
    for (const key of SCENARIO_PARAM_KEYS) expect(params[key], key).toBeUndefined();
  });

  it("every input round-trips: a deep link reproduces the scenario", () => {
    const params = {
      scenario: "exact-tier",
      N: 2_000_000,
      M: 5_000_000,
      n: 50_000,
      p: 0.0001,
      q: 0.99,
      tier: "sample",
      uniq: "streaming",
      env: "prd",
      K: 15,
      D: 99_000,
      s: 0.5,
      sdk: "multi",
      warm: true,
      filter: false,
    };
    const state = stateFromParams(params);
    expect(state.custom).toBe(true);
    expect(paramsFromState(state)).toEqual(params);
    const inputs = inputsFrom(state);
    expect(inputs).toMatchObject({
      N: 2_000_000,
      M: 5_000_000,
      n: 50_000,
      tier: "sample",
      uniqueness: "streaming",
      env: "prd",
      keyspaceLog10: 15,
      columnDistinct: 99_000,
      nonEmptyShare: 0.5,
      sdkContainers: "multi",
      warm: true,
      sourceFilter: false,
    });
    expect(computeScenario(inputs).blockerRatio).toBe(0.01);
  });

  it("ignores invalid values instead of breaking the calculator", () => {
    const state = stateFromParams({ scenario: "nope", n: -5, p: 3, tier: "all", env: "qa", K: 99, s: 0 });
    expect(state.presetId).toBe(presetById(undefined).id);
    expect(state.custom).toBe(false);
  });
});
