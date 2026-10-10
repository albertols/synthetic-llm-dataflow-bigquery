/**
 * The column × metric heatmap: one row per column (worst first), one cell per
 * field- or column-level metric, each cell the status as an icon + a word for
 * screen readers + the raw value, tinted by status. It is a real table — the
 * accessible form IS the visual — and renders `rows` rows at a time so a
 * 200-column evaluation stays fast.
 */
import { Search } from "lucide-react";
import { memo } from "react";

import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/cn";

import { FAMILIES, FAMILY_LABEL, metricConcept, metricShort, metricTitle } from "../lib/catalogue";
import { fmtMetric } from "../lib/format";
import { countsPhrase, type ColumnSummary, type HeatmapModel } from "../lib/model";
import { downgradedFrom, downgradeLabel, isDocumentedEdge } from "../lib/reading";

export const HEATMAP_PAGE = 50;

const CELL: Record<string, { glyph: string; word: string; className: string }> = {
  pass: { glyph: "✓", word: "pass", className: "bg-status-good/10" },
  warn: { glyph: "!", word: "warn", className: "bg-status-warn/20" },
  fail: { glyph: "✕", word: "fail", className: "bg-status-critical/25 font-semibold" },
  info: { glyph: "i", word: "info", className: "bg-status-info/12" },
  not_evaluated: { glyph: "–", word: "not evaluated", className: "bg-status-neutral/15" },
};

const HeatRow = memo(function HeatRow({
  summary,
  metricIds,
  family,
  onOpen,
}: {
  summary: ColumnSummary;
  metricIds: readonly string[];
  family?: string;
  onOpen: (key: string) => void;
}) {
  const worst = CELL[summary.worst ?? ""];
  return (
    <TableRow data-column={summary.key}>
      <th scope="row" className="sticky left-0 z-[1] bg-surface-1 px-2 py-1 text-left font-normal">
        <button
          type="button"
          onClick={() => onOpen(summary.key)}
          className="flex max-w-[15rem] min-w-0 cursor-pointer items-center gap-1.5 rounded-sm text-left hover:underline"
          aria-label={`Open ${summary.key}: ${countsPhrase(summary.counts)}`}
        >
          <span aria-hidden="true" className="w-3 shrink-0 text-center text-xs text-text-2">
            {worst?.glyph ?? "·"}
          </span>
          <span className="truncate font-mono text-xs text-link">{summary.key}</span>
          <span className="shrink-0 text-[10px] text-text-3">{summary.kind ?? ""}</span>
        </button>
      </th>
      {metricIds.map((id) => {
        const row = summary.byMetric.get(id);
        if (!row || (family && row.family !== family)) {
          return (
            <td key={id} className="px-1 py-1 text-center text-[11px] text-text-3">
              <span aria-hidden="true">·</span>
              <span className="sr-only">not applicable</span>
            </td>
          );
        }
        const status = isDocumentedEdge(row) ? "info" : row.status;
        const from = downgradedFrom(row);
        const cell = CELL[status] ?? { glyph: "?", word: status, className: "" };
        const value =
          row.value !== null
            ? fmtMetric(row.value, row.value_kind)
            : row.status === "not_evaluated"
              ? "n/e"
              : "undefined (gated on its CI bound)";
        return (
          <td
            key={id}
            data-status={status}
            title={`${metricTitle(id)} on ${summary.key}: ${value} (${cell.word}${from ? `, ${downgradeLabel(from)}` : ""})`}
            className={cn(
              "px-1.5 py-1 text-right font-mono text-[11px] whitespace-nowrap text-text-1 tabular-nums",
              cell.className,
            )}
          >
            <span aria-hidden="true" className="mr-1 text-text-2">
              {cell.glyph}
            </span>
            <span className="sr-only">{cell.word}, </span>
            {value}
            {from ? (
              // A PASS the evaluator downgraded as sampling noise (Ruling R40).
              <span className="ml-1 text-text-3">
                ≈<span className="sr-only"> within noise, was {from.toUpperCase()}</span>
              </span>
            ) : null}
          </td>
        );
      })}
    </TableRow>
  );
});

