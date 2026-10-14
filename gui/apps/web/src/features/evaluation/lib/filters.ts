/**
 * The list's URL search ↔ the `/api/evaluations` filter, the facet
 * definitions the filter bar renders, preset views, and the named views a
 * viewer saves in this browser (localStorage, guarded: private windows and
 * blocked storage fall back to none).
 */
import type { EvaluationFilter, Facets } from "@contracts/api";

import type { EvaluationListSearch } from "../route";
import { fmtSig, shortModel } from "./format";

export const PAGE_SIZE = 25;

export type ListFacetKey =
  | "status"
  | "engine"
  | "llm_model"
  | "embedder"
  | "retrieval"
  | "seed"
  | "tables"
  | "env"
  | "trigger"
  | "runner"
  | "mode"
  | "source_stats_tier"
  | "profiler_version"
  | "evaluator_version"
  | "catalogue_version"
  | "relationship_model"
  | "reference_rows_limit"
  | "num_rows";

export interface FacetDef {
  key: ListFacetKey;
  label: string;
  /** Primary facets sit in the first filter row; the rest behind "More filters". */
  primary: boolean;
  options: (facets: Facets) => { value: string; label: string }[];
  numeric?: boolean;
}

const plain = (values: readonly (string | number)[]) => values.map((v) => ({ value: String(v), label: String(v) }));
const count = (values: readonly number[]) =>
  values.map((v) => ({ value: String(v), label: new Intl.NumberFormat("en-US").format(v) }));

export const FACETS: readonly FacetDef[] = [
  { key: "status", label: "Status", primary: true, options: (f) => plain(f.statuses) },
  { key: "engine", label: "Engine", primary: true, options: (f) => plain(f.engines) },
  {
    key: "llm_model",
    label: "LLM model",
    primary: true,
    options: (f) => f.llm_models.map((m) => ({ value: m, label: shortModel(m) })),
  },
  { key: "tables", label: "Tables", primary: true, options: (f) => plain(f.tables) },
  { key: "env", label: "Env", primary: true, options: (f) => plain(f.envs) },
  { key: "embedder", label: "Embedder", primary: false, options: (f) => plain(f.embedders) },
  { key: "retrieval", label: "Retrieval", primary: false, options: (f) => plain(f.retrieval_methods) },
  { key: "seed", label: "Seed", primary: false, options: (f) => plain(f.seeds) },
  {
    key: "reference_rows_limit",
    label: "Reference rows",
    primary: false,
    numeric: true,
    options: (f) => count(f.reference_rows_limits),
  },
  { key: "num_rows", label: "Rows requested", primary: false, numeric: true, options: (f) => count(f.num_rows) },
  { key: "source_stats_tier", label: "Source-stats tier", primary: false, options: (f) => plain(f.source_stats_tiers) },
  { key: "profiler_version", label: "Profiler", primary: false, options: (f) => plain(f.profiler_versions) },
  { key: "trigger", label: "Trigger", primary: false, options: (f) => plain(f.triggers) },
  { key: "runner", label: "Runner", primary: false, options: (f) => plain(f.runners) },
  { key: "mode", label: "Mode", primary: false, options: (f) => plain(f.modes) },
  { key: "evaluator_version", label: "Evaluator", primary: false, options: (f) => plain(f.evaluator_versions) },
  { key: "catalogue_version", label: "Catalogue", primary: false, options: (f) => plain(f.catalogue_versions) },
  {
    key: "relationship_model",
    label: "Relationship model",
    primary: false,
    options: (f) => plain(f.relationship_models),
  },
];

/** A date "YYYY-MM-DD" or an ISO timestamp passes; anything else is dropped (the API would 400). */
export function validBound(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.test(value)) return value;
  return undefined;
}

