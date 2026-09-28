/**
 * The typed client for the BFF (`/api/*`, apps/server) and its TanStack Query hooks.
 *
 * Types come from `@contracts/api` as `import type` only: the server validates
 * every response against the generated zod, so the browser ships no zod here
 * (this module sits in the shell chunk through `useDataSource`).
 *
 * Every hook's `data` is an `ApiResult<T>`: the payload plus the BigQuery
 * bytes the route's queries would scan (`bytesEstimate`, from the dry run;
 * null in mock mode) — show it next to the data it cost. Lists in query
 * params are sent comma-separated; `undefined`, `null` and `""` are dropped.
 *
 *   const { data, isPending, error } = useEvaluations({ engine: ["b1_rag"], limit: 50 });
 *   data?.data.items; data?.bytesEstimate;
 */
import { keepPreviousData, queryOptions, useQuery } from "@tanstack/react-query";

import type {
  ChunkMeta,
  Comparison,
  DlqSummary,
  EvaluationDetail,
  EvaluationFilter,
  EvaluationSummary,
  Facets,
  FreetextPool,
  FreetextPoolsQuery,
  Health,
  Page,
  ProfileQuery,
  ProfileRow,
  RagChunksQuery,
  RunFilter,
  SourceStats,
  SourceStatsQuery,
  TrendPoint,
  TrendQuery,
  ValidationRun,
} from "@contracts/api";
import type { CatalogueMetric } from "@contracts/generated/catalogue";
import type { KnobsFile } from "@contracts/knobs";
import { decodeVectorEnvelope, type VectorEnvelope } from "@contracts/vectors";

export interface ApiResult<T> {
  data: T;
  /** Bytes the route's BigQuery queries would scan (dry run); null in mock mode. */
  bytesEstimate: number | null;
  dataSource: "mock" | "bigquery" | null;
}

/** A non-2xx answer; `details` carries validation issues or a contract mismatch. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly details: string[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryValue = string | number | boolean | null | undefined | readonly (string | number)[];

/** `{ engine: ["a", "b"], limit: 10, q: undefined }` → `engine=a%2Cb&limit=10`. */
export function toQuery(params: object = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params as Record<string, QueryValue>)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      if (value.length) search.set(key, value.join(","));
    } else search.set(key, String(value));
  }
  return search.toString();
}

const BASE = "/api";

