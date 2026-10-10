/**
 * ChartFrame — every 2-D chart in the app goes through it.
 *
 *   <ChartFrame
 *     title="Order totals: source vs synthetic"
 *     concept="metric:column.ks"
 *     option={{ xAxis: { type: "category" }, yAxis: { type: "value" },
 *               series: [{ type: "bar", encode: { x: "bucket", y: "source" } }] }}
 *     data={rows}                       // also the "View data" table
 *     empty={{ when: metric.value === null, message: "Not evaluated: reference unverified." }}
 *   />
 *
 * - ECharts is a lazy chunk (`echarts/core` + the registered charts and
 *   components in EChartCanvas.tsx); the frame shows a skeleton meanwhile.
 * - Colours, text and grid come from the design tokens (echartsTheme.ts) and
 *   follow the theme toggle. Categorical series take `--chart-1..8` in order.
 * - When `option.dataset` is absent, `data` is passed as `dataset.source`, so
 *   series can `encode` columns by name.
 * - "View data" swaps the chart for DataTableFallback (the WCAG twin).
 * - `empty.when`, or `data.length === 0`, renders a labelled empty state
 *   instead of blank axes.
 * - Animation is off under prefers-reduced-motion.
 * - Interaction: `onEvents` maps ECharts event names ("click", "legendselectchanged",
 *   "brushselected", "datazoom" …) to handlers, bound with chart.on/off and
 *   rebound when the map's identity changes; `onReady` hands over the chart
 *   instance once, after the first render (dispatchAction, getDataURL …).
 * - Memoise `option`, `data` and `onEvents` (useMemo/useCallback): every
 *   render with a new `option` re-renders the chart from scratch (notMerge).
 *   An option that is a new object with the same top-level parts is skipped.
 */
import type { EChartsOption, EChartsType } from "echarts";
import { ChartColumn, ChartNoAxesColumn, Table2 } from "lucide-react";
import { lazy, Suspense, useId, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";

import { DataTableFallback, type DataColumn } from "./DataTableFallback";
import { EmptyState } from "./EmptyState";
import { InfoHint } from "./InfoHint";
import { Button } from "./ui/button";
import { Skeleton } from "./ui/skeleton";

const EChartCanvas = lazy(() => import("./EChartCanvas"));

/** ECharts event name → handler. `params` is ECharts' event payload (e.g. { dataIndex, seriesName, value } on "click"). */
export type ChartEventHandlers = Record<string, (params: unknown) => void>;

export type ChartFrameProps = {
  title: string;
  /** Concept id for the (i) next to the title. */
  concept?: string;
  option: EChartsOption;
  /** The plotted rows; also the "View data" table. */
  data: Array<Record<string, unknown>>;
  /** Plot height in px, axis labels included (default 280). */
  height?: number;
  /** Renders `message` instead of the chart when `when` is true. */
  empty?: { when: boolean; message: string };
  /** One line under the title (what is plotted, units). */
  description?: ReactNode;
  /** Extra controls in the header (a ToggleGroup, a legend switch). */
  actions?: ReactNode;
  /** Column order/format for the table view. */
  columns?: readonly DataColumn[];
  /** ECharts events → handlers ("click", "legendselectchanged" …). Memoise the map. */
  onEvents?: ChartEventHandlers;
  /** Called once with the chart instance after its first render. */
  onReady?: (chart: EChartsType) => void;
  /** Under the chart (and the table): a note, a legend for bands, a Callout ("Docs differ"). */
  footer?: ReactNode;
  className?: string;
};

export function ChartFrame({
  title,
  concept,
  option,
  data,
  height = 280,
  empty,
  description,
  actions,
  columns,
  onEvents,
  onReady,
  footer,
  className,
}: ChartFrameProps) {
  const [showTable, setShowTable] = useState(false);
  const titleId = useId();
  const bodyId = useId();
  const isEmpty = (empty?.when ?? false) || data.length === 0;
  const emptyMessage = empty?.when ? empty.message : "No data to plot.";

  return (
    <figure
      aria-labelledby={titleId}
      data-slot="chart-frame"
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
        {isEmpty ? null : (
          <Button variant="ghost" size="sm" aria-controls={bodyId} onClick={() => setShowTable((v) => !v)}>
            {showTable ? <ChartColumn aria-hidden="true" /> : <Table2 aria-hidden="true" />}
            {showTable ? "View chart" : "View data"}
          </Button>
        )}
      </figcaption>
      <div id={bodyId} style={{ minHeight: isEmpty || showTable ? undefined : height }}>
        {isEmpty ? (
          <EmptyState compact headingLevel="none" icon={ChartNoAxesColumn} title={emptyMessage} className="min-h-32" />
        ) : showTable ? (
          <DataTableFallback caption={title} rows={data} columns={columns} />
        ) : (
          <Suspense fallback={<Skeleton style={{ height }} className="w-full" />}>
            <EChartCanvas
              option={option}
              data={data}
              height={height}
              ariaLabel={`${title}. Chart; choose View data for the values.`}
              onEvents={onEvents}
              onReady={onReady}
            />
          </Suspense>
        )}
      </div>
      {footer}
    </figure>
  );
}
