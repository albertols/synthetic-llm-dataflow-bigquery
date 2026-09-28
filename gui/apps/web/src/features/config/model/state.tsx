/**
 * One state for the whole tab: the knob settings the amp turns and the data
 * assumptions the calculator adds (source size, rare share, tail quantile …).
 * The amp, the meter bridge, the calculator and the guardrails all read it,
 * so turning a dial re-computes every output on the page.
 *
 * The scenario round-trips through the URL: the active preset (`scenario`)
 * plus every input that differs from it (`N`, `M`, `n`, `p`, `q`, `tier`,
 * `uniq`, `env`, `K`, `D`, `s`, `sdk`, `warm`, `filter`), so a deep link
 * reproduces the calculator exactly.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

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
  "N" | "p" | "q" | "keyspaceLog10" | "columnDistinct" | "nonEmptyShare" | "sourceFilter" | "sdkContainers" | "warm"
>;

/** The URL form of the scenario (see route.tsx); a key is present only when it differs from the preset. */
export type ScenarioParams = {
  scenario?: string;
  N?: number;
  M?: number;
  n?: number;
  p?: number;
  q?: number;
  tier?: string;
  uniq?: string;
  env?: string;
  K?: number;
  D?: number;
  s?: number;
  sdk?: string;
  warm?: boolean;
  filter?: boolean;
};

/** Every scenario key the URL may carry (all written, `undefined` when equal to the preset). */
export const SCENARIO_PARAM_KEYS = [
  "N",
  "M",
  "n",
  "p",
  "q",
  "tier",
  "uniq",
  "env",
  "K",
  "D",
  "s",
  "sdk",
  "warm",
  "filter",
] as const;

type State = {
  /** The active preset: resets and the URL diff are relative to it. */
  presetId: string;
  /** Edited after the preset was applied. */
  custom: boolean;
  settings: Record<string, KnobValue>;
  data: ScenarioData;
};

const TIERS: readonly StatsTier[] = ["off", "sample", "exact"];
const MODES: readonly UniquenessMode[] = ["exact", "exact_chained", "streaming"];

function fromPreset(presetId: string | undefined): State {
  const preset = presetById(presetId);
  const { N, p, q, keyspaceLog10, columnDistinct, nonEmptyShare, sourceFilter, sdkContainers, warm } = preset.inputs;
  return {
    presetId: preset.id,
    custom: false,
    settings: {
      ...defaultSettings(),
      reference_rows_limit: preset.inputs.n,
      num_rows: preset.inputs.M,
      source_stats: preset.inputs.tier,
      uniqueness_mode: preset.inputs.uniqueness,
      env: preset.inputs.env,
    },
    data: { N, p, q, keyspaceLog10, columnDistinct, nonEmptyShare, sourceFilter, sdkContainers, warm },
  };
}

const positive = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;
const share = (v: unknown): v is number => typeof v === "number" && v > 0 && v < 1;

/** The preset, then every valid override from the URL (invalid values are ignored). */
export function stateFromParams(params: ScenarioParams): State {
  const state = fromPreset(params.scenario);
  const settings = { ...state.settings };
  const data = { ...state.data };
  if (positive(params.n)) settings.reference_rows_limit = Math.round(params.n);
  if (positive(params.M)) settings.num_rows = Math.round(params.M);
  if (params.tier && TIERS.includes(params.tier as StatsTier)) settings.source_stats = params.tier;
  if (params.uniq && MODES.includes(params.uniq as UniquenessMode)) settings.uniqueness_mode = params.uniq;
  if (params.env && getKnob(`blocker_failure_ratio_${params.env}`)) settings.env = params.env;
  if (positive(params.N)) data.N = Math.round(params.N);
  if (share(params.p)) data.p = params.p;
  if (share(params.q)) data.q = params.q;
  if (typeof params.K === "number" && params.K >= 1 && params.K <= 30) data.keyspaceLog10 = Math.round(params.K);
  if (positive(params.D)) data.columnDistinct = Math.round(params.D);
  if (typeof params.s === "number" && params.s > 0 && params.s <= 1) data.nonEmptyShare = params.s;
  if (params.sdk === "single" || params.sdk === "multi") data.sdkContainers = params.sdk;
  if (typeof params.warm === "boolean") data.warm = params.warm;
  if (typeof params.filter === "boolean") data.sourceFilter = params.filter;
  const custom =
    JSON.stringify(settings) !== JSON.stringify(state.settings) || JSON.stringify(data) !== JSON.stringify(state.data);
  return { ...state, custom, settings, data };
}

