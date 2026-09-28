/**
 * `DATA_SOURCE=bigquery`: every call runs named queries from queries/registry.ts
 * against `synthetic_data_quality.*` and `synthetic_rag.*`.
 *
 * Per query: parameters only (never client text in SQL), a dry run first (its
 * `totalBytesProcessed` is added to the request's `x-bq-bytes-estimate` and
 * refused above MAX_BYTES_BILLED), then the job with `maximumBytesBilled` set
 * as well. Rows are normalized to the wire format (TIMESTAMP → ISO string,
 * INT64 → number, JSON → parsed) and validated against the generated zod; a
 * mismatch is a ContractError (HTTP 502), never a silent pass. Results are
 * cached in memory (LRU, TTL) and never persisted. The runner's ADC stays in
 * this process.
 */
import {
  chunkMetaSchema,
  evaluationDataHistoryRowSchema,
  evaluationMetricsRowSchema,
  evaluationProfilesRowSchema,
  evaluationRowFlagsRowSchema,
  evaluationSummarySchema,
  freetextPoolsRowSchema,
  ragChunksRowSchema,
  sourceTableStatsRowSchema,
  trendPointSchema,
  validationRunsRowSchema,
  type ChunkMeta,
  type Comparison,
  type DlqSummary,
  type EvaluationDetail,
  type EvaluationFilterParsed,
  type EvaluationRecord,
  type EvaluationSummary,
  type Facets,
  type FreetextPool,
  type MetricRow,
  type Page,
  type ProfileRow,
  type RagSet,
  type RunFilterParsed,
  type SourceStats,
  type SourceTableStatsRow,
  type TrendPoint,
  type TrendQueryParsed,
  type ValidationRun,
  type ValidationRunsRow,
} from "@synthetic-platform/contracts";
import { LRUCache } from "lru-cache";
import { z } from "zod";

import type { ServerConfig } from "../config";
import { normalizeRows } from "../normalize";
import {
  assertReadOnly,
  QUERIES,
  type Datasets,
  type NamedQuery,
  type QueryName,
  type SortKey,
} from "../queries/registry";
import {
  assembleSourceStats,
  blockerFlags,
  buildComparison,
  buildFacets,
  parseCounts,
  parseJsonText,
  resolveTable,
  sortDlq,
  toSummary,
} from "./shared";
import {
  BytesCapError,
  ContractError,
  newContext,
  type DataProvider,
  type ProfileFilter,
  type QueryContext,
  type RagChunks,
  type RagChunksFilter,
} from "./types";

/** The slice of `@google-cloud/bigquery` the provider uses (a stub implements it in tests). */
export interface BigQueryJob {
  metadata?: { statistics?: { totalBytesProcessed?: string | number | null } | null } | null;
  getQueryResults(options?: Record<string, unknown>): Promise<unknown[]>;
}
export interface BigQueryClient {
  createQueryJob(options: Record<string, unknown>): Promise<unknown[]>;
}

type Params = Record<string, unknown>;

interface Cached {
  rows: Record<string, unknown>[];
  bytes: number;
}

const summaryPage = z.array(evaluationSummarySchema.extend({ total_rows: z.int().nonnegative() }));
const dlqRows = z.array(
  z.object({
    run_id: z.string(),
    rule_id: z.string().nullable(),
    error_type: z.string().nullable(),
    pipeline_step: z.string().nullable(),
    stage: z.string().nullable(),
    count: z.int().nonnegative(),
    first_seen: z.iso.datetime({ offset: true }).nullable(),
    last_seen: z.iso.datetime({ offset: true }).nullable(),
    raw_record: z.string().nullable(),
    error_detail: z.string().nullable(),
  }),
);

export class BigQueryProvider implements DataProvider {
  readonly mode = "bigquery" as const;
  readonly project: string;
  readonly location: string;
  private readonly datasets: Datasets;
  private readonly cache: LRUCache<string, Cached>;
  private clientPromise: Promise<BigQueryClient> | null = null;

