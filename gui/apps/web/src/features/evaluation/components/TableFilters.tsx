/**
 * The table filter bar (ADR 0042): one row above the scorecards, its values in the URL
 * (gui/docs/UX.md). It narrows and orders the isolated-table tiles and the per-table scorecard
 * rows; the roll-ups, the findings and the counts stay the whole run's. Its count says so: a
 * selected table it would hide stays shown, and the tables of the layered graph are not filtered.
 */
import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatCount } from "@/lib/format";

import { batchLabel, type TableFilter, type TableSort, type TableStatus } from "../lib/tables";

const ALL = "all";

export function TableFilters({
  value,
  onChange,
  batches,
  shown,
  total,
  pinned = null,
  layered = 0,
}: {
  value: TableFilter;
  onChange: (patch: Partial<TableFilter>) => void;
  /** The batch jobs the run's tables belong to (none: the batch filter is hidden). */
  batches: readonly number[];
  /** The tables the filter matches, of `total`. */
  shown: number;
  total: number;
  /** The selected table shown although the filter does not match it. */
  pinned?: string | null;
  /** The run's tables drawn in the layered graph, which the bar does not narrow. */
  layered?: number;
}) {
  const active = !!(value.query || value.pk || value.status || value.batch !== undefined);
  return (
    <div role="group" aria-label="Table filters" className="flex flex-wrap items-center gap-3">
      <label className="relative flex min-w-48 flex-1 items-center sm:max-w-72">
        <span className="sr-only">Find a table</span>
        <Search className="pointer-events-none absolute left-2.5 size-4 text-text-3" aria-hidden="true" />
        <input
          type="search"
          value={value.query ?? ""}
          onChange={(event) => onChange({ query: event.target.value || undefined })}
          placeholder="Find a table…"
          className="h-9 w-full rounded-md border border-control-border bg-surface-1 pr-3 pl-8 text-sm text-text-1 placeholder:text-text-3"
        />
      </label>
      <ToggleGroup
        type="single"
        aria-label="Primary key"
        value={value.pk ?? "any"}
        onValueChange={(v) => v && onChange({ pk: v === "any" ? undefined : (v as "yes" | "no") })}
      >
        <ToggleGroupItem value="any">Any key</ToggleGroupItem>
        <ToggleGroupItem value="yes">PK</ToggleGroupItem>
        <ToggleGroupItem value="no">No PK</ToggleGroupItem>
      </ToggleGroup>
      <ToggleGroup
        type="single"
        aria-label="Table status"
        value={value.status ?? ALL}
        onValueChange={(v) => v && onChange({ status: v === ALL ? undefined : (v as TableStatus) })}
        className="max-w-full overflow-x-auto"
      >
        <ToggleGroupItem value={ALL}>All</ToggleGroupItem>
        <ToggleGroupItem value="pass">Pass</ToggleGroupItem>
        <ToggleGroupItem value="warn">Warn</ToggleGroupItem>
        <ToggleGroupItem value="fail">Fail</ToggleGroupItem>
        <ToggleGroupItem value="not_evaluated">Not evaluated</ToggleGroupItem>
      </ToggleGroup>
      {batches.length ? (
        <Select
          value={value.batch === undefined ? ALL : String(value.batch)}
          onValueChange={(v) => onChange({ batch: v === ALL ? undefined : Number(v) })}
        >
          <SelectTrigger aria-label="Batch job" className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All batch jobs</SelectItem>
            {batches.map((b) => (
              <SelectItem key={b} value={String(b)}>
                {batchLabel(b)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
      <Select value={value.sort ?? "score"} onValueChange={(v) => onChange({ sort: v as TableSort })}>
        <SelectTrigger aria-label="Sort tables" className="w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="score">Worst first</SelectItem>
          <SelectItem value="name">By name</SelectItem>
        </SelectContent>
      </Select>
      <span className="text-xs text-text-3" aria-live="polite">
        {formatCount(shown)} of {formatCount(total)} tables
        {pinned ? ` · plus ${pinned}, selected` : ""}
        {layered
          ? ` · the ${formatCount(layered)} in the layered graph ${layered === 1 ? "is" : "are"} not filtered`
          : ""}
      </span>
      {active ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange({ query: undefined, pk: undefined, status: undefined, batch: undefined })}
        >
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}
