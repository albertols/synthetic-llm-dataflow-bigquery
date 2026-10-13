/**
 * Relational: first the keys the launch adjusted (ADR 0038) — per table the
 * declared key, the source's and the synthetic key-repeat share, the
 * generator's verdict and, beside it, the evaluator's own duplicate rate —
 * then, per foreign-key edge, the orphan rate (enforced edges gate at 0;
 * documented edges are INFO next to the source's own orphan rate, never a
 * FAIL) and the edge's fan-out and adherence metrics. The evaluator compares
 * the two fan-out distributions and stores the distances, not the histograms.
 */
import { useId, type ReactNode } from "react";

import type { MetricRow } from "@contracts/api";

import { AdjustmentPill } from "@/components/AdjustmentPill";
import { Callout } from "@/components/Callout";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { StatusPill } from "@/components/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtPoints, fmtRepeatShare, fmtTolerance, REPEAT_SHARE_TOLERANCE } from "@/lib/adjustments";
import { formatCount, MISSING } from "@/lib/format";

import { IntervalPlot, type IntervalRow } from "../components/IntervalPlot";
import { MetricReadout } from "../components/MetricReadout";
import { VisualFrame } from "../components/VisualFrame";
import type { AdjustmentRow } from "../lib/adjustment";
import { fmtCompared, fmtShare } from "../lib/format";
import type { Graph, GraphEdge } from "../lib/graph";
import { statusRank } from "../lib/model";
import { isDocumentedEdge } from "../lib/reading";

