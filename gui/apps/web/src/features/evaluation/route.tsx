/**
 * EVALUATION route module (owned by the EVALUATION tab).
 *
 * Three routes share it: "/evaluation" (list), "/evaluation/$evaluationId"
 * (run view) and "/evaluation/compare" (compare view). Each gets a search
 * validator and a lazy component; src/router.tsx wires them. Validators use
 * `@/lib/search` so the shell chunk stays small (see intro/route.tsx). Every
 * field is catch-guarded: a malformed or stale link falls back to the
 * default instead of an error page. Lists travel as JSON arrays (the
 * router's default), so a filtered view is a shareable URL.
 */
import { lazyRouteComponent } from "@tanstack/react-router";

import {
  extend,
  finite,
  flag,
  integer,
  list,
  oneOf,
  searchParams,
  text,
  withDefault,
  type SearchOf,
} from "@/lib/search";

const optString = () => text({ max: 200 });
const optNumber = () => finite();
const stringList = () => list(text({ max: 300 }), 50);
const numberList = () => list(finite(), 50);

export const listSortKeys = [
  "evaluated_at",
  "overall_score",
  "fidelity_score",
  "privacy_score",
  "integrity_score",
  "diversity_score",
  "num_rows_requested",
  "status",
] as const;

/** List filters: every facet of `/api/evaluations`, plus sort, page and the rows picked for compare. */
export const listSearchSchema = searchParams({
  q: optString(),
  status: stringList(),
  engine: stringList(),
  llm_model: stringList(),
  embedder: stringList(),
  retrieval: stringList(),
  seed: stringList(),
  tables: stringList(),
  env: stringList(),
  trigger: stringList(),
  runner: stringList(),
  mode: stringList(),
  source_stats_tier: stringList(),
  profiler_version: stringList(),
  evaluator_version: stringList(),
  catalogue_version: stringList(),
  relationship_model: stringList(),
  reference_rows_limit: numberList(),
  num_rows: numberList(),
  similarity_min: optNumber(),
  similarity_max: optNumber(),
  /** Dates (YYYY-MM-DD) or ISO timestamps. */
  from: optString(),
  to: optString(),
  sort: oneOf(listSortKeys),
  order: oneOf(["asc", "desc"]),
  page: integer({ min: 0, max: 10_000 }),
  /** Evaluation ids ticked for compare. */
  pick: stringList(),
});
export type EvaluationListSearch = SearchOf<typeof listSearchSchema>;

/** Compare view: the evaluation ids side by side, plus the list filters. */
export const compareSearchSchema = extend(listSearchSchema, {
  ids: withDefault(list(text(), 12), () => []),
  /** Colour runs by engine or by LLM model in the trend, Pareto and parallel views. */
  color: oneOf(["engine", "model"]),
  /** The A side of the A/B diff (default: the first id). */
  a: optString(),
  /** The B side (default: the second id). */
  b: optString(),
  /** Diff rows: every metric or only the ones that changed beyond noise. */
  rows: oneOf(["changed", "all", "not_comparable"]),
  family: oneOf(["overall", "fidelity", "privacy", "integrity", "diversity"]),
});
export type EvaluationCompareSearch = SearchOf<typeof compareSearchSchema>;

export const runTabs = ["overview", "columns", "pairs", "privacy", "detection", "relational", "params"] as const;
export type RunTabId = (typeof runTabs)[number];

/** Run view: the section, the open column drawer, and the heatmap filters. */
export const runSearchSchema = searchParams({
  tab: oneOf(runTabs),
  /** "table.column" whose drawer is open. */
  column: optString(),
  /** Scope every section to one table (the model graph sets it). */
  table: optString(),
  family: oneOf(["fidelity", "privacy", "integrity", "diversity"]),
  problems: flag(),
  /** Column search in the heatmap. */
  q: optString(),
  /** Heatmap rows shown (grows by 50). */
  rows: integer({ min: 1, max: 5000 }),
});
export type EvaluationRunSearch = SearchOf<typeof runSearchSchema>;

export const listComponent = lazyRouteComponent(() => import("./ListPage"), "EvaluationListPage");
export const runComponent = lazyRouteComponent(() => import("./RunPage"), "EvaluationRunPage");
export const compareComponent = lazyRouteComponent(() => import("./ComparePage"), "EvaluationComparePage");