  constructor(
    private readonly config: ServerConfig,
    private readonly clientFactory: () => Promise<BigQueryClient> = async () => {
      const { BigQuery } = await import("@google-cloud/bigquery");
      const client: BigQueryClient = new BigQuery({ projectId: config.GCP_PROJECT, location: config.BQ_LOCATION });
      return client;
    },
  ) {
    this.project = config.GCP_PROJECT!;
    this.location = config.BQ_LOCATION;
    this.datasets = {
      quality: `${this.project}.${config.QUALITY_DATASET}`,
      rag: `${this.project}.${config.RAG_DATASET}`,
    };
    this.cache = new LRUCache<string, Cached>({ max: config.CACHE_MAX_ENTRIES, ttl: config.CACHE_TTL_SECONDS * 1000 });
  }

  ready(): Promise<void> {
    return Promise.resolve();
  }

  private client(): Promise<BigQueryClient> {
    this.clientPromise ??= this.clientFactory();
    return this.clientPromise;
  }

  /** Runs one named query: dry run → cap check → job with maximumBytesBilled → normalize → validate. */
  async run<T>(
    name: QueryName,
    params: Params,
    schema: z.ZodType<T[]>,
    ctx: QueryContext,
    options: { sort?: SortKey; order?: "asc" | "desc" } = {},
  ): Promise<T[]> {
    const query: NamedQuery = QUERIES[name];
    const sql = query.sql(this.datasets, options);
    assertReadOnly(sql);
    for (const key of Object.keys(params))
      if (!(key in query.types)) throw new Error(`query "${name}" has no parameter "${key}"`);
    const bound: Params = {};
    for (const key of Object.keys(query.types))
      bound[key] = params[key] ?? (Array.isArray(query.types[key]) ? [] : null);
    const cacheKey = `${name}|${sql.length}|${JSON.stringify(bound)}`;
    ctx.queries += 1;
    let hit = this.cache.get(cacheKey);
    if (hit) ctx.cacheHits += 1;
    else {
      const client = await this.client();
      const base = {
        query: sql,
        params: bound,
        types: query.types,
        location: this.location,
        useLegacySql: false,
        maximumBytesBilled: String(this.config.MAX_BYTES_BILLED),
        labels: { app: "synthetic-platform", query: name.replace(/[^a-z0-9_-]/gi, "_").toLowerCase() },
      };
      const [dryJob] = (await client.createQueryJob({ ...base, dryRun: true })) as [BigQueryJob];
      const estimate = Number(dryJob.metadata?.statistics?.totalBytesProcessed ?? 0);
      if (estimate > this.config.MAX_BYTES_BILLED)
        throw new BytesCapError(name, estimate, this.config.MAX_BYTES_BILLED);
      const [job] = (await client.createQueryJob({ ...base, jobTimeoutMs: 60_000 })) as [BigQueryJob];
      const [raw] = (await job.getQueryResults({ wrapIntegers: false, parseJSON: true })) as [
        Record<string, unknown>[],
      ];
      hit = { rows: normalizeRows(raw ?? [], query.fields), bytes: estimate };
      this.cache.set(cacheKey, hit);
      ctx.bytesEstimate += estimate;
    }
    const parsed = schema.safeParse(hit.rows);
    if (!parsed.success)
      throw new ContractError(
        name,
        parsed.error.issues.slice(0, 10).map((i) => `${i.path.join(".")}: ${i.message}`),
      );
    return parsed.data;
  }

  async listEvaluations(q: EvaluationFilterParsed, ctx = newContext()): Promise<Page<EvaluationSummary>> {
    const { sort, order, offset, limit, ...filters } = q;
    const rows = await this.run("evaluations.list", { ...filters, limit, offset }, summaryPage, ctx, { sort, order });
    return {
      items: rows.map(({ total_rows: _t, ...row }) => row),
      total: rows[0]?.total_rows ?? 0,
      offset,
      limit,
    };
  }

