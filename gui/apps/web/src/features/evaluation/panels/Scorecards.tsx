/**
 * Family scorecards: overall, fidelity, privacy, integrity, diversity. Each
 * card leads with the model roll-up score and its status, breaks it down per
 * table and per catalogue level (LevelChip + status counts), and lists the
 * metrics to look at first with their raw value, score, baseline and noise
 * floor side by side.
 */
import type { MetricRow } from "@contracts/api";

import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/lib/cn";

import { FAMILY_LABEL, FAMILY_QUESTION, isLevel, metricConcept, metricShort } from "../lib/catalogue";
import { fmtMetric, fmtScore, scopeLabel } from "../lib/format";
import type { RunTab } from "../lib/interpret";
import { tabFor } from "../lib/interpret";
import type { FamilyCard } from "../lib/model";
import { StatusCounts } from "../components/StatusCounts";

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

function WorstRow({ row, onOpen }: { row: MetricRow; onOpen: (target: OpenTarget) => void }) {
  const k = row.value_kind;
  const tab = tabFor(row);
  const concept = metricConcept(row.metric_id);
  return (
    <li className="grid min-w-0 gap-0.5 border-t border-border pt-2">
      <div className="flex min-w-0 items-center gap-1.5">
        <StatusPill status={row.status} size="sm" />
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
        {row.value === null ? (
          <span>not evaluated</span>
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

function Card({
  card,
  selectedTable,
  onOpen,
}: {
  card: FamilyCard;
  selectedTable?: string;
  onOpen: (t: OpenTarget) => void;
}) {
  const titleId = `scorecard-${card.family}`;
  const warn = card.rollup?.threshold_warn ?? 0.85;
  const fail = card.rollup?.threshold_fail ?? 0.7;
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
        {card.status ? <StatusPill status={card.status} size="sm" /> : null}
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
        <ul className="grid gap-1" aria-label={`${FAMILY_LABEL[card.family]} score per table`}>
          {card.tables.map((t) => (
            <li
              key={t.table}
              className={cn(
                "grid grid-cols-[minmax(0,1fr)_3rem_auto] items-center gap-2 text-xs",
                selectedTable === t.table && "font-semibold",
              )}
            >
              <span className="truncate font-mono text-text-2">{t.table}</span>
              <span className="text-right text-text-1 tabular-nums">{fmtScore(t.score)}</span>
              {t.status ? <StatusPill status={t.status} size="sm" /> : <span className="text-text-3">—</span>}
            </li>
          ))}
        </ul>
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
              <WorstRow key={`${row.metric_id}|${scopeLabel(row)}`} row={row} onOpen={onOpen} />
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-text-3">No measured metrics in this family.</p>
      )}
    </section>
  );
}

export function Scorecards({
  cards,
  selectedTable,
  onOpen,
}: {
  cards: FamilyCard[];
  selectedTable?: string;
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
          <Card key={card.family} card={card} selectedTable={selectedTable} onOpen={onOpen} />
        ))}
      </div>
    </section>
  );
}
