/**
 * Numbers the INTRO page prints, read from the generated contracts — knob
 * defaults exported from the Python code with their `path:line`, and the
 * metric catalogue — so nothing here is typed twice.
 */
import { catalogue, catalogueFamilies, catalogueLevels } from "@contracts/generated/catalogue";
import { knobs, type KnobId } from "@contracts/generated/knobs";
import type { Knob } from "@contracts/knobs";
import { dkwEpsilon } from "@synthetic-platform/stats/intervals";

const BY_ID = new Map<string, Knob>(knobs.knobs.map((knob) => [knob.id, knob]));

export function knob(id: KnobId): Knob | undefined {
  return BY_ID.get(id);
}

/** A knob's default as a number, or null when it is not numeric (or unknown). */
export function knobNumber(id: KnobId): number | null {
  const value = knob(id)?.value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

/** A knob's default as text (numbers and strings only), or null. */
export function knobText(id: KnobId): string | null {
  const value = knob(id)?.value;
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

/** A knob's allowed values (argparse choices), as strings. */
export function knobChoices(id: KnobId): string[] {
  return (knob(id)?.choices ?? []).map(String);
}

/** The values the stage labels print, each read from its contract. */
export const facts = {
  referenceRows: knobNumber("reference_rows_limit"),
  rowDocs: knobNumber("max_row_doc_rows"),
  embedDim: knobNumber("hashing_embedder_dim"),
  topK: knobNumber("rag_top_k"),
  poolMax: knobNumber("free_text_pool_max"),
  statsTiers: knobChoices("source_stats").filter((choice) => choice !== "off"),
  profilerVersion: knobText("profiler_version"),
  catalogueMetrics: catalogue.length,
  catalogueLevels: catalogueLevels.length,
  /** Measured families (the catalogue's "overall" is their roll-up). */
  catalogueFamilies: catalogueFamilies.filter((family) => family !== "overall"),
};

/** The DKW band ε at the default reference size, α = 0.05 (null when the knob is missing). */
export function referenceDkw(alpha = 0.05): number | null {
  return facts.referenceRows ? dkwEpsilon(facts.referenceRows, alpha) : null;
}
