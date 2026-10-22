/**
 * The units behind one table's integrity score (ADR 0042: nothing the evaluator scored is hidden,
 * nothing it left out is counted): a popover on the score, opened by click, Enter or Space, listing
 * the units that counted and the ones that were not applicable.
 */
import { InfoHint } from "@/components/InfoHint";
import { StatusPill } from "@/components/StatusPill";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { fmtScore } from "../lib/format";
import type { IntegrityUnit } from "../lib/integrity";

function UnitList({ title, units }: { title: string; units: readonly IntegrityUnit[] }) {
  if (!units.length) return null;
  return (
    <div className="grid gap-1">
      <p className="text-[11px] font-medium tracking-wide text-text-3 uppercase">{title}</p>
      <ul className="m-0 grid list-none gap-1 p-0 text-xs">
        {units.map((u) => (
          <li key={u.key} className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">
            {u.state === "not_applicable" ? (
              <StatusPill status="not_evaluated" label="Not applicable" tone="neutral" size="sm" />
            ) : null}
            <span className="font-mono [overflow-wrap:anywhere] text-text-1">{u.label}</span>
            {u.state === "counted" ? <span className="text-text-2 tabular-nums">{fmtScore(u.score)}</span> : null}
            {u.note ? <span className="text-text-3">{u.note}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function IntegrityUnits({
  table,
  score,
  units,
}: {
  table: string;
  score: number | null;
  units: readonly IntegrityUnit[];
}) {
  const counted = units.filter((u) => u.state === "counted");
  const na = units.filter((u) => u.state === "not_applicable");
  const unscored = units.filter((u) => u.state === "not_scored");
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          // WCAG 2.5.3 label in name: the name starts with the score the button shows.
          aria-label={`${score !== null && Number.isFinite(score) ? fmtScore(score) : "No score"} — integrity units of ${table}: ${counted.length} counted, ${na.length} not applicable`}
          className="min-h-6 min-w-6 cursor-pointer text-right text-link tabular-nums underline decoration-dotted underline-offset-2"
        >
          {fmtScore(score)}
        </button>
      </PopoverTrigger>
      <PopoverContent aria-label={`Integrity units of ${table}`} className="grid w-80 gap-3">
        <p className="flex items-center gap-0.5 text-sm font-semibold text-text-1">
          Integrity of <span className="font-mono">{table}</span> · {fmtScore(score)}
          <InfoHint concept="eval:integrity-units" />
        </p>
        <p className="text-xs text-text-2">
          The mean over the units that counted. A unit with nothing to score is left out: it neither raises nor lowers
          the score.
        </p>
        <UnitList title={`Counted (${counted.length})`} units={counted} />
        <UnitList title={`Not applicable (${na.length})`} units={na} />
        <UnitList title={`Not scored (${unscored.length})`} units={unscored} />
      </PopoverContent>
    </Popover>
  );
}