/** The URL form of a state: the preset id, and each input that differs from the preset (else undefined). */
export function paramsFromState(state: Pick<State, "presetId" | "settings" | "data">): ScenarioParams {
  const base = inputsFrom(fromPreset(state.presetId));
  const now = inputsFrom(state);
  const diff = <T,>(a: T, b: T) => (a === b ? undefined : a);
  return {
    scenario: state.presetId,
    N: diff(now.N, base.N),
    M: diff(now.M, base.M),
    n: diff(now.n, base.n),
    p: diff(now.p, base.p),
    q: diff(now.q, base.q),
    tier: diff(now.tier, base.tier),
    uniq: diff(now.uniqueness, base.uniqueness),
    env: diff(now.env, base.env),
    K: diff(now.keyspaceLog10, base.keyspaceLog10),
    D: diff(now.columnDistinct, base.columnDistinct),
    s: diff(now.nonEmptyShare, base.nonEmptyShare),
    sdk: diff(now.sdkContainers, base.sdkContainers),
    warm: diff(now.warm, base.warm),
    filter: diff(now.sourceFilter, base.sourceFilter),
  };
}

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
  /** The active preset (what resets return to). */
  presetId: string;
  /** Edited after the preset was applied. */
  custom: boolean;
  settings: Record<string, KnobValue>;
  data: ScenarioData;
  inputs: ScenarioInputs;
  outputs: ScenarioOutputs;
  recommendations: Recommendation[];
  /** The active preset's value for a knob (the code default for knobs a preset does not set). */
  presetValue: (id: string) => KnobValue;
  setSetting: (id: string, value: KnobValue) => void;
  /** Back to the ACTIVE preset's value (for knobs no preset sets, that is the code default). */
  resetSetting: (id: string) => void;
  setData: (patch: Partial<ScenarioData>) => void;
  applyPreset: (id: string) => void;
};

const ScenarioContext = createContext<ScenarioApi | null>(null);

export function ScenarioProvider({
  initialParams = {},
  onParamsChange,
  children,
}: {
  /** The URL's scenario params (read once, on mount). */
  initialParams?: ScenarioParams;
  /** Called after every change with the URL form of the new state. */
  onParamsChange?: (params: ScenarioParams) => void;
  children: ReactNode;
}) {
  const [state, setState] = useState<State>(() => stateFromParams(initialParams));
  const setSetting = useCallback((id: string, value: KnobValue) => {
    setState((s) => ({ ...s, custom: true, settings: { ...s.settings, [id]: value } }));
  }, []);
  const resetSetting = useCallback((id: string) => {
    if (!getKnob(id)) return;
    setState((s) => ({ ...s, settings: { ...s.settings, [id]: fromPreset(s.presetId).settings[id] ?? null } }));
  }, []);
  const setData = useCallback((patch: Partial<ScenarioData>) => {
    setState((s) => ({ ...s, custom: true, data: { ...s.data, ...patch } }));
  }, []);
  const applyPreset = useCallback((id: string) => setState(fromPreset(id)), []);

  // Mirror every change into the URL (not the initial state: visiting the tab must not rewrite it).
  const initial = useRef(state);
  const notify = useRef(onParamsChange);
  useEffect(() => {
    notify.current = onParamsChange;
  }, [onParamsChange]);
  useEffect(() => {
    if (state === initial.current) return;
    notify.current?.(paramsFromState(state));
  }, [state]);

  const api = useMemo<ScenarioApi>(() => {
    const inputs = inputsFrom(state);
    const outputs = computeScenario(inputs);
    const preset = fromPreset(state.presetId);
    return {
      presetId: state.presetId,
      custom: state.custom,
      settings: state.settings,
      data: state.data,
      inputs,
      outputs,
      recommendations: recommend(inputs, outputs),
      presetValue: (id: string) => preset.settings[id] ?? null,
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
