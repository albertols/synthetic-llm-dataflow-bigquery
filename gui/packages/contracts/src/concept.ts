/**
 * The (i) concept contract: one entry per idea the GUI explains in an InfoHint.
 *
 * Concept sources live in `src/concepts/<owner>.ts`, one file per owner
 * (`core.ts` for the foundation, `intro.ts`, `evaluation.ts`, `rag.ts`,
 * `config.ts` for the tabs). Each file exports `concepts: readonly Concept[]`.
 * The web app merges every file with `import.meta.glob`
 * (`apps/web/src/lib/concepts.ts`), and `concepts.test.ts` there validates the
 * merged registry against `conceptSchema` (concept.schema.ts): unique ids,
 * https links, KaTeX that parses, diagram ids that exist.
 *
 * This module stays free of runtime dependencies: the registry is in the
 * shell chunk, and the zod schema is only needed by tests and tooling.
 */
export const conceptLinkKinds = ["paper", "docs", "adr", "code"] as const;
export type ConceptLinkKind = (typeof conceptLinkKinds)[number];

export const conceptLevels = ["field", "column", "pair", "row", "table", "relationship", "model"] as const;
export type ConceptLevel = (typeof conceptLevels)[number];

export type ConceptLink = { label: string; url: string; kind: ConceptLinkKind };

export type Concept = {
  /** Namespaced id, e.g. "metric:column.ks", "knob:reference_rows_limit", "rag:great", "core:noise-floor". */
  id: string;
  title: string;
  /** At most two sentences: what the idea is for. */
  purpose: string;
  /** KaTeX source, standard macros only; rendered lazily by `<Formula>`. */
  formula?: string;
  interpretation?: { good?: string; bad?: string; tip?: string };
  pitfalls?: string;
  /** Key into the MiniDiagram registry (`apps/web/src/components/diagrams/*`, `src/features/<tab>/diagrams/*`). */
  diagram?: string;
  links: ConceptLink[];
  level?: ConceptLevel;
};

/** Namespaced id: `<namespace>:<name>`, lower case, dots/dashes/underscores allowed in the name. */
export const conceptIdPattern = /^[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9._-]*$/;

/** Identity helper that keeps literal checking on concept files. */
export function defineConcepts<const T extends readonly Concept[]>(concepts: T): T {
  return concepts;
}