/** The URL search → the API filter (page → offset). */
export function toFilter(search: EvaluationListSearch, pageSize = PAGE_SIZE): EvaluationFilter {
  return {
    q: search.q || undefined,
    status: search.status,
    engine: search.engine,
    llm_model: search.llm_model,
    embedder: search.embedder,
    retrieval: search.retrieval,
    seed: search.seed,
    tables: search.tables,
    env: search.env,
    trigger: search.trigger,
    runner: search.runner,
    mode: search.mode,
    source_stats_tier: search.source_stats_tier,
    profiler_version: search.profiler_version,
    evaluator_version: search.evaluator_version,
    catalogue_version: search.catalogue_version,
    relationship_model: search.relationship_model,
    reference_rows_limit: search.reference_rows_limit,
    num_rows: search.num_rows,
    similarity_min: search.similarity_min,
    similarity_max: search.similarity_max,
    from: validBound(search.from),
    to: validBound(search.to),
    sort: search.sort,
    order: search.order,
    offset: (search.page ?? 0) * pageSize,
    limit: pageSize,
  };
}

/** Active filters as removable chips: [key, label, the search patch that removes it]. */
export function activeChips(
  search: EvaluationListSearch,
  facets?: Facets,
): { id: string; label: string; remove: Partial<EvaluationListSearch> }[] {
  const chips: { id: string; label: string; remove: Partial<EvaluationListSearch> }[] = [];
  if (search.q) chips.push({ id: "q", label: `“${search.q}”`, remove: { q: undefined } });
  for (const def of FACETS) {
    const values = search[def.key] as (string | number)[] | undefined;
    if (!values?.length) continue;
    const options = facets ? def.options(facets) : [];
    for (const value of values) {
      const label = options.find((o) => o.value === String(value))?.label ?? String(value);
      const rest = values.filter((v) => v !== value);
      chips.push({
        id: `${def.key}:${value}`,
        label: `${def.label}: ${label}`,
        remove: { [def.key]: rest.length ? rest : undefined },
      });
    }
  }
  if (search.similarity_min !== undefined)
    chips.push({
      id: "smin",
      label: `Similarity ≥ ${fmtSig(search.similarity_min)}`,
      remove: { similarity_min: undefined },
    });
  if (search.similarity_max !== undefined)
    chips.push({
      id: "smax",
      label: `Similarity ≤ ${fmtSig(search.similarity_max)}`,
      remove: { similarity_max: undefined },
    });
  if (search.from) chips.push({ id: "from", label: `From ${search.from}`, remove: { from: undefined } });
  if (search.to) chips.push({ id: "to", label: `Before ${search.to}`, remove: { to: undefined } });
  return chips;
}

export const EMPTY_FILTERS: Partial<EvaluationListSearch> = Object.fromEntries(
  [...FACETS.map((f) => f.key), "q", "similarity_min", "similarity_max", "from", "to", "page"].map((k) => [
    k,
    undefined,
  ]),
);

function isoDaysAgo(days: number, now = Date.now()): string {
  return new Date(now - days * 86_400_000).toISOString().slice(0, 10);
}

/** Preset views: a name and the search they apply (on top of cleared filters). */
export function presetViews(now = Date.now()): { id: string; label: string; search: Partial<EvaluationListSearch> }[] {
  return [
    { id: "failed", label: "Not clean", search: { status: ["FAILED", "PARTIAL", "SUCCEEDED_WITH_WARNINGS"] } },
    { id: "privacy", label: "Worst privacy first", search: { sort: "privacy_score", order: "asc" } },
    { id: "b2", label: "b2 library runs", search: { engine: ["b2_library"] } },
    { id: "exact", label: "Exact source stats", search: { source_stats_tier: ["exact"] } },
    { id: "week", label: "Last 7 days", search: { from: isoDaysAgo(7, now) } },
  ];
}

// ------------------------------------------------------------ saved views --

export interface SavedView {
  name: string;
  search: EvaluationListSearch;
}

const STORAGE_KEY = "synthetic-platform.evaluation.views";

export function readSavedViews(): SavedView[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed
          .filter(
            (v): v is SavedView => typeof v === "object" && v !== null && typeof (v as SavedView).name === "string",
          )
          .slice(0, 20)
      : [];
  } catch {
    return [];
  }
}

export function writeSavedViews(views: SavedView[]): boolean {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(views.slice(0, 20)));
    return true;
  } catch {
    return false;
  }
}