export function ColumnHeatmap({
  model,
  rows,
  family,
  problemsOnly,
  query,
  onFamily,
  onProblems,
  onQuery,
  onMore,
  onOpen,
}: {
  model: HeatmapModel;
  rows: number;
  family?: string;
  problemsOnly: boolean;
  query: string;
  onFamily: (family: string | undefined) => void;
  onProblems: (value: boolean) => void;
  onQuery: (value: string) => void;
  onMore: (rows: number) => void;
  onOpen: (key: string) => void;
}) {
  const shown = model.rows.slice(0, rows);
  const fieldIds = model.metricIds.filter((id) => id.startsWith("field."));
  const columnIds = model.metricIds.filter((id) => !id.startsWith("field."));
  return (
    <section aria-labelledby="heatmap-title" className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="heatmap-title" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Columns × metrics
          <InfoHint concept="eval:heatmap" />
        </h2>
        <span className="text-xs text-text-3">
          {model.rows.length.toLocaleString("en-US")} of {model.total.toLocaleString("en-US")} columns · worst first ·
          open a column for its drawer
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Heatmap filters">
        <ToggleGroup
          type="single"
          value={family ?? "all"}
          onValueChange={(value) => value && onFamily(value === "all" ? undefined : value)}
          aria-label="Family"
          className="max-w-full overflow-x-auto"
        >
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          {FAMILIES.map((f) => (
            <ToggleGroupItem key={f} value={f}>
              {FAMILY_LABEL[f]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="flex items-center gap-2">
          <Switch id="heatmap-problems" checked={problemsOnly} onCheckedChange={onProblems} />
          <label htmlFor="heatmap-problems" className="text-sm text-text-2">
            Problems only
          </label>
        </div>
        <label className="relative flex min-w-48 flex-1 items-center sm:max-w-72">
          <span className="sr-only">Find a column</span>
          <Search className="pointer-events-none absolute left-2.5 size-4 text-text-3" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="Find a column…"
            className="h-9 w-full rounded-md border border-control-border bg-surface-1 pr-3 pl-8 text-sm text-text-1 placeholder:text-text-3"
          />
        </label>
      </div>
      {shown.length ? (
        <TableContainer aria-label="Column by metric heatmap" className="max-h-[70vh]">
          <Table className="w-auto min-w-full">
            <caption className="sr-only">
              Columns by metric: each cell shows the status and the raw value; rows are sorted worst first.
            </caption>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead scope="col" rowSpan={2} className="sticky left-0 z-20 bg-surface-2 align-bottom">
                  Column
                </TableHead>
                {fieldIds.length ? (
                  <TableHead scope="colgroup" colSpan={fieldIds.length} className="h-7 border-l border-border">
                    <LevelChip level="field" size="sm" withHint />
                  </TableHead>
                ) : null}
                {columnIds.length ? (
                  <TableHead scope="colgroup" colSpan={columnIds.length} className="h-7 border-l border-border">
                    <LevelChip level="column" size="sm" withHint />
                  </TableHead>
                ) : null}
              </TableRow>
              <TableRow className="hover:bg-transparent">
                {model.metricIds.map((id, index) => {
                  const concept = metricConcept(id);
                  return (
                    <TableHead
                      key={id}
                      scope="col"
                      className={cn(
                        "h-auto px-1.5 py-1 align-bottom",
                        (index === 0 || index === fieldIds.length) && "border-l border-border",
                      )}
                    >
                      <span className="flex items-center justify-end gap-0.5 text-[11px] whitespace-nowrap">
                        <span title={metricTitle(id)}>{metricShort(id)}</span>
                        {concept ? <InfoHint concept={concept} /> : null}
                      </span>
                    </TableHead>
                  );
                })}
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((summary) => (
                <HeatRow
                  key={summary.key}
                  summary={summary}
                  metricIds={model.metricIds}
                  family={family}
                  onOpen={onOpen}
                />
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      ) : (
        <p className="rounded-md border border-dashed border-border-strong p-6 text-center text-sm text-text-2">
          No column matches these filters.
        </p>
      )}
      {model.rows.length > shown.length ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-3">
          <span>
            Showing {shown.length.toLocaleString("en-US")} of {model.rows.length.toLocaleString("en-US")} columns.
          </span>
          <Button size="sm" variant="secondary" onClick={() => onMore(rows + HEATMAP_PAGE)}>
            Show {Math.min(HEATMAP_PAGE, model.rows.length - shown.length)} more
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onMore(model.rows.length)}>
            Show all
          </Button>
        </div>
      ) : null}
      <p className="text-[11px] text-text-3">
        ✓ pass · ! warn · ✕ fail · i info · – not evaluated (n/e) · · not applicable to this column kind. Values are raw
        (not scores); the header (i) gives each metric&apos;s thresholds and reading.
      </p>
    </section>
  );
}
