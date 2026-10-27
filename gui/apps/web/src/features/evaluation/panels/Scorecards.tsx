/**
 * Family scorecards: overall, fidelity, privacy, integrity, diversity. Each
 * card leads with the model roll-up score and its status, breaks it down per
 * table and per catalogue level (LevelChip + status counts), and lists the
 * metrics to look at first with their raw value, score, baseline and noise
 * floor side by side. A PK duplicate rate on a key the launch adjusted
 * (ADR 0038) shows the generator's verdict, both key-repeat shares and the
 * evaluator's own status and value; the card's key-failure count stays the
 * evaluator's, with the number of them on adjusted keys under it. The per-table
 * list renders only its visible rows above twelve tables, and each table's
 * integrity score opens the units it averages (ADR 0036, ADR 0042).
 */
import { useMemo, useState } from "react";

import type { MetricRow } from "@contracts/api";
import { inapplicableKey, NOT_APPLICABLE_LABEL } from "@contracts/applicability";

import { AdjustmentPill } from "@/components/AdjustmentPill";
import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { StatusPill } from "@/components/StatusPill";
import { fmtPoints, fmtRepeatShare, fmtTolerance } from "@/lib/adjustments";
import { cn } from "@/lib/cn";

import { PK_METRIC, type AdjustmentRow } from "../lib/adjustment";
import type { TableView } from "../lib/tables";
import { FAMILY_LABEL, FAMILY_QUESTION, isLevel, metricConcept, metricShort } from "../lib/catalogue";
import { fmtCompared, fmtMetric, fmtScore, scopeLabel } from "../lib/format";
import type { RunTab } from "../lib/interpret";
import { tabFor } from "../lib/interpret";
import type { FamilyCard } from "../lib/model";
import { IntegrityUnits } from "../components/IntegrityUnits";
import { NoiseDowngrade } from "../components/NoiseDowngrade";
import { StatusCounts } from "../components/StatusCounts";
import { VirtualList } from "../components/VirtualList";
import { integrityUnitsByTable, type IntegrityUnit } from "../lib/integrity";
import { readingOf, undefinedValueText } from "../lib/reading";

export type OpenTarget = { tab: RunTab; column?: string; table?: string };

function ScoreBar({ score, warn, fail }: { score: number | null; warn: number; fail: number }) {
  return (
    <span aria-hidden="true" className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
      {score !== null ? (
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-seq-4"
          style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }}
        />
      ) : null}
      <span className="absolute inset-y-0 w-px bg-status-critical" style={{ left: `${fail * 100}%` }} />
      <span className="absolute inset-y-0 w-px bg-status-warn" style={{ left: `${warn * 100}%` }} />
    </span>
  );
}

/** The generator's reading of a PK row's key (ADR 0038), when the adjustments say anything about its table. */
function adjustedFor(row: MetricRow, adjustments: readonly AdjustmentRow[]): AdjustmentRow | null {
  if (row.metric_id !== PK_METRIC) return null;
  const found = adjustments.find((a) => a.table === row.table_name);
  return found?.reading ? found : null;
}

function WorstRow({
  row,
  adjustments,
  tolerance,
  onOpen,
}: {
  row: MetricRow;
  tolerance?: number;
  adjustments: readonly AdjustmentRow[];
  onOpen: (target: OpenTarget) => void;
}) {
  const k = row.value_kind;
  const reading = readingOf(row);
  const adjusted = adjustedFor(row, adjustments);
  // The adjusted reading's evidence (both shares, the strip) is on the Relational tab.
  const tab = adjusted ? "relational" : tabFor(row);
  const concept = metricConcept(row.metric_id);
  const replaced = adjusted?.reading?.kind === "adjusted";
  // ADR 0042: a key check the model gave no key for is nothing to check, not a gap.
  const na = inapplicableKey(row);
  return (
    <li
      className="grid min-w-0 gap-0.5 border-t border-border pt-2"
      data-adjusted={adjusted ? "true" : undefined}
      data-metric={row.metric_id}
    >
      {/* Only a row with a reading wraps: every other row keeps its one line, truncated. */}
      <div className={cn("flex min-w-0 items-center gap-1.5", adjusted && "flex-wrap")}>
        {replaced ? null : na ? (
          <>
            <StatusPill status="not_evaluated" label="Not applicable" tone="neutral" size="sm" />
            <InfoHint concept="eval:not-applicable" />
          </>
        ) : (
          <StatusPill status={row.status} size="sm" />
        )}
        {adjusted?.reading ? (
          <AdjustmentPill
            verdict={adjusted.adjustment.verdict}
            disagree={adjusted.reading.kind === "disagree"}
            evaluator={adjusted.reading.evaluatorText}
          />
        ) : null}
        {reading.downgradedFrom ? <NoiseDowngrade from={reading.downgradedFrom} /> : null}
        <button
          type="button"
          onClick={() =>
            onOpen({
              tab,
              table: row.level === "model" ? undefined : row.table_name,
              column: tab === "columns" && row.column_name ? `${row.table_name}.${row.column_name}` : undefined,
            })
          }
          className="min-w-0 cursor-pointer truncate text-left text-xs font-medium text-link hover:underline"
        >
          {metricShort(row.metric_id)} · <span className="font-mono">{scopeLabel(row)}</span>
        </button>
        {concept ? <InfoHint concept={concept} /> : null}
      </div>
      <p className="font-mono text-[11px] break-words text-text-2 tabular-nums">
        {na ? (
          <span>{NOT_APPLICABLE_LABEL[na]}</span>
        ) : row.status === "not_evaluated" ? (
          <span>not evaluated</span>
        ) : row.value === null ? (
          // No finite point value, graded on its CI bound (Ruling R38): the reading names the gate.
          <>
            value {undefinedValueText(reading).split(":")[0]} · gate {reading.gateSource} {fmtMetric(reading.gate, k)} ·
            score {fmtScore(row.score)}
          </>
        ) : replaced && adjusted ? (
          // The generator's two shares, and the evaluator's own value kept beside them.
          <>
            source {fmtRepeatShare(adjusted.adjustment.source_repeat_share)} · synthetic{" "}
            {fmtRepeatShare(adjusted.adjustment.landing_repeat_share)} · Δ{" "}
            {fmtPoints(adjusted.adjustment.repeat_share_delta)} · tolerance {fmtTolerance(tolerance)} · evaluator value{" "}
            {fmtCompared([row.value, 0], k)[0]}
          </>
        ) : (
          <>
            value {fmtMetric(row.value, k)} · score {fmtScore(row.score)} · baseline {fmtMetric(row.baseline_value, k)}{" "}
            · floor {fmtMetric(row.noise_floor, k)}
          </>
        )}
      </p>
    </li>
  );
}