  async getEvaluation(
    id: string,
    options: { profiles?: "all" | "none" } = {},
    ctx = newContext(),
  ): Promise<EvaluationDetail | null> {
    const events = await this.run("evaluations.events", { id }, z.array(evaluationDataHistoryRowSchema), ctx);
    if (!events.length) return null;
    const evaluation = events.at(-1)!;
    const scope = { id, evaluated_at: evaluation.evaluated_at };
    const [metrics, profiles, flags] = await Promise.all([
      this.run("metrics.byEvaluation", scope, z.array(evaluationMetricsRowSchema), ctx),
      options.profiles === "none"
        ? Promise.resolve([] as ProfileRow[])
        : this.run("profiles.byEvaluation", scope, z.array(evaluationProfilesRowSchema), ctx),
      this.run("flags.byEvaluation", scope, z.array(evaluationRowFlagsRowSchema), ctx),
    ]);
    return { evaluation, events, metrics, profiles, flags };
  }

  async profiles(id: string, q: ProfileFilter, ctx = newContext()): Promise<ProfileRow[] | null> {
    const events = await this.run("evaluations.events", { id }, z.array(evaluationDataHistoryRowSchema), ctx);
    if (!events.length) return null;
    return this.run(
      "profiles.byEvaluation",
      { id, evaluated_at: events.at(-1)!.evaluated_at, table: q.table, column: q.column, kinds: q.kind, sides: q.side },
      z.array(evaluationProfilesRowSchema),
      ctx,
    );
  }

  metricTrend(q: TrendQueryParsed, ctx = newContext()): Promise<TrendPoint[]> {
    return this.run("trend", { ...q }, z.array(trendPointSchema), ctx);
  }

  async compare(ids: string[], ctx = newContext()): Promise<Comparison> {
    const evaluations: EvaluationRecord[] = await this.run(
      "evaluations.latestByIds",
      { ids },
      z.array(evaluationDataHistoryRowSchema),
      ctx,
    );
    const metrics: MetricRow[] = evaluations.length
      ? await this.run(
          "metrics.byEvaluations",
          {
            ids: evaluations.map((e) => e.evaluation_id),
            evaluated_at: [...new Set(evaluations.map((e) => e.evaluated_at))],
          },
          z.array(evaluationMetricsRowSchema),
          ctx,
        )
      : [];
    return buildComparison(ids, evaluations, metrics);
  }

  async runs(q: RunFilterParsed, ctx = newContext()): Promise<ValidationRun[]> {
    const rows: ValidationRunsRow[] = await this.run("runs.list", { ...q }, z.array(validationRunsRowSchema), ctx);
    return rows.map((r) => ({ ...r, dlq_by_rule_map: parseCounts(r.dlq_by_rule) }));
  }

  async dlqSummary(runIds: string[], ctx = newContext()): Promise<DlqSummary[]> {
    const rows = await this.run("dlq.summary", { run_ids: runIds }, dlqRows, ctx);
    return sortDlq(
      rows.map(({ raw_record, error_detail, ...r }) => ({
        ...r,
        sample: { raw_record: parseJsonText(raw_record) as never, error_detail: parseJsonText(error_detail) as never },
        ...blockerFlags(r.rule_id),
      })),
    );
  }

  async sourceStats(
    q: { table: string; tier?: "sample" | "exact" | undefined; digest?: string | undefined },
    ctx = newContext(),
  ): Promise<SourceStats | null> {
    const tables = await this.run("sourceStats.tables", {}, z.array(z.object({ table_fqn: z.string() }).loose()), ctx);
    const fqn = resolveTable(
      tables.map((t) => t.table_fqn),
      q.table,
    );
    if (!fqn) return null;
    const rows: SourceTableStatsRow[] = await this.run(
      "sourceStats.byTable",
      { table_fqn: fqn },
      z.array(sourceTableStatsRowSchema),
      ctx,
    );
    return assembleSourceStats(fqn, rows, q);
  }