function EdgeDetail({ edge }: { edge: GraphEdge }) {
  // The label holds spaces and parentheses (`child(col) -> parent(col)`): not an id.
  const titleId = useId();
  const others = edge.rows
    .filter((r) => r.metric_id !== "relationship.orphan_rate" && r.metric_id !== "relationship.orphan_rate_source")
    .sort((a, b) => statusRank(a.status) - statusRank(b.status));
  return (
    <section aria-labelledby={titleId} className="grid gap-3 rounded-lg border border-border bg-surface-1 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={titleId} className="font-mono text-sm font-semibold break-all text-text-1">
          {edge.label}
        </h3>
        <Badge variant={edge.documented ? "outline" : "neutral"}>
          {edge.documented ? "documented" : (edge.role ?? "enforced")}
        </Badge>
        {edge.status ? <StatusPill status={edge.status} size="sm" /> : null}
      </header>
      {others.length ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {others.map((row) => (
            <MetricReadout key={row.metric_id} row={row} showScope={false} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-text-3">No other metrics on this edge.</p>
      )}
    </section>
  );
}

/** A column header with its (i). */
function Hinted({ concept, children }: { concept: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      {children} <InfoHint concept={concept} />
    </span>
  );
}

const JOINED_BY: Record<string, string> = {
  run_id: "joined by run id",
  base_run_id: "joined by the launch's base run id",
  window: "joined by landing table and generation window",
};

const SHARE_TICKS = [0, 0.25, 0.5, 0.75, 1];

/** The strip: each table's synthetic share (dot) against its own source share (tick) ± the tolerance (band). */
function repeatShareRows(rows: readonly AdjustmentRow[]): IntervalRow[] {
  return rows
    .filter((r) => r.adjustment.source_repeat_share !== null)
    .map((r) => {
      const source = r.adjustment.source_repeat_share!;
      return {
        key: r.table,
        label: r.table,
        // Source → synthetic, the tick and the dot in numbers (the declared key is in the table above).
        sublabel: `${fmtRepeatShare(source)} → ${fmtRepeatShare(r.adjustment.landing_repeat_share)}`,
        value: r.adjustment.landing_repeat_share,
        // The band belongs to the reference, not to the dot: no whisker of the dot's own.
        lo: null,
        hi: null,
        ref: source,
        band: {
          lo: Math.max(0, source - REPEAT_SHARE_TOLERANCE),
          hi: Math.min(1, source + REPEAT_SHARE_TOLERANCE),
        },
        status: r.reading?.status ?? "info",
        mark: <AdjustmentPill verdict={r.adjustment.verdict} disagree={r.reading?.kind === "disagree"} />,
        detail: `Δ ${fmtPoints(r.adjustment.repeat_share_delta)}`,
      };
    });
}

/**
 * ADR 0038: the generator's half of the key question, next to the evaluator's. Nothing here
 * changes an orphan rate: referential integrity is a separate question, read below.
 */
function ModelAdjustments({ rows }: { rows: readonly AdjustmentRow[] }) {
  const strip = repeatShareRows(rows);
  const readouts = rows.filter((r) => r.row && r.reading);
  return (
    <section aria-labelledby="adjustments-title" className="grid gap-3" data-testid="model-adjustments">
      <h2 id="adjustments-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
        Model adjustments (ADR 0038)
        <InfoHint concept="eval:model-adjusted" />
      </h2>
      <p className="flex flex-wrap items-center gap-0.5 text-sm text-text-2">
        The relationship model declared these keys unique; a full scan of the source proved they repeat, so the launch
        dropped them from the effective model and copied the source&apos;s repeat share. The verdict compares the two
        shares (tolerance {fmtTolerance()}). The evaluator still grades the declared key, where any repeat fails: its
        rate and status stay beside the verdict, unchanged.
        <InfoHint concept="eval:repeat-share" />
      </p>
      <TableContainer aria-label="Model adjustments per table">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead scope="col">Table</TableHead>
              <TableHead scope="col">
                <Hinted concept="eval:model-adjusted">Declared PK</Hinted>
              </TableHead>
              <TableHead scope="col" className="text-right">
                <Hinted concept="eval:repeat-share">Source share</Hinted>
              </TableHead>
              <TableHead scope="col" className="text-right">
                <Hinted concept="eval:repeat-share">Synthetic share</Hinted>
              </TableHead>
              <TableHead scope="col" className="text-right">
                <Hinted concept="eval:repeat-share">Δ (syn − src)</Hinted>
              </TableHead>
              <TableHead scope="col">
                <Hinted concept="eval:model-adjusted">Verdict</Hinted>
              </TableHead>
              <TableHead scope="col" className="text-right">
                <Hinted concept="metric:table.pk_duplicate_rate">Evaluator duplicate rate</Hinted>
              </TableHead>
              <TableHead scope="col">
                <Hinted concept="core:status">Evaluator status</Hinted>
              </TableHead>
              <TableHead scope="col">
                <Hinted concept="eval:model-adjusted">Excluded rules</Hinted>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => {
              const a = r.adjustment;
              return (
                <TableRow key={r.table} data-table={r.table} data-verdict={a.verdict}>
                  <TableCell className="text-xs whitespace-nowrap">
                    <span className="font-mono font-semibold text-text-1">{r.table}</span>
                    <span className="block text-[11px] text-text-3">
                      {a.run_id ? (JOINED_BY[a.joined_by ?? ""] ?? "joined") : "no validation_runs row joined"}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs [overflow-wrap:anywhere]">
                    {r.keyColumns.join(", ") || MISSING}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {fmtRepeatShare(a.source_repeat_share)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {fmtRepeatShare(a.landing_repeat_share)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {fmtPoints(a.repeat_share_delta)}
                  </TableCell>
                  <TableCell className="text-xs">
                    {r.reading ? (
                      <AdjustmentPill verdict={a.verdict} disagree={r.reading.kind === "disagree"} />
                    ) : (
                      <span className="text-text-3">
                        {a.verdict === "not_adjusted" ? "not adjusted: a unique key is expected" : MISSING}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {r.row && r.row.value !== null ? fmtCompared([r.row.value, 0], "share")[0] : MISSING}
                  </TableCell>
                  <TableCell>
                    {r.row ? (
                      <StatusPill status={r.row.status} size="sm" />
                    ) : (
                      <span className="text-xs text-text-3">{MISSING}</span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs whitespace-nowrap">
                    {a.excluded_rules.length ? a.excluded_rules.join(", ") : MISSING}
                    {a.pk_duplicate_dlq !== null && a.adjusted ? (
                      <span className="block text-[11px] text-text-3">
                        {formatCount(a.pk_duplicate_dlq)} counted, none removed
                      </span>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
      {strip.length ? (
        <VisualFrame
          title="Synthetic key-repeat share against the source's"
          concept="eval:repeat-share"
          description={`Tick: the source's share. Band: the generator's tolerance, ${fmtTolerance()}. Dot: the synthetic share — inside the band, the copy is faithful. Under each table: source → synthetic.`}
          data={rows.map((r) => ({
            table: r.table,
            declared_pk: r.keyColumns.join(", "),
            source_repeat_share: r.adjustment.source_repeat_share,
            synthetic_repeat_share: r.adjustment.landing_repeat_share,
            delta: r.adjustment.repeat_share_delta,
            tolerance: REPEAT_SHARE_TOLERANCE,
            verdict: r.adjustment.verdict,
            evaluator_duplicate_rate: r.row?.value ?? null,
            evaluator_status: r.row?.status ?? null,
          }))}
        >
          <IntervalPlot
            rows={strip}
            scale={{ kind: "linear", lo: 0, hi: 1 }}
            lines={[]}
            ticks={SHARE_TICKS}
            formatTick={(t) => `${Math.round(t * 100)}%`}
          />
        </VisualFrame>
      ) : null}
      {readouts.length ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {readouts.map((r) => (
            <MetricReadout key={r.table} row={r.row!} adjusted={r} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

const NO_ADJUSTMENTS: readonly AdjustmentRow[] = [];

export function RelationalPanel({
  graph,
  table,
  noMetrics,
  adjustments = NO_ADJUSTMENTS,
  adjustmentsError,
}: {
  graph: Graph;
  table?: string;
  /**
   * The evaluation has no metric row at all: say that, not a guess at the model. "yet" while it is
   * running (its last event is RUNNING and not stale), "at-all" otherwise (it failed, or never finished).
   */
  noMetrics?: "yet" | "at-all";
  /** ADR 0038: per table, the generator's verdict on its declared key (lib/adjustment.ts `adjustmentRows`). */
  adjustments?: readonly AdjustmentRow[];
  /** Why the adjustments could not be loaded (the evaluator's rows below still read as stored). */
  adjustmentsError?: string | null;
}) {
  const edges = graph.edges.filter((e) => e.rows.length && (!table || e.child === table || e.parent === table));
  const scoped = adjustments.filter((r) => !table || r.table === table);
  // The section appears when the generator has something to say: a verdict, or a question.
  const adjusted = scoped.some((r) => r.reading) ? scoped : NO_ADJUSTMENTS;
  const failed = adjustmentsError ? (
    <Callout tone="warn" title="The launch's model adjustments could not be loaded">
      {adjustmentsError} Every PK duplicate rate on this page reads as the evaluator stored it: whether a key was
      adjusted by the launch (ADR 0038) is not known here.
    </Callout>
  ) : null;
  if (!edges.length && !adjusted.length)
    return (
      <div className="grid gap-4">
        {failed}
        <EmptyState
          title="No relationship metrics"
          description={
            noMetrics === "yet"
              ? "This evaluation has written no metric rows yet, so no relationship has been measured so far."
              : noMetrics === "at-all"
                ? "This evaluation wrote no metric rows at all, so no relationship was measured."
                : "The launch generated one table, or its relationship model declares no foreign keys between generated tables."
          }
        />
      </div>
    );
  const orphanRow = (row: MetricRow | null) => (row ? row.value : null);
  return (
    <div className="grid gap-6">
      {failed}
      {adjusted.length ? <ModelAdjustments rows={adjusted} /> : null}
      {edges.length ? (
        <>
          <section aria-labelledby="orphans-title" className="grid gap-3">
            <h2
              id="orphans-title"
              className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1"
            >
              Orphan rates
              <InfoHint concept="metric:relationship.orphan_rate" />
            </h2>
            <p className="flex flex-wrap items-center gap-0.5 text-sm text-text-2">
              Enforced edges are generated from their parent&apos;s keys, so any orphan fails. Documented edges
              (enforced: false) are reported as INFO next to the source&apos;s own orphan rate — never as a FAIL.
              <InfoHint concept="eval:documented-edge" />
            </p>
            <TableContainer aria-label="Orphan rate per foreign key">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead scope="col">Edge (child → parent)</TableHead>
                    <TableHead scope="col">Enforcement</TableHead>
                    <TableHead scope="col" className="text-right">
                      Synthetic orphan rate
                    </TableHead>
                    <TableHead scope="col" className="text-right">
                      Orphans
                    </TableHead>
                    <TableHead scope="col" className="text-right">
                      <span className="inline-flex items-center gap-0.5">
                        Source orphan rate <InfoHint concept="metric:relationship.orphan_rate_source" />
                      </span>
                    </TableHead>
                    <TableHead scope="col">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {edges.map((edge) => {
                    const orphans = (edge.orphan?.detail as { orphans?: number } | null | undefined)?.orphans;
                    const documented = edge.documented || (edge.orphan ? isDocumentedEdge(edge.orphan) : false);
                    return (
                      <TableRow key={edge.label} data-edge={edge.label}>
                        <TableCell className="font-mono text-xs break-all">{edge.label}</TableCell>
                        <TableCell className="text-xs whitespace-nowrap">
                          {documented
                            ? "documented (enforced: false)"
                            : `enforced${edge.role ? ` · ${edge.role}` : ""}`}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {fmtShare(orphanRow(edge.orphan))}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {orphans === undefined ? MISSING : formatCount(orphans)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {fmtShare(orphanRow(edge.orphanSource))}
                        </TableCell>
                        <TableCell>
                          {edge.orphan ? (
                            <StatusPill
                              status={documented ? "info" : edge.orphan.status}
                              size="sm"
                              label={documented ? "Info · documented" : undefined}
                            />
                          ) : (
                            <span className="text-xs text-text-3">{MISSING}</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          </section>
          <section aria-labelledby="fanout-title" className="grid gap-4">
            <h2
              id="fanout-title"
              className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1"
            >
              Fan-out and adherence per edge <InfoHint concept="eval:fanout" />
            </h2>
            {edges.map((edge) => (
              <EdgeDetail key={edge.label} edge={edge} />
            ))}
          </section>
        </>
      ) : null}
    </div>
  );
}
