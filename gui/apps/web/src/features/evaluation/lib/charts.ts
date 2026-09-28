/**
 * ECharts option builders for the EVALUATION views. Pure: payloads in, an
 * option plus the rows of its "View data" table out (null when the inputs
 * cannot make an honest chart — the caller shows a labelled empty state).
 *
 * Colour roles (dataviz): source = slot 1, synthetic = slot 2, the reference
 * sample / holdout = slot 3 (fixed, never cycled); status colours only where
 * the mark means status; bands in slate; thin marks, hairline grid.
 */
import type { EChartsOption } from "echarts";

import type {
  CorrMatrixPayload,
  DistanceHistPayload,
  FanoutHistPayload,
  HistogramPayload,
  QuantilesPayload,
  RocCurvePayload,
  TopkPayload,
} from "@contracts/payloads";
import { binormalRoc } from "@synthetic-platform/stats/intervals";

import { formatDate, isFiniteNumber } from "@/lib/format";

import { fmtShare, fmtSig } from "./format";
import type { ChartTokens } from "./tokens";

export interface ChartSpec {
  option: EChartsOption;
  data: Array<Record<string, unknown>>;
}

const sum = (xs: readonly number[]) => xs.reduce((a, b) => a + b, 0);
const shares = (counts: readonly number[]) => {
  const total = sum(counts);
  return counts.map((c) => (total > 0 ? c / total : 0));
};
const round = (x: number, digits = 6) => Number(x.toFixed(digits));

export const SIDE_NAME = { source: "Source", synthetic: "Synthetic", reference: "Reference sample (R)" } as const;

function axisLabel(value: number, unit: "value" | "epoch_seconds"): string {
  return unit === "epoch_seconds" ? formatDate(value * 1000) : fmtSig(value);
}

/** "≤ e0", "e0–e1", …, "> e_last" for the evaluator's interior edges. */
export function binLabels(edges: readonly number[], unit: "value" | "epoch_seconds"): string[] {
  if (!edges.length) return ["all"];
  const f = (v: number) => axisLabel(v, unit);
  return [`≤ ${f(edges[0]!)}`, ...edges.slice(1).map((e, i) => `${f(edges[i]!)}–${f(e)}`), `> ${f(edges.at(-1)!)}`];
}

const sameEdges = (a: readonly number[], b: readonly number[]) =>
  a.length === b.length && a.every((v, i) => Math.abs(v - b[i]!) <= 1e-9 * Math.max(1, Math.abs(v)));

