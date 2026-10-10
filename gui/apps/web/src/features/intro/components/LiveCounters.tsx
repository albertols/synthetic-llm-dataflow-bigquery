/**
 * Live counters from the BFF: generation runs, evaluations and tables from
 * `/api/facets`, and the latest overall score with its trend from
 * `/api/evaluations`. The same tiles read the seeded mock or BigQuery.
 */
import { Link } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";

import type { EvaluationSummary, Facets } from "@contracts/api";

import { Callout } from "@/components/Callout";
import { InfoHint } from "@/components/InfoHint";
import { StatTile } from "@/components/StatTile";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useEvaluations, useFacets } from "@/lib/api";
import { useDataSource } from "@/lib/dataSource";
import { formatBytes, formatCount, formatDate, formatFixed, isFiniteNumber } from "@/lib/format";

import { IntroSection } from "./primitives";

/** How many recent evaluations the score trend reads. */
const TREND_WINDOW = 24;
const TREND_POINTS = 12;

export type ScoreSeries = { latest: number | null; previous: number | null; trend: number[] };

/**
 * The overall-score series: FINAL registry rows with a score, oldest first,
 * anchored on the facets' latest evaluation; `previous` is the one before it.
 */
export function scoreSeries(items: readonly EvaluationSummary[], latestId: string | undefined): ScoreSeries {
  const scored = items
    .filter((row) => row.event === "FINAL" && isFiniteNumber(row.overall_score))
    .sort((a, b) => a.evaluated_at.localeCompare(b.evaluated_at) || a.evaluation_id.localeCompare(b.evaluation_id));
  const at = latestId ? scored.findIndex((row) => row.evaluation_id === latestId) : scored.length - 1;
  const end = at >= 0 ? at : scored.length - 1;
  const upTo = scored.slice(0, end + 1);
  return {
    latest: upTo.at(-1)?.overall_score ?? null,
    previous: upTo.at(-2)?.overall_score ?? null,
    trend: upTo.slice(-TREND_POINTS).map((row) => row.overall_score as number),
  };
}

function SourceLine({ bytes }: { bytes: number | null }) {
  const source = useDataSource();
  const text =
    source.mode === "bigquery"
      ? `BigQuery · ${source.project}`
      : source.mode === "mock"
        ? "Seeded mock data · no GCP access needed"
        : source.mode === "offline"
          ? "The local BFF is offline"
          : "Connecting to the local BFF…";
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-3">
      <span className="inline-flex items-center gap-1">
        {text}
        <InfoHint concept="core:data-source" />
      </span>
      {bytes !== null ? (
        <span className="inline-flex items-center gap-1">
          Dry run: {formatBytes(bytes)} scanned
          <InfoHint concept="core:bytes-estimate" />
        </span>
      ) : null}
    </p>
  );
}

export function LiveCounters() {
  const facetsQuery = useFacets();
  const evaluationsQuery = useEvaluations({ sort: "evaluated_at", order: "desc", limit: TREND_WINDOW });
  const facets = facetsQuery.data?.data;
  const bytes = [facetsQuery.data?.bytesEstimate, evaluationsQuery.data?.bytesEstimate].filter(isFiniteNumber);

  return (
    <IntroSection
      id="counters"
      eyebrow="Live"
      title="What the registry holds right now"
      lead={<SourceLine bytes={bytes.length ? bytes.reduce((a, b) => a + b, 0) : null} />}
    >
      {facetsQuery.isError ? (
        <Callout
          tone="danger"
          title="Live counters are unavailable"
          action={
            <Button size="sm" onClick={() => void facetsQuery.refetch()}>
              <RefreshCw aria-hidden="true" />
              Retry
            </Button>
          }
        >
          The local BFF did not answer /api/facets ({facetsQuery.error.message}). Start it with{" "}
          <code className="font-mono">npm start</code> (mock data) and retry.
        </Callout>
      ) : !facets ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" role="status" aria-busy="true">
          <span className="sr-only">Loading the live counters</span>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32 w-full rounded-lg" />
          ))}
        </div>
      ) : (
        <CounterTiles facets={facets} items={evaluationsQuery.data?.data.items ?? []} />
      )}
    </IntroSection>
  );
}

function CounterTiles({ facets, items }: { facets: Facets; items: readonly EvaluationSummary[] }) {
  const { counts, latest, date_range: range } = facets;
  const series = scoreSeries(items, latest?.evaluation_id);
  const score = latest?.overall_score ?? null;
  const delta = isFiniteNumber(score) && isFiniteNumber(series.previous) ? score - series.previous : null;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="live-counters">
      <StatTile
        label="Generation runs"
        value={counts.runs}
        format={formatCount}
        concept="intro:counter-runs"
        footnote="validation_runs rows: one per table per launch"
      />
      <StatTile
        label="Evaluations"
        value={counts.evaluations}
        format={formatCount}
        concept="intro:counter-evaluations"
        footnote={range ? `${formatDate(range.min)} – ${formatDate(range.max)}` : "No evaluation yet"}
      />
      <StatTile
        label="Tables evaluated"
        value={counts.tables}
        format={formatCount}
        concept="intro:counter-tables"
        footnote={
          <span className="line-clamp-2 font-mono text-[11px]">{facets.tables.join(" · ") || "No table yet"}</span>
        }
      />
      <StatTile
        label="Latest overall score"
        value={score}
        format={(v) => formatFixed(v, 2)}
        concept="core:score"
        delta={
          delta === null
            ? undefined
            : { value: delta, vs: "the previous evaluation", goodWhen: "up", format: (v) => formatFixed(v, 3) }
        }
        trend={series.trend}
        footnote={
          latest ? (
            <span className="flex flex-wrap items-center gap-1.5">
              <StatusPill status={latest.status} size="sm" />
              <Link
                to="/evaluation/$evaluationId"
                params={{ evaluationId: latest.evaluation_id }}
                className="font-mono text-link hover:underline"
              >
                {latest.evaluation_id}
              </Link>
              <span>{formatDate(latest.evaluated_at)}</span>
            </span>
          ) : (
            "No finished evaluation yet"
          )
        }
      />
    </div>
  );
}
