/**
 * The knob contract: the shape of `generated/knobs.json`, written by
 * `scripts/gui/export_knobs.py` (values imported from code, never retyped).
 * `generated/knobs.ts` embeds the file typed as `KnobsFile`; the zod twin used
 * by tests and the BFF is `knobsFileSchema` in `knobs.schema.ts`.
 */
import type { ExportedFrom } from "./relational";

/** How a knob can be changed: a launcher flag, a Composer DAG param, a flex-template param, or not at all. */
export type SettableVia = "cli" | "composer" | "flex" | "constant" | "derived";

export type KnobValue = string | number | boolean | null | readonly KnobValue[] | { readonly [key: string]: KnobValue };

export interface KnobChannel {
  /** sampling | generation | free_text | rag | guardrails | relational | serving | evaluation */
  id: string;
  /** Upper-case panel label, e.g. "FREE TEXT". */
  label: string;
  description: string;
}

export interface Knob {
  /** snake_case; the CONFIG concept id is `knob:<id>`. */
  id: string;
  channel: string;
  /** A finer grouping inside the channel, e.g. "Pool ladder". */
  group: string;
  label: string;
  /** The CLI default (or the constant's value). `null` for a required flag. */
  value: KnobValue;
  unit: string | null;
  settable_via: SettableVia[];
  cli_flag?: string;
  composer_param?: string;
  /** Present when the Composer DAG's default differs from the CLI default (raw, may be a deploy-time marker). */
  composer_default?: string;
  flex_param?: string;
  choices?: KnobValue[];
  required?: boolean;
  /** The argparse help text, whitespace-normalized. */
  help?: string;
  /** The comment block above a constant (or a function docstring). */
  comment?: string;
  /** "repo/relative/path.py:LINE", or "planned" (EVALUATION channel before the evaluator CLI exists). */
  source: string;
  /** The text the exporter's test finds on the `source` line. */
  source_token: string | null;
  /** ADR numbers ("0022") cited by the help text, the comment block or the exporter. */
  related_adrs: string[];
  /** Repo-relative docs paths. */
  docs: string[];
}

export interface KnobEvidence {
  source: string;
  role: "docs" | "code";
  /** Text found verbatim on the `source` line. */
  excerpt: string;
}

/** A place where the docs and the code disagree; the UI shows the code and a "Docs differ" callout. */
export interface KnobAnnotation {
  id: string;
  title: string;
  knobs: string[];
  docs_say: string;
  code_does: string;
  evidence: KnobEvidence[];
  links?: { label: string; url: string }[];
  related_adrs: string[];
}

/** One constant from a figure script's MEASURED block (the typed-once source of a measured number). */
export interface MeasuredValue {
  /** "<script stem>.<NAME>" */
  id: string;
  script: string;
  name: string;
  value: KnobValue;
  /** The block header, e.g. "MEASURED (cold job 2026-08-29_07_33_36-…)". */
  block: string;
  comment: string;
  source: string;
}

/** A figure script whose MEASURED blocks feed `measured`, with its docstring's provenance paragraph. */
export interface MeasuredSource {
  script: string;
  provenance: string;
}

export interface KnobsFile {
  generated_by: string;
  note: string;
  /** The commit the `source` links resolve at (see `ExportedFrom`). */
  exported_from: ExportedFrom;
  channels: KnobChannel[];
  knobs: Knob[];
  annotations: KnobAnnotation[];
  measured_sources: MeasuredSource[];
  measured: MeasuredValue[];
}