/**
 * Each per-table row's height (rem; the row's `h-6`): the windowed list needs one height for every row, in
 * px, so it is converted at the root's font size — a larger default font size (WCAG 1.4.4) grows the rows
 * and their positions together, and they never overlap.
 */
export const TABLE_ROW_REM = 1.5;

/** The root font size (px), read once per list; 16 where it cannot be read. */
function rootFontPx(): number {
  const px = typeof window === "undefined" ? NaN : parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(px) && px > 0 ? px : 16;
}

type TableScore = FamilyCard["tables"][number];

const NO_UNITS: readonly IntegrityUnit[] = [];

/** A family's score per table: windowed above twelve tables (ADR 0036); integrity scores open their units. */
function TableScores({
  card,
  rows,
  selectedTable,
}: {
  card: FamilyCard;
  rows: readonly TableScore[];
  selectedTable?: string;
}) {
  const family = FAMILY_LABEL[card.family] ?? card.family;
  const [rowPx] = useState(() => TABLE_ROW_REM * rootFontPx());
  // Grouped once per card: a scroll re-renders only the visible rows, which read this map.
  const units = useMemo(
    () => (card.family === "integrity" ? integrityUnitsByTable(card.metrics) : null),
    [card.family, card.metrics],
  );
  return (
    <VirtualList
      items={rows}
      rowHeight={rowPx}
      label={`${family} score per table`}
      frameLabel={`Per-table scores, ${family}: ${rows.length} tables`}
      getKey={(t) => t.table}
      scrollToIndex={selectedTable ? rows.findIndex((t) => t.table === selectedTable) : undefined}
      renderItem={(t) => (
        <div
          data-table={t.table}
          className={cn(
            "grid h-6 grid-cols-[minmax(0,1fr)_3rem_auto] items-center gap-2 text-xs",
            selectedTable === t.table && "font-semibold",
          )}
        >
          <span className="truncate font-mono text-text-2" title={t.table}>
            {t.table}
          </span>
          {units ? (
            <IntegrityUnits table={t.table} score={t.score} units={units.get(t.table) ?? NO_UNITS} />
          ) : (
            <span className="text-right text-text-1 tabular-nums">{fmtScore(t.score)}</span>
          )}
          {t.status ? <StatusPill status={t.status} size="sm" /> : <span className="text-text-3">—</span>}
        </div>
      )}
    />
  );
}

