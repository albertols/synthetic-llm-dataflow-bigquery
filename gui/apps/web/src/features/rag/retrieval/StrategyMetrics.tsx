/**
 * Coverage, diversity and redundancy of every strategy's seeds on the current
 * cloud, as three small multiples (one measure, one axis each), the current
 * strategy emphasised, and a table that doubles as the strategy picker.
 */
import type { EChartsOption } from "echarts";
import { useMemo } from "react";

import { ChartFrame } from "@/components/ChartFrame";
import { InfoHint } from "@/components/InfoHint";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatFixed } from "@/lib/format";
import { readToken, useTheme } from "@/lib/theme";

import { clustersReached } from "../lib/metrics";
import { CLUSTER_K } from "../lib/projection";
import { STRATEGIES, type StrategyId } from "../lib/strategies";
import type { StrategyRowData } from "../lib/strategyJob";

export interface StrategyRow extends StrategyRowData {
  /** How many of the 3 spherical k-means clusters the seeds land in (null until the labels arrive). */
  clusters: number | null;
}

/** The worker's rows (picks and metrics of every strategy) with cluster counts joined once the labels arrive. */
export function useStrategyRows(rows: readonly StrategyRowData[] | null, clusters: Int32Array | null) {
  return useMemo(
    (): StrategyRow[] => (rows ?? []).map((row) => ({ ...row, clusters: clustersReached(row.picks, clusters) })),
    [rows, clusters],
  );
}

type MetricKey = "coverage" | "diversity" | "redundancy";

const METRICS: { key: MetricKey; title: string; better: string; concept: string }[] = [
  {
    key: "coverage",
    title: "Coverage",
    better: "mean 1 − cos to the nearest seed · lower is better",
    concept: "rag:coverage",
  },
  {
    key: "diversity",
    title: "Diversity",
    better: "mean 1 − cos between seeds · higher is better",
    concept: "rag:diversity",
  },
  {
    key: "redundancy",
    title: "Redundancy",
    better: "mean cos of a seed to its closest fellow seed · lower is better",
    concept: "rag:redundancy",
  },
];

export function StrategyMetrics({
  rows,
  current,
  onPick,
}: {
  rows: readonly StrategyRow[];
  current: StrategyId;
  onPick: (id: StrategyId) => void;
}) {
  const { resolved } = useTheme();
  const data = useMemo(
    () =>
      rows.map((r) => ({
        strategy: STRATEGIES[r.id].label + (STRATEGIES[r.id].inPipeline ? "" : " *"),
        coverage: r.coverage,
        diversity: r.diversity,
        redundancy: r.redundancy,
      })),
    [rows],
  );
  const currentIndex = rows.findIndex((r) => r.id === current);
  const options = useMemo(() => {
    void resolved;
    const accent = readToken("--accent", "#eb6834");
    const other = readToken("--chart-other", "#6b7280");
    return Object.fromEntries(
      METRICS.map((m): [MetricKey, EChartsOption] => [
        m.key,
        {
          grid: { left: 118, right: 40, top: 8, bottom: 28 },
          xAxis: { type: "value", min: 0 },
          yAxis: { type: "category", inverse: true },
          tooltip: { trigger: "item", valueFormatter: (v) => formatFixed(v as number, 3) },
          series: [
            {
              type: "bar",
              encode: { y: "strategy", x: m.key },
              barMaxWidth: 16,
              itemStyle: {
                borderRadius: [0, 4, 4, 0],
                color: (params: { dataIndex: number }) => (params.dataIndex === currentIndex ? accent : other),
              },
              label: {
                show: true,
                position: "right",
                formatter: (params: { value: unknown }) => {
                  const row = params.value as Record<string, number | null>;
                  return formatFixed(row[m.key] ?? null, 3);
                },
              },
            },
          ],
        },
      ]),
    ) as Record<MetricKey, EChartsOption>;
  }, [resolved, currentIndex]);

  return (
    <div className="grid gap-4">
      <div
        className="relative min-w-0 overflow-x-auto rounded-lg border border-border"
        role="region"
        aria-label="Strategies compared"
        tabIndex={0}
      >
        <table className="w-full min-w-[44rem] text-left text-xs">
          <caption className="sr-only">
            Seed strategies on the current cloud: truth label, coverage, diversity, redundancy, clusters reached
          </caption>
          <thead className="bg-surface-2 text-text-3">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                Strategy
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Status
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Coverage ↓
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Diversity ↑
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Redundancy ↓
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                <span className="inline-flex items-center gap-1">
                  Clusters reached
                  <InfoHint concept="rag:clusters" />
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const info = STRATEGIES[r.id];
              const on = r.id === current;
              return (
                <tr key={r.id} className={cn("border-t border-border", on && "bg-accent-soft")}>
                  <th scope="row" className="px-3 py-2 font-medium text-text-1">
                    <button
                      type="button"
                      aria-pressed={on}
                      onClick={() => onPick(r.id)}
                      className="inline-flex min-h-6 items-center gap-2 rounded-sm text-left hover:text-accent-text focus-visible:outline-2 focus-visible:outline-focus-ring"
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "inline-block size-3 rounded-full border-2",
                          on ? "border-accent bg-accent" : "border-control-border",
                        )}
                      />
                      {info.label}
                    </button>
                    <InfoHint concept={info.concept} />
                  </th>
                  <td className="px-3 py-2">
                    {info.inPipeline ? (
                      <Badge variant="cpu">exact port</Badge>
                    ) : (
                      <Badge variant="outline">teaching contrast — not in pipeline</Badge>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">
                    {formatFixed(r.coverage, 3)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">
                    {formatFixed(r.diversity, 3)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">
                    {formatFixed(r.redundancy, 3)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-text-1 tabular-nums">
                    {r.clusters === null ? "—" : `${r.clusters} of ${CLUSTER_K}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {METRICS.map((m) => (
          <ChartFrame
            key={m.key}
            title={m.title}
            concept={m.concept}
            description={m.better}
            option={options[m.key]}
            data={data}
            height={200}
            columns={[
              { key: "strategy", label: "Strategy (* teaching only)" },
              { key: m.key, label: m.title, align: "right", format: (v) => formatFixed(v as number | null, 3) },
            ]}
          />
        ))}
      </div>
      <p className="text-xs text-text-3">
        * Teaching contrast — not in the pipeline. All five are measured in the full embedding space with cosine
        distance, never on the projection. The emphasised bar is the strategy selected above.
      </p>
    </div>
  );
}
