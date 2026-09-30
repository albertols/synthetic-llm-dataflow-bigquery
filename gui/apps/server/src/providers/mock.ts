/**
 * `DATA_SOURCE=mock`: answers every provider call from `@synthetic-platform/mock`
 * (seeded, self-consistent, invented thelook-shaped data) with the same
 * shared logic the BigQuery provider uses. Nothing is fetched; nothing is written.
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
  RelationshipModel,
  RunFilterParsed,
  SourceStats,
  TrendPoint,
  TrendQueryParsed,
  ValidationRun,
} from "@synthetic-platform/contracts";
import { getMockDataset, MOCK_RELATIONSHIP_MODEL, type MockDataset } from "@synthetic-platform/mock";

import {
  assembleSourceStats,
  blockerFlags,
  buildComparison,
  buildFacets,
  latestPerEvaluation,
  matchesFilter,
  parseCounts,
  parseJsonText,
  resolveTable,
  sortDlq,
  sortEvaluations,
  toSummary,
  toTrendPoint,
  withinWindow,
} from "./shared";
import type { DataProvider, ProfileFilter, RagChunks, RagChunksFilter, SourceStatsFilter } from "./types";

/** Ascending with NULLs first: the reverse of BigQuery's `DESC` (NULLS LAST). */
const nullsFirst = (a: string | null, b: string | null) =>
  a === b ? 0 : a === null ? -1 : b === null ? 1 : a < b ? -1 : a > b ? 1 : 0;

/** `run_id` belongs to launch `base`: the one-table id itself, or a per-table `<base>-NN-<table>`. */
export function belongsToLaunch(runId: string, base: string): boolean {
  return runId === base || (runId.startsWith(`${base}-`) && /^\d{2}-.+$/.test(runId.slice(base.length + 1)));
}

type JsonValue = NonNullable<DlqSummary["sample"]>["raw_record"];

export class MockProvider implements DataProvider {
  readonly mode = "mock" as const;
  readonly project = null;
  readonly location = null;
  private data: MockDataset | null = null;

  constructor(private readonly load: () => MockDataset = getMockDataset) {}

  private get dataset(): MockDataset {
    this.data ??= this.load();
    return this.data;
  }

  ready(): Promise<void> {
    void this.dataset;
    return Promise.resolve();
  }

  relationshipModels(): Promise<RelationshipModel[]> {
    return Promise.resolve([MOCK_RELATIONSHIP_MODEL]);
  }

  private latest() {
    return latestPerEvaluation(this.dataset.registry);
  }

  listEvaluations(q: EvaluationFilterParsed): Promise<Page<EvaluationSummary>> {
    const rows = sortEvaluations(
      this.latest()
        .map(toSummary)
        .filter((r) => matchesFilter(r, q)),
      q.sort,
      q.order,
    );
    return Promise.resolve({
      items: rows.slice(q.offset, q.offset + q.limit),
      total: rows.length,
      offset: q.offset,
      limit: q.limit,
    });
  }

  getEvaluation(id: string, options: { profiles?: "all" | "none" } = {}): Promise<EvaluationDetail | null> {
    const events = this.dataset.registry
      .filter((r) => r.evaluation_id === id)
      .sort((a, b) => a.recorded_at.localeCompare(b.recorded_at));
    if (!events.length) return Promise.resolve(null);
    return Promise.resolve({
      evaluation: events.at(-1)!,
      events,
      metrics: this.dataset.metrics.filter((m) => m.evaluation_id === id),
      profiles: options.profiles === "none" ? [] : this.dataset.profiles.filter((p) => p.evaluation_id === id),
      flags: this.dataset.flags.filter((f) => f.evaluation_id === id),
    });
  }

  profiles(id: string, q: ProfileFilter): Promise<ProfileRow[] | null> {
    if (!this.dataset.registry.some((r) => r.evaluation_id === id)) return Promise.resolve(null);
    return Promise.resolve(
      this.dataset.profiles.filter(
        (p) =>
          p.evaluation_id === id &&
          (!q.table || p.table_name === q.table) &&
          (!q.column || p.column_name === q.column) &&
          (!q.kind?.length || q.kind.includes(p.profile_kind)) &&
          (!q.side?.length || q.side.includes(p.side)),
      ),
    );
  }

