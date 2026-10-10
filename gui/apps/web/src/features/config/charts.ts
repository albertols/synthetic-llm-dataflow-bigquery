/**
 * ECharts option builders shared by the CONFIG tab. Colours come from the
 * design tokens (read live, so pass `theme` to memoise on it), categorical
 * series take the fixed slots in order, markers of "the current setting"
 * wear the accent. One y-axis per chart, always.
 */
import type { EChartsOption } from "echarts";

import { readToken } from "@/lib/theme";

export type AxisType = "value" | "log" | "category";

export type LineSeriesSpec = {
  name: string;
  /** Column in the dataset rows holding y. */
  y: string;
  /** Step line (e.g. an ECDF or a ladder). */
  step?: boolean;
  /** Fill the area under the line (a band). */
  area?: boolean;
  symbols?: boolean;
};

export type Marker = { x?: number | string; y?: number; label: string };

export function lineOption(
  spec: {
    x: string;
    xType?: AxisType;
    xName: string;
    yName: string;
    yType?: AxisType;
    yMin?: number;
    yMax?: number;
    series: LineSeriesSpec[];
    markers?: Marker[];
    /** A shaded band: `base` = its lower edge, `span` = upper − lower (stacked), e.g. ε around an ECDF. */
    band?: { base: string; span: string; name: string };
    xFormatter?: (value: number) => string;
    yFormatter?: (value: number) => string;
  },
  /** The resolved theme: pass it so a memoised option re-reads the tokens after a theme switch. */
  _theme?: string,
): EChartsOption {
  const accent = readToken("--accent");
  const text2 = readToken("--text-2");
  const band = readToken("--chart-1");
  const series: NonNullable<EChartsOption["series"]> = [];
  if (spec.band) {
    series.push(
      {
        type: "line",
        name: `${spec.band.name} (lower)`,
        encode: { x: spec.x, y: spec.band.base },
        stack: "band",
        lineStyle: { opacity: 0 },
        itemStyle: { color: band },
        showSymbol: false,
        silent: true,
        tooltip: { show: false },
      },
      {
        type: "line",
        name: spec.band.name,
        encode: { x: spec.x, y: spec.band.span },
        stack: "band",
        lineStyle: { opacity: 0 },
        itemStyle: { color: band },
        areaStyle: { color: band, opacity: 0.16 },
        showSymbol: false,
        silent: true,
      },
    );
  }
  spec.series.forEach((s, i) => {
    // Fixed slot order for the plotted series, whatever the band series before them took.
    const color = readToken(`--chart-${(i % 8) + 1}`);
    series.push({
      type: "line",
      name: s.name,
      encode: { x: spec.x, y: s.y },
      itemStyle: { color },
      lineStyle: { color, width: 2 },
      showSymbol: s.symbols ?? false,
      ...(s.step ? { step: "end" as const } : {}),
      ...(s.area ? { areaStyle: { opacity: 0.1 } } : {}),
      ...(i === 0 && spec.markers?.length
        ? {
            markLine: {
              symbol: "none",
              silent: true,
              lineStyle: { color: accent, width: 1.5, type: "solid" },
              label: { color: text2, formatter: (p: { name?: string }) => p.name ?? "" },
              data: spec.markers.map((m) =>
                m.x !== undefined ? { name: m.label, xAxis: m.x } : { name: m.label, yAxis: m.y },
              ),
            },
          }
        : {}),
    });
  });
  const legendNames = spec.series.map((s) => s.name);
  const legend = legendNames.length > 1;
  // Never set a key to `undefined`: it would override the token theme's default (e.g. hide axis labels).
  return {
    grid: { left: 8, right: 24, top: legend ? 60 : 34, bottom: 34, containLabel: true },
    ...(legend ? { legend: { type: "scroll", top: 0, left: 0, right: 0, data: legendNames } } : {}),
    tooltip: { trigger: "axis" },
    xAxis: {
      type: (spec.xType ?? "value") as "value",
      name: spec.xName,
      nameLocation: "middle",
      nameGap: 28,
      ...(spec.xFormatter ? { axisLabel: { formatter: spec.xFormatter } } : {}),
    },
    yAxis: {
      type: (spec.yType ?? "value") as "value",
      name: spec.yName,
      nameTextStyle: { align: "left" },
      ...(spec.yMin !== undefined ? { min: spec.yMin } : {}),
      ...(spec.yMax !== undefined ? { max: spec.yMax } : {}),
      ...(spec.yFormatter ? { axisLabel: { formatter: spec.yFormatter } } : {}),
    },
    series,
  };
}

export function barOption(
  spec: {
    x: string;
    y: string;
    name: string;
    xName?: string;
    yName: string;
    horizontal?: boolean;
    /** Category → highlighted in the accent (the current setting). */
    highlight?: string | number;
    yFormatter?: (value: number) => string;
    markY?: Marker;
  },
  _theme?: string,
): EChartsOption {
  const accent = readToken("--accent");
  const text2 = readToken("--text-2");
  const categoryAxis = { type: "category" as const, ...(spec.xName ? { name: spec.xName } : {}) };
  const valueAxis = {
    type: "value" as const,
    name: spec.yName,
    ...(spec.yFormatter ? { axisLabel: { formatter: spec.yFormatter } } : {}),
  };
  return {
    grid: { left: 8, right: 24, top: 30, bottom: spec.xName && !spec.horizontal ? 30 : 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: spec.horizontal ? valueAxis : categoryAxis,
    yAxis: spec.horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
    series: [
      {
        type: "bar",
        name: spec.name,
        encode: spec.horizontal ? { y: spec.x, x: spec.y } : { x: spec.x, y: spec.y },
        itemStyle: {
          borderRadius: spec.horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
          ...(spec.highlight === undefined
            ? {}
            : {
                color: (p: { name?: string }) =>
                  String(p.name) === String(spec.highlight) ? accent : readToken("--chart-1"),
              }),
        },
        ...(spec.markY
          ? {
              markLine: {
                symbol: "none",
                silent: true,
                lineStyle: { color: accent, width: 1.5, type: "solid" },
                label: { color: text2, formatter: spec.markY.label },
                data: [spec.horizontal ? { xAxis: spec.markY.y } : { yAxis: spec.markY.y }],
              },
            }
          : {}),
      },
    ],
  };
}

/**
 * Returns `option` unchanged; the theme argument makes a memoised option
 * depend on the theme, so its token colours are re-read after a switch.
 */
export function themed<T>(option: T, _theme: string): T {
  return option;
}

/** Log-spaced sample points between lo and hi (inclusive). */
export function logSpace(lo: number, hi: number, count: number): number[] {
  const a = Math.log10(lo);
  const b = Math.log10(hi);
  return Array.from({ length: count }, (_, i) => 10 ** (a + ((b - a) * i) / (count - 1)));
}

export const compact = (v: number) =>
  new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
export const pctFormat = (v: number) => `${Math.round(v * 1000) / 10}%`;
/** Percent for log axes: keeps small shares visible (0.001%, 0.01%, 1%). */
export const pctLogFormat = (v: number) => `${Number((v * 100).toPrecision(2))}%`;
