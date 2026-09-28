/**
 * The concept registry behind every (i) InfoHint.
 *
 * Sources: `gui/packages/contracts/src/concepts/*.ts`, one file per owner
 * (core.ts = foundation; intro.ts, evaluation.ts, rag.ts, config.ts = tabs;
 * G0b adds the catalogue's "metric:<id>" entries). Each exports
 * `concepts: readonly Concept[]`; they are merged with `import.meta.glob`,
 * so adding a file needs no edit here.
 *
 * Loading: core.ts is bundled with the shell (the top nav uses it). Every
 * other file is one lazy batch, fetched on first need — `useConcept` and
 * InfoHint trigger it, and AppShell prefetches it when the browser is idle —
 * so concept text never counts against the shell's size budget.
 *
 * Ids are namespaced: "core:noise-floor", "metric:column.ks",
 * "knob:reference_rows_limit", "rag:great". A duplicate id keeps the first
 * definition (core first, then files by name) and is reported in
 * `conceptProblems`; `concepts.test.ts` fails the suite on any problem.
 */
import { useEffect, useMemo, useSyncExternalStore } from "react";

import type { Concept } from "@contracts/concept";

export type { Concept, ConceptLevel, ConceptLink, ConceptLinkKind } from "@contracts/concept";

type ConceptModule = { concepts?: readonly Concept[] };

const eagerModules = import.meta.glob<ConceptModule>("../../../../packages/contracts/src/concepts/core.ts", {
  eager: true,
});
const lazyModules = import.meta.glob<ConceptModule>([
  "../../../../packages/contracts/src/concepts/*.ts",
  "!../../../../packages/contracts/src/concepts/core.ts",
  "!../../../../packages/contracts/src/concepts/*.test.ts",
]);

const registry = new Map<string, Concept>();
const origin = new Map<string, string>();
/** Registry defects found while merging (duplicate ids, a file without `concepts`). */
export const conceptProblems: string[] = [];

let state: "partial" | "loading" | "complete" = Object.keys(lazyModules).length ? "partial" : "complete";
let version = 0;
let pending: Promise<void> | null = null;
const listeners = new Set<() => void>();

function fileName(path: string): string {
  return path.split("/").pop() ?? path;
}

function isConceptList(value: unknown): value is readonly Concept[] {
  return Array.isArray(value);
}

function merge(path: string, mod: ConceptModule | undefined) {
  const file = fileName(path);
  const list: unknown = mod?.concepts;
  if (!isConceptList(list)) {
    conceptProblems.push(`${file}: no \`concepts\` array export`);
    return;
  }
  for (const concept of list) {
    const first = origin.get(concept.id);
    if (first) {
      conceptProblems.push(`${file}: duplicate concept id "${concept.id}" (first defined in ${first})`);
      continue;
    }
    registry.set(concept.id, concept);
    origin.set(concept.id, file);
  }
}

for (const path of Object.keys(eagerModules).sort()) merge(path, eagerModules[path]);

function notify() {
  version += 1;
  for (const listener of listeners) listener();
}

/** Loads every lazy concept file (once). Resolves when the registry is complete. */
export function loadConcepts(): Promise<void> {
  if (state === "complete") return Promise.resolve();
  if (pending) return pending;
  state = "loading";
  const paths = Object.keys(lazyModules).sort();
  pending = Promise.all(paths.map((path) => lazyModules[path]!()))
    .then((mods) => {
      paths.forEach((path, index) => merge(path, mods[index]));
      if (import.meta.env.DEV && conceptProblems.length) {
        console.error(`[concepts] ${conceptProblems.length} registry problem(s):\n${conceptProblems.join("\n")}`);
      }
    })
    .catch((error: unknown) => {
      conceptProblems.push(`lazy concept files failed to load: ${String(error)}`);
      console.error("[concepts] failed to load concept files", error);
    })
    .finally(() => {
      state = "complete";
      notify();
    });
  return pending;
}

/** Whether every concept file has been merged. */
export function conceptsComplete(): boolean {
  return state === "complete";
}

const warnedEarly = new Set<string>();

/**
 * The concept for `id` if it is loaded (core concepts always are), else
 * undefined. A synchronous read cannot wait for the lazy files: a miss before
 * they merged starts loading them (so a later call finds the concept) and, in
 * development, warns once per id. Components should prefer `useConcept`,
 * which re-renders when the files arrive; other code can `await loadConcepts()`.
 */
export function getConcept(id: string): Concept | undefined {
  const concept = registry.get(id);
  if (concept || state === "complete") return concept;
  void loadConcepts();
  if (import.meta.env.DEV && !warnedEarly.has(id)) {
    warnedEarly.add(id);
    console.warn(
      `[concepts] getConcept("${id}") ran before the concept files loaded and returned undefined; use useConcept(id) or await loadConcepts() first`,
    );
  }
  return undefined;
}

/** Every loaded concept, sorted by id. Call `loadConcepts()` first for the full list. */
export function listConcepts(): Concept[] {
  return [...registry.values()].sort((a, b) => a.id.localeCompare(b.id));
}

/** The concept file that defined `id` ("core.ts"), for diagnostics. */
export function conceptSource(id: string): string | undefined {
  return origin.get(id);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const getVersion = () => version;

export type ConceptLookup =
  | { status: "ready"; concept: Concept }
  | { status: "loading"; concept?: undefined }
  | { status: "missing"; concept?: undefined };

/** Looks `id` up, loading the lazy concept files if needed; re-renders when they arrive. */
export function useConcept(id: string): ConceptLookup {
  useSyncExternalStore(subscribe, getVersion, getVersion);
  const concept = registry.get(id);
  const complete = state === "complete";
  useEffect(() => {
    if (!concept && !complete) void loadConcepts();
  }, [concept, complete]);
  if (concept) return { status: "ready", concept };
  return complete ? { status: "missing" } : { status: "loading" };
}

/** All concepts, loading the lazy files if needed (glossary search). */
export function useConcepts(): { concepts: Concept[]; complete: boolean } {
  const current = useSyncExternalStore(subscribe, getVersion, getVersion);
  const complete = state === "complete";
  useEffect(() => {
    if (!complete) void loadConcepts();
  }, [complete]);
  const concepts = useMemo(() => (current >= 0 ? listConcepts() : []), [current]);
  return { concepts, complete };
}
