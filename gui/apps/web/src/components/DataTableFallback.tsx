import { useMemo } from "react";

import { cn } from "@/lib/cn";
import { formatCell, MISSING } from "@/lib/format";

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/table";

export type DataColumn<Row extends Record<string, unknown> = Record<string, unknown>> = {
  key: string;
  /** Header text; defaults to the key. */
  label?: string;
  /** Cell text; defaults to `formatCell` (numbers en-US, empty → "—"). */
  format?: (value: unknown, row: Row) => string;
  align?: "left" | "right";
};

export type DataTableFallbackProps<Row extends Record<string, unknown> = Record<string, unknown>> = {
  /** Names the table (the chart title); announced as the table's caption. */
  caption: string;
  rows: readonly Row[];
  /** Column order and formatting; defaults to every key seen in the first 200 rows. */
  columns?: readonly DataColumn<Row>[];
  /** Rows rendered before truncating with a note (default 500). */
  maxRows?: number;
  className?: string;
};

function inferColumns<Row extends Record<string, unknown>>(rows: readonly Row[]): DataColumn<Row>[] {
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const row of rows.slice(0, 200)) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        keys.push(key);
      }
    }
  }
  return keys.map((key) => ({
    key,
    align: rows.slice(0, 50).some((row) => typeof row[key] === "number") ? "right" : "left",
  }));
}

/**
 * The accessible twin of a chart: every plotted value as a real table.
 * ChartFrame renders it behind "View data"; use it directly wherever a
 * visual encodes data (3-D views, SVG diagrams with values).
 */
export function DataTableFallback<Row extends Record<string, unknown>>({
  caption,
  rows,
  columns,
  maxRows = 500,
  className,
}: DataTableFallbackProps<Row>) {
  const cols = useMemo(() => columns ?? inferColumns(rows), [columns, rows]);
  const shown = rows.slice(0, maxRows);
  const truncated = rows.length - shown.length;

  return (
    <TableContainer aria-label={`${caption} (data table)`} className={cn("max-h-[26rem]", className)}>
      <Table>
        <TableCaption className="sr-only">{`${caption} — data table, ${rows.length} rows`}</TableCaption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {cols.map((col) => (
              <TableHead key={col.key} scope="col" className={cn("font-mono", col.align === "right" && "text-right")}>
                {col.label ?? col.key}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((row, index) => (
            <TableRow key={index}>
              {cols.map((col) => {
                const text = col.format ? col.format(row[col.key], row) : formatCell(row[col.key]);
                return (
                  <TableCell
                    key={col.key}
                    className={cn("whitespace-nowrap", col.align === "right" && "text-right tabular-nums")}
                  >
                    {text === MISSING ? (
                      <>
                        <span aria-hidden="true" className="text-text-3">
                          {MISSING}
                        </span>
                        <span className="sr-only">empty</span>
                      </>
                    ) : (
                      text
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {truncated > 0 ? (
        <p className="border-t border-border px-3 py-2 text-xs text-text-3">
          Showing the first {shown.length.toLocaleString("en-US")} of {rows.length.toLocaleString("en-US")} rows.
        </p>
      ) : null}
    </TableContainer>
  );
}
