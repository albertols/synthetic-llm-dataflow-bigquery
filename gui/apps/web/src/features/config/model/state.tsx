/**
 * One state for the whole tab: the knob settings the amp turns and the data
 * assumptions the calculator adds (source size, rare share, tail quantile …).
 * The amp, the meter bridge, the calculator and the guardrails all read it,
 * so turning a dial re-computes every output on the page.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import type { KnobValue } from "@contracts/knobs";

import { asText, defaultSettings, getKnob } from "./knobs";
import {
  computeScenario,
  presetById,
  recommend,
  type Recommendation,
  type ScenarioInputs,
  type ScenarioOutputs,
  type StatsTier,
  type UniquenessMode,
} from "./scenario";

/** Calculator assumptions that are not pipeline knobs. */
export type ScenarioData = Pick<
  ScenarioInputs,
  "N" | "p" | "q" | "keyspaceLog10" | "columnDistinct" | "nonEmptyShare" | "sdkContainers" | "warm"
>;

type State = { presetId: string; settings: Record<string, KnobValue>; data: ScenarioData };

function fromPreset(presetId: string | undefined): State {
  const preset = presetById(presetId);
  const { N, p, q, keyspaceLog10, columnDistinct, nonEmptyShare, sdkContainers, warm } = preset.inputs;
  return {
    presetId: preset.id,
    settings: {
      ...defaultSettings(),
      reference_rows_limit: preset.inputs.n,
      num_rows: preset.inputs.M,
      source_stats: preset.inputs.tier,
      uniqueness_mode: preset.inputs.uniqueness,
      env: preset.inputs.env,
    },
    data: { N, p, q, keyspaceLog10, columnDistinct, nonEmptyShare, sdkContainers, warm },
  };
}

const TIERS: readonly StatsTier[] = ["off", "sample", "exact"];
const MODES: readonly UniquenessMode[] = ["exact", "exact_chained", "streaming"];

export function inputsFrom(state: Pick<State, "settings" | "data">): ScenarioInputs {
  const { settings, data } = state;
  const num = (id: string, fallback: number) => {
    const v = settings[id];
    return typeof v === "number" && Number.isFinite(v) ? v : fallback;
  };
  const tier = asText(settings.source_stats) as StatsTier;
  const mode = asText(settings.uniqueness_mode) as UniquenessMode;
  return {
    ...data,
    n: num("reference_rows_limit", 10_000),
    M: num("num_rows", 0),
    tier: TIERS.includes(tier) ? tier : "sample",
    uniqueness: MODES.includes(mode) ? mode : "exact",
    env: asText(settings.env) || "dev",
  };
}

export type ScenarioApi = {
  presetId: string;
  settings: Record<string, KnobValue>;
  data: ScenarioData;
  inputs: ScenarioInputs;
  outputs: ScenarioOutputs;
  recommendations: Recommendation[];
  setSetting: (id: string, value: KnobValue) => void;
  resetSetting: (id: string) => void;
  setData: (patch: Partial<ScenarioData>) => void;
  applyPreset: (id: string) => void;
};

const ScenarioContext = createContext<ScenarioApi | null>(null);

export function ScenarioProvider({ initialPreset, children }: { initialPreset?: string; children: ReactNode }) {
  const [state, setState] = useState<State>(() => fromPreset(initialPreset));
  const setSetting = useCallback((id: string, value: KnobValue) => {
    setState((s) => ({ ...s, presetId: "custom", settings: { ...s.settings, [id]: value } }));
  }, []);
  const resetSetting = useCallback((id: string) => {
    const k = getKnob(id);
    if (!k) return;
    // num_rows has no code default (required): reset it to the active preset's M.
    setState((s) => {
      const value = id === "num_rows" ? presetById(s.presetId).inputs.M : k.value;
      return { ...s, settings: { ...s.settings, [id]: value } };
    });
  }, []);
  const setData = useCallback((patch: Partial<ScenarioData>) => {
    setState((s) => ({ ...s, presetId: "custom", data: { ...s.data, ...patch } }));
  }, []);
  const applyPreset = useCallback((id: string) => setState(fromPreset(id)), []);
  const api = useMemo<ScenarioApi>(() => {
    const inputs = inputsFrom(state);
    const outputs = computeScenario(inputs);
    return {
      presetId: state.presetId,
      settings: state.settings,
      data: state.data,
      inputs,
      outputs,
      recommendations: recommend(inputs, outputs),
      setSetting,
      resetSetting,
      setData,
      applyPreset,
    };
  }, [state, setSetting, resetSetting, setData, applyPreset]);
  return <ScenarioContext value={api}>{children}</ScenarioContext>;
}

export function useScenario(): ScenarioApi {
  const api = useContext(ScenarioContext);
  if (!api) throw new Error("useScenario needs a <ScenarioProvider>");
  return api;
}