  metricTrend(q: TrendQueryParsed): Promise<TrendPoint[]> {
    const evaluations = new Map(
      this.latest()
        .map(toSummary)
        .filter((r) => matchesFilter(r, { ...q, sort: "evaluated_at", order: "asc", offset: 0 }))
        .map((r) => [r.evaluation_id, r]),
    );
    const points = this.dataset.metrics
      .filter(
        (m) =>
          m.metric_id === q.metric_id &&
          evaluations.has(m.evaluation_id) &&
          (!q.table || m.table_name === q.table) &&
          (!q.column || m.column_name === q.column) &&
          (!q.column_2 || m.column_name_2 === q.column_2) &&
          (!q.edge || m.edge === q.edge),
      )
      .filter((m) => m.evaluated_at === evaluations.get(m.evaluation_id)!.evaluated_at)
      .map((m) => toTrendPoint(m, evaluations.get(m.evaluation_id)!))
      // The SQL's newest-first order, reversed: oldest first, the newest `limit` kept.
      .sort(
        (a, b) =>
          nullsFirst(a.evaluated_at, b.evaluated_at) ||
          nullsFirst(a.evaluation_id, b.evaluation_id) ||
          nullsFirst(a.table_name, b.table_name) ||
          nullsFirst(a.column_name, b.column_name) ||
          nullsFirst(a.column_name_2, b.column_name_2) ||
          nullsFirst(a.edge, b.edge),
      );
    return Promise.resolve(points.slice(-q.limit));
  }

  compare(ids: string[]): Promise<Comparison> {
    const wanted = new Set(ids);
    const evaluations = this.latest().filter((r) => wanted.has(r.evaluation_id));
    const metrics = this.dataset.metrics.filter((m) => wanted.has(m.evaluation_id));
    return Promise.resolve(buildComparison(ids, evaluations, metrics));
  }

  runs(q: RunFilterParsed): Promise<ValidationRun[]> {
    const rows = this.dataset.validationRuns
      .filter(
        (r) =>
          (!q.run_ids?.length || q.run_ids.includes(r.run_id)) &&
          (!q.base_run_id || belongsToLaunch(r.run_id, q.base_run_id)) &&
          (!q.landing_table?.length || (r.landing_table !== null && q.landing_table.includes(r.landing_table))) &&
          (!q.engine?.length || (r.engine !== null && q.engine.includes(r.engine))) &&
          (!q.status?.length || (r.status !== null && q.status.includes(r.status))) &&
          (!q.env?.length || (r.env !== null && q.env.includes(r.env))) &&
          withinWindow(r.created_at, q.from, q.to),
      )
      .sort((a, b) => b.created_at.localeCompare(a.created_at) || a.run_id.localeCompare(b.run_id))
      .slice(0, q.limit);
    return Promise.resolve(rows.map((r) => ({ ...r, dlq_by_rule_map: parseCounts(r.dlq_by_rule) })));
  }

  dlqSummary(runIds: string[]): Promise<DlqSummary[]> {
    const out: DlqSummary[] = [];
    for (const run of this.dataset.validationRuns.filter((r) => runIds.includes(r.run_id))) {
      for (const [rule, count] of Object.entries(parseCounts(run.dlq_by_rule))) {
        const samples = this.dataset.dlq.filter((d) => d.run_id === run.run_id && d.rule_id === rule);
        const first = samples[0];
        const times = samples.map((d) => d.dlq_inserted_at).sort();
        out.push({
          run_id: run.run_id,
          rule_id: rule,
          error_type: first?.error_type ?? null,
          pipeline_step: first?.pipeline_step ?? null,
          stage: first?.stage ?? null,
          count,
          first_seen: times[0] ?? null,
          last_seen: times.at(-1) ?? null,
          sample: first
            ? {
                raw_record: parseJsonText(first.raw_record) as JsonValue,
                error_detail: parseJsonText(first.error_detail) as JsonValue,
              }
            : null,
          ...blockerFlags(rule),
        });
      }
    }
    return Promise.resolve(sortDlq(out));
  }

  sourceStats(q: SourceStatsFilter): Promise<SourceStats | null> {
    const fqns = [...new Set(this.dataset.sourceStats.map((r) => r.table_fqn))];
    const fqn = resolveTable(fqns, q.table);
    if (!fqn) return Promise.resolve(null);
    return Promise.resolve(
      assembleSourceStats(
        fqn,
        this.dataset.sourceStats.filter((r) => r.table_fqn === fqn),
        q,
      ),
    );
  }

