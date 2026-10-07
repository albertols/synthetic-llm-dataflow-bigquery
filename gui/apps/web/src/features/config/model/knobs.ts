/**
 * The knob model behind the amp: every value, default, choice and source
 * comes from the generated knobs.json (code is the source of truth). This
 * module only classifies knobs into panel parts and formats their values.
 *
 *   dial      settable number → a rotary knob (the GUI's what-if range)
 *   selector  settable with choices → a stepped rotary switch
 *   port      settable free-form value (a URI, a flag string) → a jack, not turnable here
 *   screw     constant → a fixed screw (a code edit changes it)
 *   readout   derived → an LED readout
 */
import type { Knob, KnobChannel, KnobValue } from "@contracts/knobs";
import { knobs as knobsFile } from "@contracts/generated/knobs";

import { formatBytes, formatCount, formatNumber } from "@/lib/format";

export type KnobKind = "dial" | "selector" | "port" | "screw" | "readout";

export const KNOBS: readonly Knob[] = knobsFile.knobs;
export const CHANNELS: readonly KnobChannel[] = knobsFile.channels;
export const ANNOTATIONS = knobsFile.annotations;
export const MEASURED = knobsFile.measured;
export const MEASURED_SOURCES = knobsFile.measured_sources;

const BY_ID = new Map(KNOBS.map((k) => [k.id, k]));

export function getKnob(id: string): Knob | undefined {
  return BY_ID.get(id);
}

/** A knob that must exist (the CONFIG model is built on it); throws on a renamed id so tests catch it. */
export function knob(id: string): Knob {
  const found = BY_ID.get(id);
  if (!found) throw new Error(`knobs.json has no knob "${id}"`);
  return found;
}

/** A knob value as plain text (strings as-is, arrays and objects as JSON). */
export function asText(value: KnobValue | undefined): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** A number from knobs.json (constants such as the 512 pool cap). */
export function knobNumber(id: string): number {
  const value = knob(id).value;
  if (typeof value !== "number") throw new Error(`knob "${id}" is not numeric`);
  return value;
}

/** One MEASURED constant from a figure script (`<script stem>.<NAME>`). */
export function measured(id: string) {
  const found = MEASURED.find((m) => m.id === id);
  if (!found) throw new Error(`knobs.json has no measured value "${id}"`);
  return found;
}

export function isSettable(k: Knob): boolean {
  return k.settable_via.some((via) => via === "cli" || via === "composer" || via === "flex");
}

/**
 * The GUI's what-if range for numeric dials (not a code limit): chosen around
 * the default, log-spaced where the maths is log-shaped. The knob sheet says so.
 */
const DIAL_STEPS: Readonly<Record<string, readonly number[]>> = {
  reference_rows_limit: [1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000],
  num_rows: [
    1_000, 10_000, 100_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000, 50_000_000, 100_000_000,
    200_000_000, 500_000_000, 1_000_000_000,
  ],
  similarity: Array.from({ length: 21 }, (_, i) => Math.round(i * 5) / 100),
  batch_size: [4, 8, 16, 32, 64, 128, 256, 512, 1_024, 10_000, 100_000],
  fk_candidate_cap: [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1_024],
};

/** `env` has no argparse choices; its tiers are the ones config/thresholds.yml defines ratios for. */
const ENV_CHOICES = KNOBS.filter((k) => k.id.startsWith("blocker_failure_ratio_")).map((k) =>
  k.id.replace("blocker_failure_ratio_", ""),
);

export function choicesOf(k: Knob): string[] | null {
  if (k.id === "env") return ENV_CHOICES.length ? ENV_CHOICES : null;
  if (k.choices && k.choices.length >= 2) return k.choices.map(String);
  return null;
}

export function kindOf(k: Knob): KnobKind {
  if (k.settable_via.includes("constant")) return "screw";
  if (k.settable_via.includes("derived")) return "readout";
  if (!isSettable(k)) return "screw";
  if (DIAL_STEPS[k.id]) return "dial";
  if (choicesOf(k)) return "selector";
  return "port";
}

