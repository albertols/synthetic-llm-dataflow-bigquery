/**
 * VisualFrame — ChartFrame's twin for the visuals this tab draws itself
 * (HTML/SVG: the metric forest, the model graph, interval plots, the
 * heatmap): a titled figure with an (i), a description, a "View data"
 * toggle to the DataTableFallback, and a labelled empty state.
 */
import { ChartColumn, ChartNoAxesColumn, Table2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import type { DataColumn } from "@/components/DataTableFallback";
import { DataTableFallback } from "@/components/DataTableFallback";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export type VisualFrameProps = {
  title: string;
  concept?: string;
  description?: ReactNode;
  actions?: ReactNode;
  /** The table twin. */
  data: Array<Record<string, unknown>>;
  columns?: readonly DataColumn[];
  empty?: { when: boolean; message: string };
  footer?: ReactNode;
  className?: string;
  children: ReactNode;
};

export function VisualFrame({
  title,
  concept,
  description,
  actions,
  data,
  columns,
  empty,
  footer,
  className,
  children,
}: VisualFrameProps) {
  const [showTable, setShowTable] = useState(false);
  const titleId = useId();
  const bodyId = useId();
  const isEmpty = empty?.when ?? false;
  return (
    <figure
      aria-labelledby={titleId}
      data-slot="visual-frame"
      className={cn("m-0 flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface-1 p-4", className)}
    >
      <figcaption className="flex flex-wrap items-start gap-x-2 gap-y-1">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-1">
            <span id={titleId} className="text-sm leading-tight font-semibold text-text-1">
              {title}
            </span>
            {concept ? <InfoHint concept={concept} /> : null}
          </div>
          {description ? <span className="text-xs text-text-3">{description}</span> : null}
        </div>
        {actions}
        {isEmpty || !data.length ? null : (
          <Button variant="ghost" size="sm" aria-controls={bodyId} onClick={() => setShowTable((v) => !v)}>
            {showTable ? <ChartColumn aria-hidden="true" /> : <Table2 aria-hidden="true" />}
            {showTable ? "View chart" : "View data"}
          </Button>
        )}
      </figcaption>
      <div id={bodyId} className="min-w-0">
        {isEmpty ? (
          <EmptyState
            compact
            headingLevel="none"
            icon={ChartNoAxesColumn}
            title={empty?.message ?? "No data."}
            className="min-h-32"
          />
        ) : showTable ? (
          <DataTableFallback caption={title} rows={data} columns={columns} />
        ) : (
          children
        )}
      </div>
      {footer}
    </figure>
  );
}
