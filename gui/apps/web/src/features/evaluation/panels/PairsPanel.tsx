/**
 * Pairs: joint structure. Per table, the correlation matrices of the source
 * and the synthetic sample and their difference (small multiples on one
 * diverging scale), every pair metric in a status table, and the
 * contingency tables of the most divergent categorical pairs as Δ maps.
 */
import type { EChartsOption } from "echarts";
import { useMemo } from "react";

import type { MetricRow, ProfileRow } from "@contracts/api";
import { parseProfile, type ContingencyPayload } from "@contracts/payloads";

import { ChartFrame } from "@/components/ChartFrame";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableContainer, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProfiles } from "@/lib/api";
import { formatCount } from "@/lib/format";

import { MetricReadout } from "../components/MetricReadout";
import { metricConcept, metricShort } from "../lib/catalogue";
import { alignCorr, corrHeatmap } from "../lib/charts";
import { fmtMetric } from "../lib/format";
import { pairRows, statusRank } from "../lib/model";
import { reasonOf } from "../lib/reading";
import { useChartTokens, type ChartTokens } from "../lib/tokens";

const PAIR_ORDER = [
  "pair.pearson_delta",
  "pair.spearman_delta",
  "pair.cramers_v_delta",
  "pair.nmi_delta",
  "pair.contingency_tvd",
];
const GLYPH: Record<string, string> = { pass: "✓", warn: "!", fail: "✕", info: "i", not_evaluated: "–" };

function CorrMatrices({ table, rows, tokens }: { table: string; rows: ProfileRow[]; tokens: ChartTokens }) {
  const specs = useMemo(() => {
    const source = rows.find((r) => r.profile_kind === "corr_matrix" && r.side === "source");
    const synthetic = rows.find((r) => r.profile_kind === "corr_matrix" && r.side === "synthetic");
    const s = source ? parseProfile("corr_matrix", source.payload) : null;
    const y = synthetic ? parseProfile("corr_matrix", synthetic.payload) : null;
    if (!s || !y) return null;
    const aligned = alignCorr(s, y);
    if (aligned.columns.length < 2) return null;
    const maxDelta = Math.max(0.1, ...aligned.delta.flat().map((v) => Math.abs(v ?? 0)));
    return {
      method: s.method,
      n: s.n,
      source: corrHeatmap(aligned.columns, aligned.source, tokens, { name: "source r" }),
      synthetic: corrHeatmap(aligned.columns, aligned.synthetic, tokens, { name: "synthetic r" }),
      delta: corrHeatmap(aligned.columns, aligned.delta, tokens, { name: "Δ r", range: Number(maxDelta.toFixed(2)) }),
      size: aligned.columns.length,
    };
  }, [rows, tokens]);
  if (!specs) return null;
  const height = Math.min(420, 120 + specs.size * 34);
  return (
    <div className="grid gap-3">
      <p className="text-xs text-text-3">
        {specs.method} correlations on a paired sample of n = {formatCount(specs.n)} per side. Same diverging scale for
        source and synthetic (−1 … +1); the Δ map is scaled to its own largest difference.
      </p>
      <div className="grid gap-4 lg:grid-cols-3">
        <ChartFrame
          title={`${table} · source`}
          concept="eval:correlation-delta"
          option={specs.source?.option ?? {}}
          data={specs.source?.data ?? []}
          height={height}
        />
        <ChartFrame
          title={`${table} · synthetic`}
          option={specs.synthetic?.option ?? {}}
          data={specs.synthetic?.data ?? []}
          height={height}
        />
        <ChartFrame
          title={`${table} · Δ (synthetic − source)`}
          option={specs.delta?.option ?? {}}
          data={specs.delta?.data ?? []}
          height={height}
        />
      </div>
    </div>
  );
}

