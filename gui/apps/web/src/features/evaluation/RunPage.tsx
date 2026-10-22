/**
 * The run view, /evaluation/$evaluationId: one evaluation read top-down —
 * header (status, scope, reference, sampled mode), scorecards per family
 * with level chips, the interpretation, the model graph, then one section
 * per question (columns, pairs, privacy, detection, relational, parameters)
 * in URL-driven tabs. Only the active section renders; column profiles load
 * when a drawer opens. The noise-floor legend is docked to the bottom.
 */
import { getRouteApi, Link } from "@tanstack/react-router";
import { ArrowLeft, GitCompareArrows, SearchX } from "lucide-react";
import { useCallback, useMemo } from "react";

import { isEvaluatorJobName } from "@contracts/adjustments";
import type { EvaluationDetail } from "@contracts/api";
import { inapplicableKey } from "@contracts/applicability";

import { Callout } from "@/components/Callout";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { SourceLink } from "@/components/SourceLink";
import { StatTile } from "@/components/StatTile";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ApiError,
  useEvaluation,
  useEvaluationAdjustments,
  useEvaluationRecord,
  useEvaluations,
  useRelationships,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCompact, formatCount, formatDateTime } from "@/lib/format";
import { CLOSE_BY_HAND, isStale, STALE_AFTER_HOURS } from "@/lib/runState";

import { NoiseLegend } from "./components/NoiseLegend";
import { StatusCounts } from "./components/StatusCounts";
import { adjustedSummary, adjustedTableCount, adjustmentRows, PK_METRIC } from "./lib/adjustment";
import { isRollup } from "./lib/catalogue";
import { fmtScore, shortModel } from "./lib/format";
import { buildGraph, WIDE_GRAPH_MIN_TILES } from "./lib/graph";
import { findings, headline } from "./lib/interpret";
import { columnSummaries, countStatuses, familyCards, heatmapModel, tableNames } from "./lib/model";
import { toleranceOf } from "./lib/record";
import { tableFacts } from "./lib/tables";
import { ColumnDrawer } from "./panels/ColumnDrawer";
import { ColumnHeatmap, HEATMAP_PAGE } from "./panels/ColumnHeatmap";
import { DetectionPanel } from "./panels/DetectionPanel";
import { InterpretationPanel } from "./panels/InterpretationPanel";
import { GeneratorPanel } from "./panels/GeneratorPanel";
import { ModelGraph } from "./panels/ModelGraph";
import { PairsPanel } from "./panels/PairsPanel";
import { ParamsPanel } from "./panels/ParamsPanel";
import { PrivacyPanel } from "./panels/PrivacyPanel";
import { RelationalPanel } from "./panels/RelationalPanel";
import { RunHeader } from "./panels/RunHeader";
import { Scorecards, type OpenTarget } from "./panels/Scorecards";
import type { EvaluationRunSearch, RunTabId } from "./route";
import { RelatedKnobsProvider } from "./components/RelatedKnobs";

const route = getRouteApi("/evaluation/$evaluationId");

const TAB_LABEL: Record<RunTabId, string> = {
  overview: "Overview",
  columns: "Columns",
  pairs: "Pairs",
  privacy: "Privacy",
  detection: "Detection",
  relational: "Relational",
  params: "Parameters",
};

const ALL = "__all__";

function Loading({ id }: { id: string }) {
  return (
    <div className="flex flex-col gap-6" role="status" aria-busy="true">
      <PageHeader eyebrow="Evaluation run" title={id} description="Loading the registry row and its metrics…" />
      <Skeleton className="h-40 w-full" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-64" />
        ))}
      </div>
    </div>
  );
}

function usePrevious(detail: EvaluationDetail | undefined) {
  const evaluation = detail?.evaluation;
  const previous = useEvaluations(
    evaluation
      ? {
          to: evaluation.evaluated_at,
          relationship_model: evaluation.relationship_model ? [evaluation.relationship_model] : undefined,
          limit: 1,
          sort: "evaluated_at",
          order: "desc",
        }
      : { limit: 1 },
  );
  if (!evaluation) return undefined;
  const item = previous.data?.data.items[0];
  return item && item.evaluation_id !== evaluation.evaluation_id && item.evaluated_at < evaluation.evaluated_at
    ? item
    : undefined;
}