const percentAxis = { type: "value" as const, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%` } };

// -------------------------------------------------------------- histogram --

export function histogramOverlay(
  sides: { source?: HistogramPayload | null; synthetic?: HistogramPayload | null; reference?: HistogramPayload | null },
  tokens: ChartTokens,
): ChartSpec | null {
  const { source, synthetic, reference } = sides;
  if (!source || !synthetic) return null;
  if (!sameEdges(source.edges, synthetic.edges) || source.counts.length !== synthetic.counts.length) return null;
  const labels = binLabels(source.edges, source.unit);
  const ps = shares(source.counts);
  const py = shares(synthetic.counts);
  const refOk =
    reference && sameEdges(reference.edges, source.edges) && reference.counts.length === source.counts.length;
  const pr = refOk ? shares(reference.counts) : null;
  const data = labels.map((bin, i) => ({
    bin,
    source: round(ps[i] ?? 0),
    synthetic: round(py[i] ?? 0),
    ...(pr ? { reference: round(pr[i] ?? 0) } : {}),
  }));
  const series: NonNullable<EChartsOption["series"]> = [
    { type: "bar", name: SIDE_NAME.source, encode: { x: "bin", y: "source" }, itemStyle: { color: tokens.slots[0] } },
    {
      type: "bar",
      name: SIDE_NAME.synthetic,
      encode: { x: "bin", y: "synthetic" },
      itemStyle: { color: tokens.slots[1] },
    },
  ];
  if (pr)
    series.push({
      type: "line",
      name: SIDE_NAME.reference,
      encode: { x: "bin", y: "reference" },
      showSymbol: true,
      symbolSize: 7,
      lineStyle: { color: tokens.slots[2], width: 2 },
      itemStyle: { color: tokens.slots[2], borderColor: tokens.surface, borderWidth: 2 },
    });
  return {
    data,
    option: {
      legend: { top: 0, left: 0 },
      grid: { left: 8, right: 12, top: 36, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => fmtShare(Number(v)) },
      xAxis: { type: "category", axisLabel: { hideOverlap: true, rotate: labels.length > 8 ? 30 : 0 } },
      yAxis: percentAxis,
      series,
    },
  };
}

// ------------------------------------------------------------------- ECDF --

export interface EcdfRow extends Record<string, unknown> {
  x: number;
  label: string;
  source: number;
  synthetic: number;
  band_low: number | null;
  band_high: number | null;
}

/** CDF values at each interior edge (the only points binned counts pin down exactly). */
export function ecdfRows(
  source: HistogramPayload,
  synthetic: HistogramPayload,
  noiseFloor: number | null,
): EcdfRow[] | null {
  if (!sameEdges(source.edges, synthetic.edges) || !source.edges.length) return null;
  const ps = shares(source.counts);
  const py = shares(synthetic.counts);
  if (!sum(source.counts) || !sum(synthetic.counts)) return null;
  let fs = 0;
  let fy = 0;
  return source.edges.map((edge, i) => {
    fs += ps[i] ?? 0;
    fy += py[i] ?? 0;
    return {
      x: edge,
      label: axisLabel(edge, source.unit),
      source: round(fs),
      synthetic: round(fy),
      band_low: noiseFloor === null ? null : round(Math.max(0, fs - noiseFloor)),
      band_high: noiseFloor === null ? null : round(Math.min(1, fs + noiseFloor)),
    };
  });
}

/** The edge with the largest CDF gap: where the binned KS lower bound d_lo sits. */
export function ksGap(rows: readonly EcdfRow[]): { index: number; gap: number } | null {
  let best: { index: number; gap: number } | null = null;
  rows.forEach((row, index) => {
    const gap = Math.abs(row.source - row.synthetic);
    if (!best || gap > best.gap) best = { index, gap };
  });
  return best;
}

export function ecdfChart(
  source: HistogramPayload | null | undefined,
  synthetic: HistogramPayload | null | undefined,
  noiseFloor: number | null,
  tokens: ChartTokens,
): (ChartSpec & { gap: { label: string; gap: number } | null }) | null {
  if (!source || !synthetic) return null;
  const rows = ecdfRows(source, synthetic, noiseFloor);
  if (!rows) return null;
  const gap = ksGap(rows);
  const gapRow = gap ? rows[gap.index] : undefined;
  const series: NonNullable<EChartsOption["series"]> = [];
  if (noiseFloor !== null) {
    series.push(
      {
        type: "line",
        name: "band-low",
        data: rows.map((r) => [r.x, r.band_low]),
        stack: "band",
        lineStyle: { opacity: 0 },
        symbol: "none",
        silent: true,
        tooltip: { show: false },
      },
      {
        type: "line",
        name: "Noise band (source ± floor)",
        data: rows.map((r) => [r.x, round((r.band_high ?? 0) - (r.band_low ?? 0))]),
        stack: "band",
        lineStyle: { opacity: 0 },
        areaStyle: { color: tokens.slate, opacity: 0.28 },
        symbol: "none",
        silent: true,
        tooltip: { show: false },
      },
    );
  }
  series.push(
    {
      type: "line",
      name: SIDE_NAME.source,
      data: rows.map((r) => [r.x, r.source]),
      step: "end",
      symbol: "none",
      lineStyle: { color: tokens.slots[0], width: 2 },
      itemStyle: { color: tokens.slots[0] },
    },
    {
      type: "line",
      name: SIDE_NAME.synthetic,
      data: rows.map((r) => [r.x, r.synthetic]),
      step: "end",
      symbol: "none",
      lineStyle: { color: tokens.slots[1], width: 2 },
      itemStyle: { color: tokens.slots[1] },
    },
  );
  if (gapRow && gap && gap.gap > 0)
    series.push({
      type: "line",
      name: "KS gap (d_lo)",
      data: [
        [gapRow.x, gapRow.source],
        [gapRow.x, gapRow.synthetic],
      ],
      symbol: "rect",
      symbolSize: [10, 2],
      lineStyle: { color: tokens.text1, width: 2 },
      itemStyle: { color: tokens.text1 },
      tooltip: { show: false },
    });
  const temporal = source.unit === "epoch_seconds";
  return {
    gap: gapRow && gap ? { label: gapRow.label, gap: gap.gap } : null,
    data: rows.map((r) => ({
      edge: r.label,
      source_cdf: r.source,
      synthetic_cdf: r.synthetic,
      band_low: r.band_low,
      band_high: r.band_high,
    })),
    option: {
      dataset: [],
      legend: {
        top: 0,
        left: 0,
        data: [
          SIDE_NAME.source,
          SIDE_NAME.synthetic,
          ...(noiseFloor !== null ? ["Noise band (source ± floor)"] : []),
          "KS gap (d_lo)",
        ],
      },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v) => fmtShare(Number(v)),
      },
      xAxis: {
        type: "value",
        scale: true,
        axisLabel: { formatter: (v: number) => axisLabel(v, temporal ? "epoch_seconds" : "value"), hideOverlap: true },
      },
      yAxis: { ...percentAxis, max: 1, min: 0 },
      series,
    },
  };
}

// --------------------------------------------------------------------- QQ --

export function qqChart(
  source: QuantilesPayload | null | undefined,
  synthetic: QuantilesPayload | null | undefined,
  tokens: ChartTokens,
): ChartSpec | null {
  if (!source || !synthetic || source.probs.length !== synthetic.probs.length || !source.probs.length) return null;
  const temporal = source.unit === "epoch_seconds";
  const points = source.probs.map((p, i) => ({ p, source: source.values[i]!, synthetic: synthetic.values[i]! }));
  const finitePoints = points.filter((pt) => isFiniteNumber(pt.source) && isFiniteNumber(pt.synthetic));
  if (!finitePoints.length) return null;
  const all = finitePoints.flatMap((pt) => [pt.source, pt.synthetic]);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const fmt = (v: number) => axisLabel(v, temporal ? "epoch_seconds" : "value");
  return {
    data: finitePoints.map((pt) => ({
      quantile: `p${Math.round(pt.p * 100)}`,
      source: fmt(pt.source),
      synthetic: fmt(pt.synthetic),
    })),
    option: {
      dataset: [],
      legend: { top: 0, left: 0, data: ["Quantile pairs", "y = x"] },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const { data } = params as { data?: [number, number, string] };
          return data ? `${data[2]}: source ${fmt(data[0])}, synthetic ${fmt(data[1])}` : "";
        },
      },
      xAxis: {
        type: "value",
        name: "source",
        nameLocation: "middle",
        nameGap: 26,
        scale: true,
        axisLabel: { formatter: fmt, hideOverlap: true },
      },
      yAxis: { type: "value", scale: true, axisLabel: { formatter: fmt } },
      series: [
        {
          type: "line",
          name: "y = x",
          data: [
            [lo, lo],
            [hi, hi],
          ],
          symbol: "none",
          lineStyle: { color: tokens.text3, width: 1 },
          silent: true,
          tooltip: { show: false },
        },
        {
          type: "scatter",
          name: "Quantile pairs",
          data: finitePoints.map((pt) => [pt.source, pt.synthetic, `p${Math.round(pt.p * 100)}`]),
          symbolSize: 8,
          itemStyle: { color: tokens.slots[1], borderColor: tokens.surface, borderWidth: 2 },
        },
      ],
    },
  };
}

// ----------------------------------------------------------- categoricals --

/** Paired horizontal bars (source vs synthetic share) over the union of both label sets, source order first. */
export function pairedBars(
  items: { label: string; source: number; synthetic: number }[],
  tokens: ChartTokens,
  options: { max?: number; categoryName?: string } = {},
): ChartSpec | null {
  if (!items.length) return null;
  const shown = items.slice(0, options.max ?? 12);
  const data = shown.map((item) => ({
    [options.categoryName ?? "label"]: item.label,
    source: round(item.source),
    synthetic: round(item.synthetic),
  }));
  const key = options.categoryName ?? "label";
  return {
    data,
    option: {
      legend: { top: 0, left: 0 },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => fmtShare(Number(v)) },
      yAxis: {
        type: "category",
        inverse: true,
        axisLabel: { width: 120, overflow: "truncate", fontFamily: "JetBrains Mono Variable, monospace" },
      },
      xAxis: percentAxis,
      series: [
        {
          type: "bar",
          name: SIDE_NAME.source,
          encode: { y: key, x: "source" },
          itemStyle: { color: tokens.slots[0], borderRadius: [0, 4, 4, 0] },
        },
        {
          type: "bar",
          name: SIDE_NAME.synthetic,
          encode: { y: key, x: "synthetic" },
          itemStyle: { color: tokens.slots[1], borderRadius: [0, 4, 4, 0] },
        },
      ],
    },
  };
}

export function topkItems(source: TopkPayload | null | undefined, synthetic: TopkPayload | null | undefined) {
  if (!source || !synthetic) return [];
  const syn = new Map(synthetic.items.map((i) => [i.label, i.share]));
  const src = new Map(source.items.map((i) => [i.label, i.share]));
  const labels = [
    ...source.items.map((i) => i.label),
    ...synthetic.items.map((i) => i.label).filter((l) => !src.has(l)),
  ];
  return labels.map((label) => ({ label, source: src.get(label) ?? 0, synthetic: syn.get(label) ?? 0 }));
}

// ------------------------------------------------------ distances, fan-out --

/** Two histograms on the same edges as paired columns (DCR / NNDR: syn→R vs H→R). */
export function pairedHistogram(
  a: { name: string; payload: DistanceHistPayload | null | undefined },
  b: { name: string; payload: DistanceHistPayload | null | undefined },
  tokens: ChartTokens,
): ChartSpec | null {
  if (!a.payload || !b.payload || !sameEdges(a.payload.edges, b.payload.edges)) return null;
  if (!sum(a.payload.counts) || !sum(b.payload.counts)) return null;
  const labels = binLabels(a.payload.edges, "value");
  const pa = shares(a.payload.counts);
  const pb = shares(b.payload.counts);
  return {
    data: labels.map((bin, i) => ({ distance: bin, [a.name]: round(pa[i] ?? 0), [b.name]: round(pb[i] ?? 0) })),
    option: {
      legend: { top: 0, left: 0 },
      grid: { left: 8, right: 12, top: 36, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => fmtShare(Number(v)) },
      xAxis: { type: "category", axisLabel: { hideOverlap: true, rotate: labels.length > 8 ? 30 : 0 } },
      yAxis: percentAxis,
      series: [
        { type: "bar", name: a.name, encode: { x: "distance", y: a.name }, itemStyle: { color: tokens.slots[1] } },
        { type: "bar", name: b.name, encode: { x: "distance", y: b.name }, itemStyle: { color: tokens.slots[2] } },
      ],
    },
  };
}

export function fanoutChart(
  source: FanoutHistPayload | null | undefined,
  synthetic: FanoutHistPayload | null | undefined,
  tokens: ChartTokens,
): ChartSpec | null {
  if (!source || !synthetic) return null;
  const values = [...new Set([...source.fanout, ...synthetic.fanout])].sort((a, b) => a - b);
  if (!values.length) return null;
  const share = (p: FanoutHistPayload) => {
    const total = sum(p.counts);
    const map = new Map(p.fanout.map((f, i) => [f, total ? (p.counts[i] ?? 0) / total : 0]));
    return (v: number) => map.get(v) ?? 0;
  };
  const s = share(source);
  const y = share(synthetic);
  const cap = Math.max(source.capped_at, synthetic.capped_at);
  const label = (v: number) => (v >= cap ? `≥ ${cap}` : String(v));
  return {
    data: values.map((v) => ({ children: label(v), source: round(s(v)), synthetic: round(y(v)) })),
    option: {
      legend: { top: 0, left: 0 },
      grid: { left: 8, right: 12, top: 36, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => fmtShare(Number(v)) },
      xAxis: { type: "category", name: "children per parent", nameLocation: "middle", nameGap: 26 },
      yAxis: percentAxis,
      series: [
        {
          type: "bar",
          name: SIDE_NAME.source,
          encode: { x: "children", y: "source" },
          itemStyle: { color: tokens.slots[0] },
        },
        {
          type: "bar",
          name: SIDE_NAME.synthetic,
          encode: { x: "children", y: "synthetic" },
          itemStyle: { color: tokens.slots[1] },
        },
      ],
    },
  };
}

// -------------------------------------------------------------------- ROC --

export function rocChart(
  roc: RocCurvePayload | null | undefined,
  ci: { low: number | null; high: number | null },
  tokens: ChartTokens,
): ChartSpec | null {
  if (!roc || roc.points.length < 2) return null;
  const low = ci.low ?? roc.ci_low;
  const high = ci.high ?? roc.ci_high;
  const series: NonNullable<EChartsOption["series"]> = [];
  const bandLabel = "AUC interval band (binormal)";
  if (low !== null && high !== null) {
    const lo = binormalRoc(low, 41);
    const hi = binormalRoc(high, 41);
    series.push(
      {
        type: "line",
        name: "band-low",
        data: lo,
        stack: "roc-band",
        symbol: "none",
        lineStyle: { opacity: 0 },
        silent: true,
        tooltip: { show: false },
      },
      {
        type: "line",
        name: bandLabel,
        data: lo.map(([x, y], i) => [x, round(Math.max(0, (hi[i]?.[1] ?? y) - y))]),
        stack: "roc-band",
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { color: tokens.slate, opacity: 0.3 },
        silent: true,
        tooltip: { show: false },
      },
    );
  }
  series.push(
    {
      type: "line",
      name: "Chance (AUC 0.5)",
      data: [
        [0, 0],
        [1, 1],
      ],
      symbol: "none",
      lineStyle: { color: tokens.text3, width: 1 },
      silent: true,
    },
    {
      type: "line",
      name: "ROC",
      data: roc.points.map(([x, y]) => [x, y]),
      symbol: "none",
      lineStyle: { color: tokens.slots[0], width: 2 },
      itemStyle: { color: tokens.slots[0] },
    },
  );
  return {
    data: roc.points.map(([fpr, tpr]) => ({ false_positive_rate: fpr, true_positive_rate: round(tpr, 4) })),
    option: {
      dataset: [],
      legend: {
        top: 0,
        left: 0,
        data: ["ROC", "Chance (AUC 0.5)", ...(low !== null && high !== null ? [bandLabel] : [])],
      },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", valueFormatter: (v) => fmtSig(Number(v)) },
      xAxis: { type: "value", min: 0, max: 1, name: "false positive rate", nameLocation: "middle", nameGap: 26 },
      yAxis: { type: "value", min: 0, max: 1, name: "true positive rate" },
      series,
    },
  };
}

// ---------------------------------------------------------- correlations --

export function corrHeatmap(
  columns: readonly string[],
  values: readonly (readonly (number | null)[])[],
  tokens: ChartTokens,
  options: { range?: number; name: string },
): ChartSpec | null {
  if (!columns.length) return null;
  const cells: [number, number, number | null][] = [];
  const data: Array<Record<string, unknown>> = [];
  columns.forEach((row, i) => {
    columns.forEach((col, j) => {
      const v = values[i]?.[j] ?? null;
      cells.push([j, i, v === null ? null : round(v, 4)]);
      if (j > i) data.push({ column_1: row, column_2: col, [options.name]: v === null ? null : round(v, 4) });
    });
  });
  const range = options.range ?? 1;
  return {
    data,
    option: {
      dataset: [],
      grid: { left: 8, right: 8, top: 8, bottom: 48, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const { data: d } = params as { data?: [number, number, number | null] };
          if (!d) return "";
          const v = d[2];
          return `${columns[d[1]] ?? ""} × ${columns[d[0]] ?? ""}: ${v === null ? "undefined" : fmtSig(v)}`;
        },
      },
      xAxis: {
        type: "category",
        data: [...columns],
        axisLabel: { rotate: 40, hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: { type: "category", data: [...columns], inverse: true, splitLine: { show: false } },
      visualMap: {
        min: -range,
        max: range,
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemHeight: 120,
        itemWidth: 10,
        text: [`+${range}`, `−${range}`],
        inRange: { color: [tokens.divNeg, tokens.divMid, tokens.divPos] },
      },
      series: [
        {
          type: "heatmap",
          name: options.name,
          data: cells,
          label: {
            show: columns.length <= 8,
            formatter: (params: unknown) => {
              const { data: d } = params as { data?: [number, number, number | null] };
              return d && d[2] !== null ? d[2].toFixed(2) : "";
            },
            color: tokens.text1,
            fontSize: 10,
          },
          itemStyle: { borderColor: tokens.surface, borderWidth: 2 },
          emphasis: { itemStyle: { borderColor: tokens.text1, borderWidth: 1 } },
        },
      ],
    },
  };
}

/** Source, synthetic and Δ matrices on the columns both sides share. */
export function alignCorr(source: CorrMatrixPayload, synthetic: CorrMatrixPayload) {
  const columns = source.columns.filter((c) => synthetic.columns.includes(c));
  const pick = (p: CorrMatrixPayload) =>
    columns.map((a) => columns.map((b) => p.values[p.columns.indexOf(a)]?.[p.columns.indexOf(b)] ?? null));
  const s = pick(source);
  const y = pick(synthetic);
  const delta = s.map((row, i) => row.map((v, j) => (v === null || y[i]?.[j] == null ? null : y[i][j] - v)));
  return { columns, source: s, synthetic: y, delta };
}