/** The rectangular Δ map (synthetic − source share per cell) of one contingency table. */
function contingencyDelta(
  source: ContingencyPayload,
  synthetic: ContingencyPayload,
  tokens: ChartTokens,
): { data: Array<Record<string, unknown>>; option: EChartsOption } {
  const xs = [...new Set([...source.x_labels, ...synthetic.x_labels])];
  const ys = [...new Set([...source.y_labels, ...synthetic.y_labels])];
  const total = (p: ContingencyPayload) => p.counts.flat().reduce((a, b) => a + b, 0) || 1;
  const ts = total(source);
  const ty = total(synthetic);
  const share = (p: ContingencyPayload, t: number, x: string, y: string) =>
    (p.counts[p.x_labels.indexOf(x)]?.[p.y_labels.indexOf(y)] ?? 0) / t;
  const cells: [number, number, number][] = [];
  const data: Array<Record<string, unknown>> = [];
  let max = 0.01;
  xs.forEach((x, i) =>
    ys.forEach((y, j) => {
      const s = share(source, ts, x, y);
      const q = share(synthetic, ty, x, y);
      const d = q - s;
      max = Math.max(max, Math.abs(d));
      cells.push([j, i, Number(d.toFixed(5))]);
      data.push({
        [source.column_x]: x,
        [source.column_y]: y,
        source_share: Number(s.toFixed(5)),
        synthetic_share: Number(q.toFixed(5)),
        delta: Number(d.toFixed(5)),
      });
    }),
  );
  return {
    data,
    option: {
      dataset: [],
      grid: { left: 8, right: 8, top: 8, bottom: 48, containLabel: true },
      tooltip: {
        trigger: "item" as const,
        formatter: (params: unknown) => {
          const { data: d } = params as { data?: [number, number, number] };
          return d ? `${xs[d[1]] ?? ""} × ${ys[d[0]] ?? ""}: Δ share ${(d[2] * 100).toFixed(2)} pp` : "";
        },
      },
      xAxis: {
        type: "category" as const,
        data: ys,
        name: source.column_y,
        nameLocation: "middle" as const,
        nameGap: 28,
        axisLabel: { hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: { type: "category" as const, data: xs, name: source.column_x, splitLine: { show: false } },
      visualMap: {
        min: -max,
        max,
        orient: "horizontal" as const,
        left: "center",
        bottom: 0,
        itemHeight: 120,
        itemWidth: 10,
        text: ["over-produced", "under-produced"],
        inRange: { color: [tokens.divNeg, tokens.divMid, tokens.divPos] },
      },
      series: [
        {
          type: "heatmap" as const,
          data: cells,
          itemStyle: { borderColor: tokens.surface, borderWidth: 2 },
          label: {
            show: xs.length * ys.length <= 30,
            formatter: (params: unknown) => {
              const { data: d } = params as { data?: [number, number, number] };
              return d ? `${d[2] >= 0 ? "+" : ""}${(d[2] * 100).toFixed(1)}` : "";
            },
            color: tokens.text1,
            fontSize: 10,
          },
        },
      ],
    },
  };
}

function Contingencies({
  table,
  rows,
  pairs,
  tokens,
}: {
  table: string;
  rows: ProfileRow[];
  pairs: MetricRow[];
  tokens: ChartTokens;
}) {
  const top = useMemo(() => {
    const tvd = pairs
      .filter((m) => m.metric_id === "pair.contingency_tvd" && m.value !== null)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
      .slice(0, 3);
    return tvd.map((metric) => {
      const match = (side: string) =>
        rows
          .filter((r) => r.profile_kind === "contingency" && r.side === side)
          .map((r) => parseProfile("contingency", r.payload))
          .find(
            (p) =>
              p &&
              ((p.column_x === metric.column_name && p.column_y === metric.column_name_2) ||
                (p.column_x === metric.column_name_2 && p.column_y === metric.column_name)),
          );
      const s = match("source");
      const y = match("synthetic");
      return { metric, spec: s && y ? contingencyDelta(s, y, tokens) : null };
    });
  }, [pairs, rows, tokens]);
  if (!top.length) return null;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {top.map(({ metric, spec }) => (
        <ChartFrame
          key={`${metric.column_name}-${metric.column_name_2}`}
          title={`${table}: ${metric.column_name} × ${metric.column_name_2}`}
          concept="eval:contingency"
          description={`Contingency TV ${fmtMetric(metric.value, "distance")} (${metric.status}). Cells: synthetic − source share, in percentage points.`}
          option={spec?.option ?? {}}
          data={spec?.data ?? []}
          empty={{ when: !spec, message: "Contingency profiles unavailable for this pair." }}
          height={300}
        />
      ))}
    </div>
  );
}

function PairTable({ pairs }: { pairs: MetricRow[] }) {
  const groups = useMemo(() => {
    const map = new Map<string, Map<string, MetricRow>>();
    for (const m of pairs) {
      const key = `${m.table_name}: ${m.column_name} × ${m.column_name_2}`;
      const g = map.get(key) ?? new Map<string, MetricRow>();
      g.set(m.metric_id, m);
      map.set(key, g);
    }
    return [...map.entries()].sort(
      ([, a], [, b]) =>
        Math.min(...[...a.values()].map((m) => statusRank(m.status))) -
        Math.min(...[...b.values()].map((m) => statusRank(m.status))),
    );
  }, [pairs]);
  const ids = PAIR_ORDER.filter((id) => pairs.some((p) => p.metric_id === id));
  return (
    <TableContainer aria-label="Pair metrics" className="max-h-[28rem]">
      <Table className="w-auto min-w-full">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead scope="col" className="sticky left-0 bg-surface-2">
              Pair
            </TableHead>
            {ids.map((id) => {
              const concept = metricConcept(id);
              return (
                <TableHead key={id} scope="col" className="text-right">
                  <span className="inline-flex items-center gap-0.5">
                    {metricShort(id)}
                    {concept ? <InfoHint concept={concept} /> : null}
                  </span>
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map(([key, metrics]) => (
            <TableRow key={key}>
              <th
                scope="row"
                className="sticky left-0 bg-surface-1 px-3 py-1.5 text-left font-mono text-xs font-normal whitespace-nowrap text-text-1"
              >
                {key}
              </th>
              {ids.map((id) => {
                const m = metrics.get(id);
                if (!m)
                  return (
                    <TableCell key={id} className="text-center text-text-3">
                      ·
                    </TableCell>
                  );
                const reason = m.status === "not_evaluated" ? reasonOf(m) : null;
                return (
                  <TableCell
                    key={id}
                    className="text-right font-mono text-xs whitespace-nowrap tabular-nums"
                    data-status={m.status}
                    title={reason ?? undefined}
                  >
                    <span aria-hidden="true" className="mr-1 text-text-2">
                      {GLYPH[m.status] ?? "?"}
                    </span>
                    <span className="sr-only">{m.status}, </span>
                    {m.value === null ? "n/e" : fmtMetric(m.value, m.value_kind)}
                    {m.noise_floor !== null && m.value !== null && m.value <= m.noise_floor ? (
                      <span className="ml-1 text-text-3">≈</span>
                    ) : null}
                    {reason ? <span className="sr-only"> ({reason})</span> : null}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export function PairsPanel({
  evaluationId,
  metrics,
  table,
}: {
  evaluationId: string;
  metrics: MetricRow[];
  table?: string;
}) {
  const tokens = useChartTokens();
  const profiles = useProfiles(evaluationId, { table, kind: ["corr_matrix", "contingency"] });
  const pairs = useMemo(() => pairRows(metrics, table), [metrics, table]);
  const tables = useMemo(() => [...new Set(pairs.map((p) => p.table_name))].sort(), [pairs]);
  const tableMetrics = metrics.filter(
    (m) =>
      (m.metric_id === "table.corr_rms_delta" || m.metric_id === "table.corr_max_delta") &&
      (!table || m.table_name === table),
  );
  const notEvaluated = pairs.filter((p) => p.status === "not_evaluated");
  if (!pairs.length && !tableMetrics.length)
    return (
      <EmptyState
        title="No pair metrics"
        description="This evaluation profiled no column pairs (one-column tables, or pairs above pair_max_columns)."
      />
    );
  return (
    <div className="grid gap-6">
      <section aria-labelledby="pairs-summary" className="grid gap-3">
        <h2 id="pairs-summary" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Joint structure
          <InfoHint concept="core:level-pair" />
        </h2>
        {tableMetrics.length ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {tableMetrics.map((m) => (
              <MetricReadout key={`${m.metric_id}-${m.table_name}`} row={m} />
            ))}
          </div>
        ) : null}
        {notEvaluated.length ? (
          <p className="text-xs text-text-2">
            {notEvaluated.length} pair metric{notEvaluated.length === 1 ? "" : "s"} not evaluated:{" "}
            {[...new Set(notEvaluated.map((m) => reasonOf(m) ?? "no reason recorded"))].join(" · ")}
          </p>
        ) : null}
        {pairs.length ? <PairTable pairs={pairs} /> : null}
        <p className="text-[11px] text-text-3">≈ marks a difference at or below its noise floor.</p>
      </section>
      <section aria-labelledby="pairs-corr" className="grid gap-4">
        <h2 id="pairs-corr" className="flex items-center gap-0.5 text-lg font-semibold tracking-tight text-text-1">
          Correlation matrices <InfoHint concept="eval:correlation-delta" />
        </h2>
        {profiles.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          tables.map((t) => (
            <div key={t} className="grid gap-4">
              <CorrMatrices
                table={t}
                rows={(profiles.data?.data ?? []).filter((r) => r.table_name === t)}
                tokens={tokens}
              />
              <Contingencies
                table={t}
                rows={(profiles.data?.data ?? []).filter((r) => r.table_name === t)}
                pairs={pairs.filter((p) => p.table_name === t)}
                tokens={tokens}
              />
            </div>
          ))
        )}
        {!profiles.isPending && !(profiles.data?.data ?? []).length ? (
          <EmptyState
            compact
            headingLevel={3}
            title="No correlation or contingency profiles stored"
            description="The pair table above still carries every pair metric."
          />
        ) : null}
      </section>
    </div>
  );
}