  ragChunks(q: RagChunksFilter): Promise<RagChunks> {
    const [embedderId, version] = q.embedder.split("/");
    const sets = this.dataset.rag.filter(
      (s) =>
        s.reference_digest === q.digest &&
        s.embedder_id === embedderId &&
        (!version || s.embedder_version === version) &&
        (!q.source_fqn || s.source_fqn === q.source_fqn),
    );
    const meta: ChunkMeta[] = [];
    const parts: Float32Array[] = [];
    let dim = 384;
    for (const set of sets) {
      dim = set.dim;
      set.chunks.forEach((chunk, i) => {
        if (meta.length >= q.limit || chunk.chunk_kind !== q.kind) return;
        const column = typeof chunk.metadata?.column === "string" ? chunk.metadata.column : null;
        if (q.column && column !== q.column) return;
        meta.push({
          chunk_id: chunk.chunk_id,
          source_fqn: chunk.source_fqn,
          chunk_kind: chunk.chunk_kind,
          chunk_index: chunk.chunk_index,
          chunk_text: chunk.chunk_text,
          column,
          row_digest: chunk.row_digest,
          embedder_id: chunk.embedder_id,
          embedder_version: chunk.embedder_version,
        });
        parts.push(set.vectors.subarray(i * set.dim, (i + 1) * set.dim));
      });
    }
    const vectors = new Float32Array(meta.length * dim);
    parts.forEach((v, i) => vectors.set(v, i * dim));
    return Promise.resolve({ meta, dim, vectors });
  }

  freetextPools(q: {
    digest: string;
    modelUri?: string | undefined;
    column?: string | undefined;
  }): Promise<FreetextPool[]> {
    return Promise.resolve(
      this.dataset.pools
        .filter(
          (p) =>
            p.reference_digest === q.digest &&
            (!q.modelUri || p.model_uri === q.modelUri) &&
            (!q.column || p.column === q.column),
        )
        .map((p) => ({ ...p, distinct: p.values.length }))
        .sort((a, b) => a.model_uri.localeCompare(b.model_uri) || a.column.localeCompare(b.column)),
    );
  }

  facets(): Promise<Facets> {
    const d = this.dataset;
    const stats = new Map<string, { tiers: Set<string>; latest: string; columns: Set<string> }>();
    for (const r of d.sourceStats) {
      const s = stats.get(r.table_fqn) ?? {
        tiers: new Set<string>(),
        latest: r.computed_at,
        columns: new Set<string>(),
      };
      s.tiers.add(r.stats_tier ?? "sample"); // as sourceStats.tables: NULL tier = sample
      if (r.computed_at > s.latest) s.latest = r.computed_at;
      s.columns.add(r.column);
      stats.set(r.table_fqn, s);
    }
    // As rag.poolSets: the digest's source table from validation_runs, else source_table_stats.
    const digestTable = new Map<string, string>();
    for (const r of [...d.sourceStats].sort((a, b) => b.table_fqn.localeCompare(a.table_fqn)))
      digestTable.set(r.reference_digest, r.table_fqn);
    for (const r of [...d.validationRuns].sort((a, b) =>
      (b.reference_table ?? "").localeCompare(a.reference_table ?? ""),
    ))
      if (r.reference_digest && r.reference_table) digestTable.set(r.reference_digest, r.reference_table);
    const pools = new Map<string, Facets["pools"][number]>();
    for (const p of d.pools) {
      const key = `${p.reference_digest}|${p.model_uri}`;
      const entry = pools.get(key) ?? {
        reference_digest: p.reference_digest,
        table_fqn: digestTable.get(p.reference_digest) ?? null,
        model_uri: p.model_uri,
        columns: [] as string[],
      };
      entry.columns.push(p.column);
      pools.set(key, entry);
    }
    return Promise.resolve(
      buildFacets({
        latest: this.latest().map(toSummary),
        runs: d.validationRuns.length,
        metrics: d.metrics.length,
        metricIds: [...new Set(d.metrics.map((m) => m.metric_id))],
        sourceTables: [...stats.entries()]
          .map(([table_fqn, s]) => ({
            table_fqn,
            tiers: [...s.tiers].sort(),
            latest_computed_at: s.latest,
            columns: s.columns.size,
          }))
          .sort((a, b) => a.table_fqn.localeCompare(b.table_fqn)),
        rag: d.rag.map((s) => ({
          source_fqn: s.source_fqn,
          reference_digest: s.reference_digest,
          embedder_id: s.embedder_id,
          embedder_version: s.embedder_version,
          row_docs: s.chunks.filter((c) => c.chunk_kind === "row_doc").length,
          value_chunks: s.chunks.filter((c) => c.chunk_kind === "free_text_col").length,
          columns: [
            ...new Set(
              s.chunks
                .map((c) => (typeof c.metadata?.column === "string" ? c.metadata.column : null))
                .filter((c): c is string => !!c),
            ),
          ].sort(),
          dim: s.dim,
          created_at: s.created_at,
        })),
        pools: [...pools.values()].map((p) => ({ ...p, columns: p.columns.sort() })),
      }),
    );
  }
}