/** The page, with its metric (i) popovers linking to the CONFIG knobs that drive them. */
export function EvaluationRunPage() {
  return (
    <RelatedKnobsProvider>
      <RunPageContent />
    </RelatedKnobsProvider>
  );
}

function RunPageContent() {
  const { evaluationId } = route.useParams();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  // Ask the registry first: an unknown id then reads "not found" without a 404 in the console,
  // and the (much larger) detail request only goes out for an id that exists.
  const lookup = useEvaluations({ q: evaluationId.slice(0, 200), limit: 50 });
  const exists = lookup.data ? lookup.data.data.items.some((e) => e.evaluation_id === evaluationId) : undefined;
  const knownId = exists === false || lookup.isPending ? undefined : evaluationId;
  const query = useEvaluation(knownId);
  // ADR 0038: the generator's own verdict on each table's declared key, from validation_runs. Its
  // own request, so a failure there never blanks the evaluation (the Relational section says so).
  const adjustmentsQuery = useEvaluationAdjustments(knownId);
  // ADR 0044: the generation record, also its own request. Its tolerance is the band of the relational readings.
  const recordQuery = useEvaluationRecord(knownId);
  const relationships = useRelationships();
  const detail = query.data?.data;
  const adjustments = adjustmentsQuery.data?.data;
  const record = recordQuery.data?.data;
  const tolerance = toleranceOf(record);
  const previous = usePrevious(detail);

  const setSearch = useCallback(
    (patch: Partial<EvaluationRunSearch>, replace = false) => {
      void navigate({ search: (prev) => ({ ...prev, ...patch }), replace, resetScroll: false });
    },
    [navigate],
  );

  const derived = useMemo(() => {
    if (!detail) return null;
    const model = relationships.data?.data.models.find((m) => m.model === detail.evaluation.relationship_model) ?? null;
    const summaries = columnSummaries(detail.metrics);
    // A re-reading layered on the evaluator's rows: the cards, the headline and the counts below
    // are computed from the rows as stored, whatever the adjustments say.
    const adjusted = adjustmentRows(detail, adjustments?.model_adjustments, tolerance);
    const graph = buildGraph(detail, model, adjusted, record?.declared_tables);
    const facts = tableFacts({ detail, graph, record });
    return {
      model,
      summaries,
      adjusted,
      adjustedSummary: adjustedSummary(adjusted),
      adjustedTables: adjustedTableCount(adjustments?.model_adjustments),
      cards: familyCards(detail),
      findings: findings(detail, adjustments?.model_adjustments, tolerance),
      headline: headline(detail),
      graph,
      facts,
      factsByName: new Map(facts.map((f) => [f.name, f])),
      tables: tableNames(detail.evaluation, detail.metrics),
      counts: countStatuses(detail.metrics.filter((m) => !isRollup(m.metric_id))),
    };
  }, [detail, adjustments, tolerance, relationships.data, record]);

  const table = search.table && derived?.tables.includes(search.table) ? search.table : undefined;
  const scopedMetrics = useMemo(
    () =>
      detail
        ? table
          ? detail.metrics.filter((m) => m.table_name === table || m.level === "model")
          : detail.metrics
        : [],
    [detail, table],
  );
  const heatmap = useMemo(
    () =>
      derived
        ? heatmapModel(derived.summaries, {
            table,
            family: search.family,
            problemsOnly: search.problems ?? false,
            query: search.q,
          })
        : null,
    [derived, table, search.family, search.problems, search.q],
  );

  const onOpen = useCallback(
    (target: OpenTarget) => setSearch({ tab: target.tab, column: target.column, table: target.table ?? table }),
    [setSearch, table],
  );
  const openColumn = useCallback((key: string) => setSearch({ column: key }), [setSearch]);

  const notFound = exists === false || (query.error instanceof ApiError && query.error.status === 404);
  // Wait for the adjustments too (they settle, failed or not): a FAIL shown first and re-read a
  // moment later would be a reading the page is about to retract.
  if (!notFound && (lookup.isPending || query.isPending || adjustmentsQuery.isLoading))
    return <Loading id={evaluationId} />;
  if (notFound || query.error || !detail || !derived || !heatmap) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Evaluation run" title={evaluationId} />
        <EmptyState
          icon={SearchX}
          title={notFound ? "No evaluation with this id" : "The evaluation could not be loaded"}
          description={
            notFound ? "Check the id, or pick one from the list." : String(query.error?.message ?? "Unknown error")
          }
          action={
            <Button asChild variant="secondary">
              <Link to="/evaluation">
                <ArrowLeft aria-hidden="true" />
                All evaluations
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  const { evaluation } = detail;
  const tab: RunTabId = search.tab ?? "overview";
  const drawerSummary = search.column ? derived.summaries.find((s) => s.key === search.column) : undefined;
  const hasMetrics = detail.metrics.length > 0;
  // An old RUNNING event: the launch or the job most likely ended without a FINAL row (lib/runState.ts).
  const stale = isStale(evaluation);
  // Beside a forest of isolated tables the graph takes the full row: the tile grid needs the width.
  const wideGraph = derived.graph.tiles.length >= WIDE_GRAPH_MIN_TILES;
  const running = evaluation.status === "RUNNING" && !stale;
  // No metric row in the whole evaluation (not the table scope): "yet" only while its last event is a live RUNNING.
  const noMetrics = hasMetrics ? undefined : evaluation.event === "RUNNING" && !stale ? "yet" : "at-all";
  const notApplicable = detail.metrics.filter((m) => inapplicableKey(m) !== null).length;
  const rowsSynthetic = evaluation.tables.reduce((acc, t) => acc + (t.rows_synthetic ?? 0), 0);
  // The registry row names an evaluator job as its generation job: by that job's name, or because
  // another evaluation ran as it. Scope and adjustments are then joined to the wrong job.
  const evaluatorJob = adjustments?.generation_job_evaluation_id ?? null;
  const wrongJob = isEvaluatorJobName(evaluation.generation_job_name) || evaluatorJob !== null;
  const adjustmentsError = adjustmentsQuery.error ? String(adjustmentsQuery.error.message) : null;
  // Without the generator's half a PK-duplicate FAIL reads as a defect: say that it is missing.
  const unreadFails =
    adjustmentsError && detail.metrics.some((m) => m.metric_id === PK_METRIC && m.status === "fail")
      ? "The launch's model adjustments (ADR 0038) could not be loaded, so every PK-duplicate FAIL below reads as the evaluator stored it."
      : null;
  const bytes = [query.data?.bytesEstimate, adjustmentsQuery.data?.bytesEstimate].filter(
    (b): b is number => b !== null && b !== undefined,
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Evaluation run"
        title={evaluation.evaluation_id}
        concept="eval:evaluation"
        description={
          <span>
            {evaluation.engine ?? "engine ?"} · {shortModel(evaluation.llm_model_uri)} ·{" "}
            {evaluation.relationship_model ?? "no relationship model"} ({derived.tables.length} table
            {derived.tables.length === 1 ? "" : "s"}) · {formatDateTime(evaluation.evaluated_at)}
          </span>
        }
        actions={
          <>
            <Button asChild variant="ghost" size="sm">
              <Link to="/evaluation">
                <ArrowLeft aria-hidden="true" />
                All evaluations
              </Link>
            </Button>
            {previous ? (
              <Button asChild variant="secondary" size="sm">
                <Link to="/evaluation/compare" search={{ ids: [previous.evaluation_id, evaluation.evaluation_id] }}>
                  <GitCompareArrows aria-hidden="true" />
                  Compare with {previous.evaluation_id}
                </Link>
              </Button>
            ) : null}
          </>
        }
      />

      <RunHeader evaluation={evaluation} contractWarnings={query.data?.warnings ?? []} />

      {/* Under the header, so the reason and the link are the first thing on every section and deep link. */}
      {stale ? (
        <Callout tone="warn" title={`Stale: no result recorded for more than ${STALE_AFTER_HOURS} hours`}>
          The registry has had a RUNNING event since {formatDateTime(evaluation.recorded_at)} and no FINAL event. The
          launch or the job most likely ended without recording a result (the tables an evaluation prepares expire{" "}
          {STALE_AFTER_HOURS} hours after it creates them).
          {hasMetrics ? " The metrics on this page are what was written so far: they may be incomplete." : ""} The row
          stays as it is until someone closes it. Before closing it, check that no Dataflow job is still running for it:
          see <SourceLink path={`${CLOSE_BY_HAND.doc}#${CLOSE_BY_HAND.anchor}`}>{CLOSE_BY_HAND.title}</SourceLink> in
          the run playbook.
        </Callout>
      ) : null}

      {wrongJob ? (
        <Callout tone="danger" title="Wrong generation job: this evaluation points at an evaluator job">
          This evaluation points at an evaluator job, not a generation launch; scope and adjustments cannot be joined.
          The registry row names{" "}
          <code className="font-mono [overflow-wrap:anywhere] text-text-1">
            {evaluation.generation_job_name ?? evaluation.generation_job_id}
          </code>{" "}
          as its generation job
          {evaluatorJob ? (
            <>
              , which is the Dataflow job of evaluation{" "}
              <Link
                to="/evaluation/$evaluationId"
                params={{ evaluationId: evaluatorJob }}
                // Underlined: a link inside a sentence is never told apart by colour alone (WCAG 1.4.1).
                className="text-link underline underline-offset-2"
              >
                {evaluatorJob}
              </Link>
            </>
          ) : null}
          . The metrics below describe the landing tables as the evaluator found them; evaluate the launch again with
          its own job id to join its window, its run ids and its model adjustments.
        </Callout>
      ) : null}

      <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Scope">
        <label htmlFor="run-table-scope" className="text-sm text-text-2">
          Table
        </label>
        <Select value={table ?? ALL} onValueChange={(value) => setSearch({ table: value === ALL ? undefined : value })}>
          <SelectTrigger id="run-table-scope" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All tables</SelectItem>
            {derived.tables.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-text-3">
          {table
            ? `Sections below show ${table} only (model roll-ups stay model-wide).`
            : "Pick a table here or in the model graph."}
        </span>
      </div>

      <Tabs
        value={tab}
        onValueChange={(value) => setSearch({ tab: value as RunTabId })}
        className="flex flex-col gap-5"
      >
        <TabsList aria-label="Evaluation sections" className="self-start">
          {(Object.keys(TAB_LABEL) as RunTabId[]).map((id) => (
            <TabsTrigger key={id} value={id}>
              {TAB_LABEL[id]}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-6">
          {hasMetrics ? (
            <>
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <StatTile
                  label="Overall score"
                  value={derived.cards[0]?.score ?? null}
                  format={(v) => fmtScore(v)}
                  concept="core:score"
                  delta={
                    previous?.overall_score !== null &&
                    previous?.overall_score !== undefined &&
                    derived.cards[0]?.score != null
                      ? {
                          value: derived.cards[0].score - previous.overall_score,
                          vs: previous.evaluation_id,
                          goodWhen: "up",
                          format: (v) => v.toFixed(3),
                        }
                      : undefined
                  }
                />
                <StatTile
                  label="Metrics failing"
                  value={derived.counts.fail}
                  format={formatCount}
                  footnote={<StatusCounts counts={derived.counts} totalLabel="measured" hint className="text-xs" />}
                  concept="core:status"
                />
                <StatTile
                  label="Not evaluated"
                  value={derived.counts.not_evaluated}
                  format={formatCount}
                  footnote={
                    notApplicable
                      ? `${formatCount(notApplicable)} not applicable (no key declared): nothing to check. The rest is not a pass.`
                      : "Not evaluated is not a pass: see the reasons below."
                  }
                />
                <StatTile
                  label="Synthetic rows in scope"
                  value={rowsSynthetic || null}
                  format={(v) => formatCompact(v)}
                  footnote={`${derived.tables.length} table${derived.tables.length === 1 ? "" : "s"} · ${formatCount(detail.flags.length)} flagged rows`}
                />
              </div>
              <Scorecards
                cards={derived.cards}
                selectedTable={table}
                adjustments={derived.adjusted}
                tolerance={tolerance}
                onOpen={onOpen}
              />
              <div className={cn("grid gap-6", !wideGraph && "xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]")}>
                <InterpretationPanel
                  headline={derived.headline}
                  findings={derived.findings}
                  adjusted={derived.adjustedSummary ?? unreadFails}
                  onOpen={onOpen}
                />
                <ModelGraph
                  graph={derived.graph}
                  facts={derived.factsByName}
                  modelName={evaluation.relationship_model}
                  resolvedFrom={derived.graph.resolvedFrom}
                  selected={table}
                  onSelect={(t) => setSearch({ table: t })}
                />
              </div>
            </>
          ) : (
            <>
              <EmptyState
                title={
                  stale
                    ? `No metrics, and no result recorded for more than ${STALE_AFTER_HOURS} hours`
                    : running
                      ? "Metrics are still being computed"
                      : "No metrics were written"
                }
                description={
                  evaluation.status_reason ??
                  (stale
                    ? `The registry has a RUNNING event from ${formatDateTime(evaluation.recorded_at)} and no FINAL event. The evaluation most likely ended without recording a result, so no metrics are expected: evaluate the launch again to get them.`
                    : running
                      ? "The registry has a RUNNING event and no FINAL event yet. Reload later."
                      : "The evaluator wrote a registry row but no metric rows.")
                }
              />
              <ModelGraph
                graph={derived.graph}
                facts={derived.factsByName}
                modelName={evaluation.relationship_model}
                resolvedFrom={derived.graph.resolvedFrom}
                selected={table}
                onSelect={(t) => setSearch({ table: t })}
              />
            </>
          )}
        </TabsContent>

        <TabsContent value="columns">
          {tab === "columns" ? (
            derived.summaries.length ? (
              <ColumnHeatmap
                model={heatmap}
                rows={search.rows ?? HEATMAP_PAGE}
                family={search.family}
                problemsOnly={search.problems ?? false}
                query={search.q ?? ""}
                onFamily={(family) => setSearch({ family: family as EvaluationRunSearch["family"] }, true)}
                onProblems={(problems) => setSearch({ problems: problems || undefined }, true)}
                onQuery={(q) => setSearch({ q: q || undefined }, true)}
                onMore={(rows) => setSearch({ rows }, true)}
                onOpen={openColumn}
              />
            ) : (
              <EmptyState
                title="No column metrics"
                description={evaluation.status_reason ?? "This evaluation wrote no field- or column-level rows."}
              />
            )
          ) : null}
        </TabsContent>
        <TabsContent value="pairs">
          {tab === "pairs" ? (
            <PairsPanel evaluationId={evaluationId} metrics={scopedMetrics} table={table} noMetrics={noMetrics} />
          ) : null}
        </TabsContent>
        <TabsContent value="privacy">
          {tab === "privacy" ? (
            <PrivacyPanel
              evaluation={evaluation}
              evaluationId={evaluationId}
              metrics={scopedMetrics}
              flags={detail.flags}
              table={table}
            />
          ) : null}
        </TabsContent>
        <TabsContent value="detection">
          {tab === "detection" ? (
            <DetectionPanel evaluationId={evaluationId} metrics={scopedMetrics} table={table} />
          ) : null}
        </TabsContent>
        <TabsContent value="relational">
          {tab === "relational" ? (
            <RelationalPanel
              graph={derived.graph}
              table={table}
              noMetrics={noMetrics}
              adjustments={derived.adjusted}
              adjustmentsError={adjustmentsError}
              tolerance={tolerance}
            />
          ) : null}
        </TabsContent>
        <TabsContent value="params">
          {tab === "params" ? (
            <div className="grid gap-4">
              <GeneratorPanel
                evaluation={evaluation}
                record={record}
                error={recordQuery.error ? String(recordQuery.error.message) : null}
              />
              <ParamsPanel evaluation={evaluation} adjustedTables={derived.adjustedTables} />
            </div>
          ) : null}
        </TabsContent>
      </Tabs>

      {query.isFetching && !query.isPending ? <p className="sr-only">Refreshing…</p> : null}
      {bytes.length ? (
        <p className="text-xs text-text-3">
          BigQuery bytes for this view: {formatCompact(bytes.reduce((a, b) => a + b, 0))} B (dry run).
        </p>
      ) : null}
      {evaluation.event === "RUNNING" && !stale ? (
        <Callout tone="info" title="Still running">
          Numbers may change: the FINAL event has not been recorded yet
          {evaluation.finished_at ? "" : ` (started ${formatDateTime(evaluation.evaluated_at)})`}.
        </Callout>
      ) : null}
      <NoiseLegend docked />
      <ColumnDrawer
        evaluationId={evaluationId}
        columnKey={search.column}
        summary={drawerSummary}
        onClose={() => setSearch({ column: undefined })}
      />
    </div>
  );
}
