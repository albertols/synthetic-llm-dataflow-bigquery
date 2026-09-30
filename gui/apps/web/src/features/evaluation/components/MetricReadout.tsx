/**
 * MetricReadout — the honest card for one metric row: level, title and (i),
 * status, the metric bar, and every number side by side (value, score,
 * baseline, noise floor, 95 % CI, the gate the status read, n), then the
 * sentence that explains the status. `not_evaluated` shows detail.reason.
 */
import type { MetricRow } from "@contracts/api";

import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/lib/cn";
import { formatCount, MISSING } from "@/lib/format";

import { isLevel, metricConcept, metricShort, metricTitle } from "../lib/catalogue";
import { fmtMetric, fmtScore, scopeLabel } from "../lib/format";
import { explainStatus, readingOf } from "../lib/reading";
import { MetricBar } from "./MetricBar";
import { NoiseDowngrade } from "./NoiseDowngrade";

function Figure({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-[11px] text-text-3">{label}</dt>
      <dd
        className={cn(
          "truncate font-mono text-xs tabular-nums",
          emphasis ? "font-semibold text-text-1" : "text-text-2",
        )}
      >
        {value === MISSING ? (
          <>
            <span aria-hidden="true">{MISSING}</span>
            <span className="sr-only">none</span>
          </>
        ) : (
          value
        )}
      </dd>
    </div>
  );
}

export type MetricReadoutProps = {
  row: MetricRow;
  /** Show the scope (table.column, edge) under the title — off inside a column drawer. */
  showScope?: boolean;
  /** Heading level of the title (default 4). */
  headingLevel?: 3 | 4 | 5;
  className?: string;
};

export function MetricReadout({ row, showScope = true, headingLevel = 4, className }: MetricReadoutProps) {
  const reading = readingOf(row);
  const concept = metricConcept(row.metric_id);
  const Heading = `h${headingLevel}` as const;
  const k = reading.kind;
  const ci =
    reading.ciLow === null && reading.ciHigh === null
      ? MISSING
      : `${fmtMetric(reading.ciLow, k)} – ${reading.ciHigh === null ? "∞" : fmtMetric(reading.ciHigh, k)}`;
  // A lift with no events has no value but is evaluated on its CI bound (Ruling R38).
  const notEvaluated = row.status === "not_evaluated" || (row.value === null && reading.gate === null);
  return (
    <article
      data-metric={row.metric_id}
      data-status={row.status}
      className={cn("grid min-w-0 gap-2 rounded-md border border-border bg-surface-1 p-3", className)}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {isLevel(row.level) ? <LevelChip level={row.level} size="sm" /> : <span className="text-xs">{row.level}</span>}
        <div className="flex min-w-0 flex-1 items-center gap-0.5">
          <Heading className="truncate text-sm font-semibold text-text-1" title={metricTitle(row.metric_id)}>
            {metricShort(row.metric_id)}
          </Heading>
          {concept ? <InfoHint concept={concept} /> : null}
        </div>
        <StatusPill status={reading.documented && row.status !== "not_evaluated" ? "info" : row.status} size="sm" />
        {reading.downgradedFrom ? <NoiseDowngrade from={reading.downgradedFrom} /> : null}
      </header>
      {showScope ? <p className="-mt-1 truncate font-mono text-[11px] text-text-3">{scopeLabel(row)}</p> : null}
      {notEvaluated ? null : <MetricBar reading={reading} />}
      <dl className="grid grid-cols-3 gap-x-3 gap-y-1.5 sm:grid-cols-4">
        <Figure label="Value" value={reading.valueUndefined ? "undefined" : fmtMetric(reading.value, k)} emphasis />
        <Figure label="Score" value={fmtScore(reading.score)} />
        <Figure label="Baseline" value={fmtMetric(reading.baseline, k)} />
        <Figure label="Noise floor" value={fmtMetric(reading.noiseFloor, k)} />
        <Figure label="95% CI" value={ci} />
        <Figure
          label="Gate reads"
          value={reading.gateSource === "value" ? "value" : `${reading.gateSource} ${fmtMetric(reading.gate, k)}`}
        />
        <Figure label="Warn / fail" value={`${fmtMetric(reading.warn, k)} / ${fmtMetric(reading.fail, k)}`} />
        <Figure label="n src / syn" value={`${formatCount(row.n_source)} / ${formatCount(row.n_synthetic)}`} />
      </dl>
      <p className="text-xs leading-relaxed text-text-2">{explainStatus(row, reading)}</p>
    </article>
  );
}