/** Dial steps with the current value inserted, so a typed value (90M) is always a detent. */
export function dialSteps(k: Knob, current: number | null | undefined): number[] {
  const base = DIAL_STEPS[k.id] ?? [];
  const set = new Set(base);
  if (typeof current === "number" && Number.isFinite(current)) set.add(current);
  return [...set].sort((a, b) => a - b);
}

/** The positions of a turnable knob and the index of `current` among them. */
export function positions(k: Knob, current: KnobValue): { values: KnobValue[]; index: number } | null {
  const kind = kindOf(k);
  if (kind === "dial") {
    const values = dialSteps(k, typeof current === "number" ? current : null);
    const index = typeof current === "number" ? values.indexOf(current) : -1;
    return { values, index: index >= 0 ? index : 0 };
  }
  if (kind === "selector") {
    const choices: KnobValue[] = choicesOf(k) ?? [];
    // A default that is none of the choices (eval_mode: unset, the runner decides) is a position of its own.
    const values = choices.includes(asText(k.value)) ? choices : [k.value, ...choices];
    const index = values.findIndex((v) => asText(v) === asText(current));
    return { values, index: index >= 0 ? index : 0 };
  }
  return null;
}

/** The value a scenario starts from: the code default, or the preset's for the required `num_rows`. */
export function defaultValue(k: Knob): KnobValue {
  return k.value;
}

/** "10,000 rows", "0.5", "exact", "512 MiB", "[0.7, 1, 1.3]", "(empty)". */
export function formatKnobValue(k: Knob, value: KnobValue = k.value): string {
  if (value === null || value === undefined) return k.required ? "required" : "unset";
  if (typeof value === "string") {
    if (value === "") return "(empty)";
    return value;
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (k.unit === "bytes") return `${formatBytes(value)} (${formatCount(value)} B)`;
    const text = Number.isInteger(value) ? formatCount(value) : formatNumber(value, 4);
    const unit = k.unit && k.unit !== "0-1" && k.unit !== "ratio" && k.unit !== "share" ? ` ${k.unit}` : "";
    return `${text}${unit}`;
  }
  if (Array.isArray(value)) return `[${value.map((v) => (v === null ? "none" : String(v))).join(", ")}]`;
  return JSON.stringify(value);
}

/** A compact value for the knob face ("10k", "90M", "0.50", "exact"). */
export function shortValue(k: Knob, value: KnobValue = k.value): string {
  if (typeof value === "number") {
    if (k.unit === "bytes") return formatBytes(value, 0);
    if (Math.abs(value) >= 10_000)
      return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
    return Number.isInteger(value) ? formatCount(value) : formatNumber(value, 2);
  }
  if (Array.isArray(value)) {
    const flat = value.every((v) => typeof v === "number" || typeof v === "string");
    const joined = flat ? value.map((v) => String(v)).join("·") : "";
    return flat && joined.length <= 16 ? joined : `${value.length} items`;
  }
  const text = formatKnobValue(k, value);
  return text.length > 16 ? `${text.slice(0, 15)}…` : text;
}

/** Badge text for each way a knob can be set. */
export const SETTABLE_LABEL: Readonly<Record<string, string>> = {
  cli: "CLI",
  composer: "Composer",
  flex: "Flex",
  constant: "Constant",
  derived: "Derived",
};

/** The annotations ("Docs differ") that name this knob. */
export function annotationsFor(id: string) {
  return ANNOTATIONS.filter((a) => a.knobs.includes(id));
}

export function channelKnobs(channelId: string): Knob[] {
  return KNOBS.filter((k) => k.channel === channelId);
}

/** Initial settings: every knob at its code default. */
export function defaultSettings(): Record<string, KnobValue> {
  return Object.fromEntries(KNOBS.map((k) => [k.id, defaultValue(k)]));
}

/** Knobs that feed the scenario calculator (turning them re-computes it). */
export const SCENARIO_KNOBS = ["reference_rows_limit", "num_rows", "source_stats", "uniqueness_mode", "env"] as const;