async function send(path: string, params?: object, signal?: AbortSignal): Promise<Response> {
  const query = toQuery(params);
  const response = await fetch(`${BASE}${path}${query ? `?${query}` : ""}`, { signal, headers: { accept: "*/*" } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let details: string[] = [];
    try {
      const body = (await response.json()) as { message?: string; details?: string[] };
      message = body.message ?? message;
      details = body.details ?? [];
    } catch {
      /* a non-JSON error body keeps the status text */
    }
    throw new ApiError(response.status, message, details);
  }
  return response;
}

function meta(response: Response): Omit<ApiResult<unknown>, "data"> {
  const bytes = response.headers.get("x-bq-bytes-estimate");
  const source = response.headers.get("x-data-source");
  return {
    bytesEstimate: bytes === null ? null : Number(bytes),
    dataSource: source === "mock" || source === "bigquery" ? source : null,
  };
}

async function getJson<T>(path: string, params?: object, signal?: AbortSignal): Promise<ApiResult<T>> {
  const response = await send(path, params, signal);
  return { data: (await response.json()) as T, ...meta(response) };
}

export type RagChunks = VectorEnvelope<ChunkMeta>;

/** Plain functions (usable outside React); every hook below wraps one. */
export const api = {
  health: (signal?: AbortSignal) => getJson<Health>("/health", undefined, signal),
  facets: (signal?: AbortSignal) => getJson<Facets>("/facets", undefined, signal),
  evaluations: (filter: EvaluationFilter = {}, signal?: AbortSignal) =>
    getJson<Page<EvaluationSummary>>("/evaluations", filter, signal),
  evaluation: (id: string, options: { profiles?: "all" | "none" } = {}, signal?: AbortSignal) =>
    getJson<EvaluationDetail>(`/evaluations/${encodeURIComponent(id)}`, options, signal),
  profiles: (id: string, query: ProfileQuery = {}, signal?: AbortSignal) =>
    getJson<ProfileRow[]>(`/evaluations/${encodeURIComponent(id)}/profiles`, query, signal),
  compare: (ids: readonly string[], signal?: AbortSignal) => getJson<Comparison>("/compare", { ids }, signal),
  trend: (query: TrendQuery, signal?: AbortSignal) => getJson<TrendPoint[]>("/trend", query, signal),
  runs: (filter: RunFilter = {}, signal?: AbortSignal) => getJson<ValidationRun[]>("/runs", filter, signal),
  dlq: (runIds: readonly string[], signal?: AbortSignal) => getJson<DlqSummary[]>("/dlq", { run_ids: runIds }, signal),
  sourceStats: (query: SourceStatsQuery, signal?: AbortSignal) => getJson<SourceStats>("/source-stats", query, signal),
  /** Binary: chunk metadata + Float32 vectors (≤ 5,000 chunks, ≤ 5 MB). */
  ragChunks: async (query: RagChunksQuery, signal?: AbortSignal): Promise<ApiResult<RagChunks>> => {
    const response = await send("/rag/chunks", query, signal);
    return { data: decodeVectorEnvelope<ChunkMeta>(await response.arrayBuffer()), ...meta(response) };
  },
  pools: (query: FreetextPoolsQuery, signal?: AbortSignal) => getJson<FreetextPool[]>("/rag/pools", query, signal),
  knobs: (signal?: AbortSignal) => getJson<KnobsFile>("/knobs", undefined, signal),
  catalogue: (signal?: AbortSignal) => getJson<CatalogueResponse>("/catalogue", undefined, signal),
};

export interface CatalogueResponse {
  version: string;
  levels: string[];
  families: string[];
  metrics: CatalogueMetric[];
}

/** Query keys, for invalidation and `queryClient.prefetchQuery`. */
export const queryKeys = {
  health: ["api", "health"] as const,
  facets: ["api", "facets"] as const,
  evaluations: (filter: EvaluationFilter) => ["api", "evaluations", filter] as const,
  evaluation: (id: string, profiles: "all" | "none") => ["api", "evaluation", id, profiles] as const,
  profiles: (id: string, query: ProfileQuery) => ["api", "profiles", id, query] as const,
  compare: (ids: readonly string[]) => ["api", "compare", [...ids]] as const,
  trend: (query: TrendQuery) => ["api", "trend", query] as const,
  runs: (filter: RunFilter) => ["api", "runs", filter] as const,
  dlq: (runIds: readonly string[]) => ["api", "dlq", [...runIds]] as const,
  sourceStats: (query: SourceStatsQuery) => ["api", "source-stats", query] as const,
  ragChunks: (query: RagChunksQuery) => ["api", "rag-chunks", query] as const,
  pools: (query: FreetextPoolsQuery) => ["api", "pools", query] as const,
  knobs: ["api", "knobs"] as const,
  catalogue: ["api", "catalogue"] as const,
};

/** Health is cheap and never touches BigQuery; the badge polls it rarely. */
export const healthQuery = queryOptions({
  queryKey: queryKeys.health,
  queryFn: ({ signal }) => api.health(signal),
  staleTime: 5 * 60_000,
  retry: 2,
});

export function useHealth() {
  return useQuery(healthQuery);
}

export function useFacets() {
  return useQuery({ queryKey: queryKeys.facets, queryFn: ({ signal }) => api.facets(signal), staleTime: 5 * 60_000 });
}

/** Keeps the previous page on screen while a new filter loads (UX: refetch at reduced opacity). */
export function useEvaluations(filter: EvaluationFilter = {}) {
  return useQuery({
    queryKey: queryKeys.evaluations(filter),
    queryFn: ({ signal }) => api.evaluations(filter, signal),
    placeholderData: keepPreviousData,
  });
}

export function useEvaluation(id: string | undefined, options: { profiles?: "all" | "none" } = {}) {
  const profiles = options.profiles ?? "all";
  return useQuery({
    queryKey: queryKeys.evaluation(id ?? "", profiles),
    queryFn: ({ signal }) => api.evaluation(id!, { profiles }, signal),
    enabled: !!id,
  });
}

export function useProfiles(id: string | undefined, query: ProfileQuery = {}, enabled = true) {
  return useQuery({
    queryKey: queryKeys.profiles(id ?? "", query),
    queryFn: ({ signal }) => api.profiles(id!, query, signal),
    enabled: !!id && enabled,
  });
}

export function useCompare(ids: readonly string[]) {
  return useQuery({
    queryKey: queryKeys.compare(ids),
    queryFn: ({ signal }) => api.compare(ids, signal),
    enabled: ids.length > 0,
    placeholderData: keepPreviousData,
  });
}

export function useTrend(query: TrendQuery | undefined) {
  return useQuery({
    queryKey: queryKeys.trend(query ?? { metric_id: "" }),
    queryFn: ({ signal }) => api.trend(query!, signal),
    enabled: !!query?.metric_id,
    placeholderData: keepPreviousData,
  });
}

export function useRuns(filter: RunFilter = {}) {
  return useQuery({ queryKey: queryKeys.runs(filter), queryFn: ({ signal }) => api.runs(filter, signal) });
}

export function useDlq(runIds: readonly string[]) {
  return useQuery({
    queryKey: queryKeys.dlq(runIds),
    queryFn: ({ signal }) => api.dlq(runIds, signal),
    enabled: runIds.length > 0,
  });
}

export function useSourceStats(query: SourceStatsQuery | undefined) {
  return useQuery({
    queryKey: queryKeys.sourceStats(query ?? { table: "" }),
    queryFn: ({ signal }) => api.sourceStats(query!, signal),
    enabled: !!query?.table,
  });
}

export function useRagChunks(query: RagChunksQuery | undefined) {
  return useQuery({
    queryKey: queryKeys.ragChunks(query ?? { digest: "", kind: "row_doc", embedder: "" }),
    queryFn: ({ signal }) => api.ragChunks(query!, signal),
    enabled: !!query?.digest && !!query.embedder,
    staleTime: 30 * 60_000,
  });
}

export function usePools(query: FreetextPoolsQuery | undefined) {
  return useQuery({
    queryKey: queryKeys.pools(query ?? { digest: "" }),
    queryFn: ({ signal }) => api.pools(query!, signal),
    enabled: !!query?.digest,
  });
}

/** The same data as `@contracts/generated/knobs` (import that for static use; this is the served copy). */
export function useKnobs() {
  return useQuery({ queryKey: queryKeys.knobs, queryFn: ({ signal }) => api.knobs(signal), staleTime: Infinity });
}

export function useCatalogue() {
  return useQuery({
    queryKey: queryKeys.catalogue,
    queryFn: ({ signal }) => api.catalogue(signal),
    staleTime: Infinity,
  });
}