  async ragChunks(q: RagChunksFilter, ctx = newContext()): Promise<RagChunks> {
    const [embedder, version] = q.embedder.split("/");
    const rows = await this.run(
      "rag.chunks",
      { digest: q.digest, kind: q.kind, embedder, version, source_fqn: q.source_fqn, column: q.column, limit: q.limit },
      z.array(ragChunksRowSchema),
      ctx,
    );
    const dim = rows[0]?.embedding.length ?? 0;
    const vectors = new Float32Array(rows.length * dim);
    const meta: ChunkMeta[] = rows.map((r, i) => {
      if (r.embedding.length !== dim)
        throw new ContractError("rag.chunks", [`chunk ${r.chunk_id}: ${r.embedding.length} dims, expected ${dim}`]);
      vectors.set(r.embedding, i * dim);
      const column =
        r.metadata && typeof r.metadata === "object" && !Array.isArray(r.metadata)
          ? (r.metadata as Record<string, unknown>).column
          : null;
      return chunkMetaSchema.parse({
        chunk_id: r.chunk_id,
        source_fqn: r.source_fqn,
        chunk_kind: r.chunk_kind,
        chunk_index: r.chunk_index,
        chunk_text: r.chunk_text,
        column: typeof column === "string" ? column : null,
        row_digest: r.row_digest,
        source_pk: r.source_pk,
        embedder_id: r.embedder_id,
        embedder_version: r.embedder_version,
      });
    });
    return { meta, dim: dim || 384, vectors };
  }

  async freetextPools(
    q: { digest: string; modelUri?: string | undefined; column?: string | undefined },
    ctx = newContext(),
  ): Promise<FreetextPool[]> {
    const rows = await this.run(
      "rag.pools",
      { digest: q.digest, model_uri: q.modelUri, column: q.column },
      z.array(freetextPoolsRowSchema),
      ctx,
    );
    return rows.map((r) => ({ ...r, distinct: r.values.length }));
  }

  async facets(ctx = newContext()): Promise<Facets> {
    const [latest, runs, metricIds, sourceTables, rag, pools] = await Promise.all([
      this.run("evaluations.slim", {}, z.array(evaluationSummarySchema), ctx),
      this.run("runs.count", {}, z.array(z.object({ runs: z.int() })), ctx),
      this.run("metrics.ids", {}, z.array(z.object({ metric_id: z.string(), rows: z.int().nullable() })), ctx),
      this.run(
        "sourceStats.tables",
        {},
        z.array(
          z.object({
            table_fqn: z.string(),
            tiers: z.array(z.string()),
            latest_computed_at: z.iso.datetime({ offset: true }),
            columns: z.int(),
          }),
        ),
        ctx,
      ),
      this.run(
        "rag.sets",
        {},
        z.array(
          z.object({
            source_fqn: z.string(),
            reference_digest: z.string(),
            embedder_id: z.string(),
            embedder_version: z.string(),
            row_docs: z.int(),
            value_chunks: z.int(),
            columns: z.array(z.string()),
            created_at: z.iso.datetime({ offset: true }).nullable(),
          }),
        ),
        ctx,
      ),
      this.run(
        "rag.poolSets",
        {},
        z.array(z.object({ reference_digest: z.string(), model_uri: z.string(), columns: z.array(z.string()) })),
        ctx,
      ),
    ]);
    return buildFacets({
      latest: latest.map((r) => toSummary({ ...r, generation_params: null, evaluation_params: null })),
      runs: runs[0]?.runs ?? 0,
      metrics: metricIds.reduce((acc, m) => acc + (m.rows ?? 0), 0),
      metricIds: metricIds.map((m) => m.metric_id),
      sourceTables: sourceTables
        .map((t) => ({ ...t, tiers: [...t.tiers].sort() }))
        .sort((a, b) => a.table_fqn.localeCompare(b.table_fqn)),
      rag: rag.map((s): RagSet => ({ ...s, dim: null })),
      pools,
    });
  }
}
