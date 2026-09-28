/**
 * Everything the RAG sections share: the URL state, the resolved set, the
 * cloud of the current space, its projections and the seeds of the current
 * strategy. One hook, called once by RagPage.
 */
import { getRouteApi } from "@tanstack/react-router";
import { useCallback, useMemo } from "react";

import type { RagSet } from "@contracts/api";
import { knobs } from "@contracts/generated/knobs";

import { useFacets } from "@/lib/api";

import { isStrategyId, type StrategyId } from "./lib/strategies";
import { resolveSelection, spaceRetrieves, type Resolved } from "./lib/selection";
import { useCloud } from "./lib/useCloud";
import { useProjection, type ProjectionMethod, type StrategyArgs } from "./lib/useProjection";
import type { RagSearch } from "./route";

const routeApi = getRouteApi("/rag");

function knobNumber(id: string, fallback: number): number {
  const value = knobs.knobs.find((k) => k.id === id)?.value;
  return typeof value === "number" ? value : fallback;
}

/** `rag_top_k` (engine.py `_DEFAULT_TOP_K`). */
export const TOP_K = knobNumber("rag_top_k", 8);
/** `MAX_ROW_DOC_ROWS` (rag/chunking.py). */
export const MAX_ROW_DOC_ROWS = knobNumber("max_row_doc_rows", 1024);
/** `MAX_FREE_TEXT_VALUES_PER_COLUMN` (rag/chunking.py). */
export const MAX_VALUES_PER_COLUMN = knobNumber("max_free_text_values_per_column", 1024);
/** `FREE_TEXT_POOL_MAX` (generation_plan.py). */
export const FREE_TEXT_POOL_MAX = knobNumber("free_text_pool_max", 512);
/** `--reference_rows_limit` default. */
export const REFERENCE_ROWS_LIMIT = knobNumber("reference_rows_limit", 10_000);

export function useRagModel() {
  const search = routeApi.useSearch();
  const navigate = routeApi.useNavigate();
  const facets = useFacets();
  const sets: RagSet[] = useMemo(() => facets.data?.data.rag ?? [], [facets.data]);
  const resolved: Resolved | null = useMemo(
    () =>
      resolveSelection(sets, {
        table: search.table,
        digest: search.digest,
        embedder: search.embedder,
        space: search.space,
      }),
    [sets, search.table, search.digest, search.embedder, search.space],
  );
  const set = resolved?.set ?? null;
  const cloudQuery = useCloud(resolved?.space ?? null, set, sets);
  const cloud = cloudQuery.data?.cloud ?? null;
  const method: ProjectionMethod = search.proj ?? "pca";
  const strategy: StrategyId = isStrategyId(search.strategy) ? search.strategy : "centroid";
  const k = search.k ?? TOP_K;
  const attempt = search.attempt ?? 0;
  const retrieves = resolved ? spaceRetrieves(resolved.space) : false;
  const walk = strategy === "kcenter_rotate" ? "kcenter_rotate" : "kcenter";
  const strategyArgs: StrategyArgs | null = useMemo(
    () => (retrieves ? { k, attempt, walk } : null),
    [retrieves, k, attempt, walk],
  );
  const projection = useProjection(
    cloud,
    method,
    set?.reference_digest ?? "",
    set ? `${set.embedder_id}/${set.embedder_version}` : "",
    strategyArgs,
  );
  // The strategies run in the worker (all five at once); the rings are the current one's picks.
  const strategyRows = projection.strategies?.status === "done" ? projection.strategies.data.rows : null;
  const seeds = useMemo(() => strategyRows?.find((r) => r.id === strategy)?.picks ?? [], [strategyRows, strategy]);

  const setSearch = useCallback(
    (patch: Partial<RagSearch>) => {
      void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true, resetScroll: false });
    },
    [navigate],
  );

  return {
    search,
    setSearch,
    facets,
    sets,
    resolved,
    set,
    cloudQuery,
    cloud,
    method,
    projection,
    strategy,
    k,
    attempt,
    retrieves,
    seeds,
  };
}

export type RagModel = ReturnType<typeof useRagModel>;
