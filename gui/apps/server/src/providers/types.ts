/**
 * The one data interface behind every route. `MockProvider` and
 * `BigQueryProvider` implement it identically; routes never know which one
 * runs. Inputs are the parsed (zod) query shapes from the contracts package.
 */
import type {
  ChunkMeta,
  Comparison,
  DlqSummary,
  EvaluationDetail,
  EvaluationFilterParsed,
  EvaluationSummary,
  Facets,
  FreetextPool,
  Page,
  ProfileRow,
  RunFilterParsed,
  SourceStats,
  TrendPoint,
  TrendQueryParsed,
  ValidationRun,
} from "@synthetic-platform/contracts";

/** Per-request accounting: the BigQuery bytes the route's queries would scan (dry run), and cache use. */
export interface QueryContext {
  bytesEstimate: number;
  queries: number;
  cacheHits: number;
}

export const newContext = (): QueryContext => ({ bytesEstimate: 0, queries: 0, cacheHits: 0 });

export interface ProfileFilter {
  table?: string | undefined;
  column?: string | undefined;
  kind?: string[] | undefined;
  side?: string[] | undefined;
}

export interface RagChunksFilter {
  digest: string;
  kind: "row_doc" | "free_text_col";
  /** embedder_id, or "<id>/<version>". */
  embedder: string;
  limit: number;
  source_fqn?: string | undefined;
  column?: string | undefined;
}

export interface RagChunks {
  meta: ChunkMeta[];
  dim: number;
  /** meta.length × dim, row-major Float32. */
  vectors: Float32Array;
}

export interface DataProvider {
  readonly mode: "mock" | "bigquery";
  /** GCP project (bigquery mode) or null. */
  readonly project: string | null;
  readonly location: string | null;
  /** Resolves when the provider can answer quickly (the mock builds its dataset). */
  ready(): Promise<void>;
  listEvaluations(q: EvaluationFilterParsed, ctx?: QueryContext): Promise<Page<EvaluationSummary>>;
  /** Registry (latest + every event) + metrics + profiles + flags; null when the id does not exist. */
  getEvaluation(
    id: string,
    options?: { profiles?: "all" | "none" },
    ctx?: QueryContext,
  ): Promise<EvaluationDetail | null>;
  profiles(id: string, q: ProfileFilter, ctx?: QueryContext): Promise<ProfileRow[] | null>;
  metricTrend(q: TrendQueryParsed, ctx?: QueryContext): Promise<TrendPoint[]>;
  compare(ids: string[], ctx?: QueryContext): Promise<Comparison>;
  runs(q: RunFilterParsed, ctx?: QueryContext): Promise<ValidationRun[]>;
  dlqSummary(runIds: string[], ctx?: QueryContext): Promise<DlqSummary[]>;
  /** null when no stats exist for the table. */
  sourceStats(
    q: { table: string; tier?: "sample" | "exact" | undefined; digest?: string | undefined },
    ctx?: QueryContext,
  ): Promise<SourceStats | null>;
  ragChunks(q: RagChunksFilter, ctx?: QueryContext): Promise<RagChunks>;
  freetextPools(
    q: { digest: string; modelUri?: string | undefined; column?: string | undefined },
    ctx?: QueryContext,
  ): Promise<FreetextPool[]>;
  facets(ctx?: QueryContext): Promise<Facets>;
}

/** A named query failed its row contract: the live table no longer matches the generated zod. */
export class ContractError extends Error {
  constructor(
    readonly query: string,
    readonly details: string[],
  ) {
    super(`BigQuery rows from "${query}" do not match the generated contract`);
    this.name = "ContractError";
  }
}

/** A dry run estimated more bytes than MAX_BYTES_BILLED allows. */
export class BytesCapError extends Error {
  constructor(
    readonly query: string,
    readonly estimate: number,
    readonly cap: number,
  ) {
    super(`query "${query}" would process ${estimate} bytes, above MAX_BYTES_BILLED (${cap})`);
    this.name = "BytesCapError";
  }
}