function Card({
  card,
  selectedTable,
  adjustments,
  tolerance,
  view,
  onOpen,
}: {
  card: FamilyCard;
  tolerance?: number;
  selectedTable?: string;
  adjustments: readonly AdjustmentRow[];
  view?: TableView | null;
  onOpen: (t: OpenTarget) => void;
}) {
  const titleId = `scorecard-${card.family}`;
  // The filter bar's tables in its order; the roll-up and the counts above stay the whole run's.
  const rows = useMemo(() => {
    if (!view) return card.tables;
    const byTable = new Map(card.tables.map((t) => [t.table, t]));
    return view.order.flatMap((name) => byTable.get(name) ?? []);
  }, [card.tables, view]);
  const warn = card.rollup?.threshold_warn ?? 0.85;
  const fail = card.rollup?.threshold_fail ?? 0.7;
  // ADR 0038: of the key failures the evaluator counted, those on keys the launch adjusted. The
  // count itself stays the evaluator's.
  const adjustedKeyFailures = card.metrics.filter(
    (m) => m.status === "fail" && adjustedFor(m, adjustments)?.reading?.kind === "adjusted",
  ).length;
  return (
    <section
      aria-labelledby={titleId}
      data-family={card.family}
      className={cn(
        "flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface-1 p-4",
        card.family === "overall" && "border-border-strong",
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={titleId} className="flex items-center gap-0.5 text-sm font-semibold text-text-1">
            {FAMILY_LABEL[card.family] ?? card.family}
            <InfoHint concept={card.family === "overall" ? "core:score" : "core:family"} />
          </h3>
          <p className="text-xs text-text-3">{FAMILY_QUESTION[card.family]}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {card.status ? <StatusPill status={card.status} size="sm" /> : null}
          {card.keyFailures ? (
            <span title="Duplicate keys or orphans on enforced edges: integrity holds by construction, so these FAIL whatever the averages say (Ruling R39).">
              <StatusPill
                status="fail"
                tone="critical"
                label={`${card.keyFailures} key ${card.keyFailures === 1 ? "failure" : "failures"}`}
                size="sm"
              />
            </span>
          ) : null}
          {card.keyFailures && adjustedKeyFailures ? (
            <span
              data-testid="adjusted-key-failures"
              className="inline-flex items-center gap-0.5 text-[11px] whitespace-nowrap text-text-3"
            >
              {adjustedKeyFailures} on adjusted {adjustedKeyFailures === 1 ? "key" : "keys"}
              <InfoHint concept="eval:model-adjusted" />
            </span>
          ) : null}
        </div>
      </header>
      <div className="flex items-end gap-3">
        <p className="text-4xl leading-none font-semibold tracking-tight text-text-1">{fmtScore(card.score)}</p>
        <div className="flex min-w-0 flex-1 flex-col gap-1 pb-1">
          <ScoreBar score={card.score} warn={warn} fail={fail} />
          <span className="text-[10px] text-text-3">
            score 0–1 · warn ≤ {warn.toFixed(2)} · fail ≤ {fail.toFixed(2)}
            {card.rollup ? "" : " · no roll-up row: registry score"}
          </span>
        </div>
      </div>
      {card.tables.length > 1 ? (
        <div className="grid gap-1">
          {view && rows.length !== card.tables.length ? (
            <p className="text-[11px] text-text-3">
              {rows.length} of {card.tables.length} tables (filtered)
            </p>
          ) : null}
          {rows.length ? (
            <TableScores card={card} rows={rows} selectedTable={selectedTable} />
          ) : (
            <p className="text-xs text-text-3">No table matches the filters.</p>
          )}
        </div>
      ) : null}
      <p className="-mb-1 flex items-center gap-0.5 text-[11px] font-medium tracking-wide text-text-3 uppercase">
        Metrics per level
        <InfoHint concept="eval:info-status" />
      </p>
      <ul className="flex flex-wrap gap-x-3 gap-y-1.5" aria-label={`${FAMILY_LABEL[card.family]} metrics per level`}>
        {card.levels.map((l) => (
          <li key={l.level} className="inline-flex items-center gap-1.5">
            {isLevel(l.level) ? <LevelChip level={l.level} size="sm" /> : <span className="text-xs">{l.level}</span>}
            <StatusCounts counts={l.counts} />
          </li>
        ))}
      </ul>
      {card.worst.length ? (
        <div className="grid gap-1">
          <h4 className="text-[11px] font-medium tracking-wide text-text-3 uppercase">Look first</h4>
          <ul className="grid gap-2">
            {card.worst.map((row) => (
              <WorstRow
                key={`${row.metric_id}|${scopeLabel(row)}`}
                row={row}
                adjustments={adjustments}
                tolerance={tolerance}
                onOpen={onOpen}
              />
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-text-3">No measured metrics in this family.</p>
      )}
    </section>
  );
}

const NO_ADJUSTMENTS: readonly AdjustmentRow[] = [];

export function Scorecards({
  cards,
  selectedTable,
  adjustments = NO_ADJUSTMENTS,
  tolerance,
  view,
  onOpen,
}: {
  cards: FamilyCard[];
  /** The run's repeat-share tolerance (A1 ruling R16); the default when omitted. */
  tolerance?: number;
  /** The filter bar's tables and order (lib/tables.ts `tableView`); null: every table, in score order. */
  view?: TableView | null;
  selectedTable?: string;
  /** ADR 0038: per table, the generator's verdict on its declared key (lib/adjustment.ts `adjustmentRows`). */
  adjustments?: readonly AdjustmentRow[];
  onOpen: (target: OpenTarget) => void;
}) {
  return (
    <section aria-labelledby="scorecards-title" className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="scorecards-title" className="text-lg font-semibold tracking-tight text-text-1">
          Scorecards
        </h2>
        <span className="text-xs text-text-3">model roll-ups · per table · per level · what to look at first</span>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => (
          <Card
            key={card.family}
            card={card}
            selectedTable={selectedTable}
            adjustments={adjustments}
            tolerance={tolerance}
            view={view}
            onOpen={onOpen}
          />
        ))}
      </div>
    </section>
  );
}
