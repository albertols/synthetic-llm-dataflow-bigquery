/**
 * Lazy chunk: ECharts core with the chart types and components the tabs use.
 * Registered once, on first chart: bar, line, scatter, heatmap, boxplot,
 * radar, parallel, gauge, graph, custom; grid, dataset + transform, tooltip,
 * legend, title, mark line/area/point, visual map, data zoom, brush, graphic,
 * aria. Pie (donut) is left out on purpose (dataviz: deprioritised). A tab
 * needing another module notes NEEDS_FOUNDATION instead of importing the
 * full `echarts` bundle.
 */
import type { EChartsOption, EChartsType } from "echarts";
import {
  BarChart,
  BoxplotChart,
  CustomChart,
  GaugeChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  ParallelChart,
  RadarChart,
  ScatterChart,
} from "echarts/charts";
import {
  AriaComponent,
  BrushComponent,
  DataZoomComponent,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  ParallelComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  TransformComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { LabelLayout, UniversalTransition } from "echarts/features";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import type { ChartEventHandlers } from "./ChartFrame";

import { useReducedMotion } from "@/lib/motion";
import { useTheme } from "@/lib/theme";

import { buildEChartsTheme } from "./echartsTheme";

echarts.use([
  BarChart,
  BoxplotChart,
  CustomChart,
  GaugeChart,
  GraphChart,
  HeatmapChart,
  LineChart,
  ParallelChart,
  RadarChart,
  ScatterChart,
  AriaComponent,
  BrushComponent,
  DataZoomComponent,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  ParallelComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  TransformComponent,
  VisualMapComponent,
  LabelLayout,
  UniversalTransition,
  CanvasRenderer,
]);

export type EChartCanvasProps = {
  option: EChartsOption;
  data: Array<Record<string, unknown>>;
  height: number;
  ariaLabel: string;
  onEvents?: ChartEventHandlers;
  onReady?: (chart: EChartsType) => void;
};

/** Same keys, and each top-level value identical. */
function shallowEqual(a: object, b: object): boolean {
  if (a === b) return true;
  const keysA = Object.keys(a);
  if (keysA.length !== Object.keys(b).length) return false;
  return keysA.every((key) => (a as Record<string, unknown>)[key] === (b as Record<string, unknown>)[key]);
}

function sameRows(a: readonly unknown[], b: readonly unknown[]): boolean {
  return a === b || (a.length === b.length && a.every((row, i) => row === b[i]));
}

export default function EChartCanvas({ option, data, height, ariaLabel, onEvents, onReady }: EChartCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const applied = useRef<{ option: EChartsOption; data: readonly unknown[]; reducedMotion: boolean } | null>(null);
  const onReadyRef = useRef(onReady);
  const { resolved } = useTheme();
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  const merged = useMemo<EChartsOption>(() => {
    const withData = option.dataset === undefined && data.length ? { ...option, dataset: { source: data } } : option;
    return { ...withData, animation: reducedMotion ? false : (withData.animation ?? true) };
  }, [option, data, reducedMotion]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = echarts.init(host, buildEChartsTheme(), { renderer: "canvas" });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
      applied.current = null;
    };
  }, []);

  // Event handlers: bound with chart.on, unbound and rebound when the map changes.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onEvents) return undefined;
    const entries = Object.entries(onEvents);
    for (const [name, handler] of entries) chart.on(name, handler);
    return () => {
      for (const [name, handler] of entries) chart.off(name, handler);
    };
  }, [onEvents]);

  useEffect(() => {
    chartRef.current?.setTheme(buildEChartsTheme());
  }, [resolved]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    // notMerge redraws from scratch, so skip an option that is only a new object with the same parts.
    const previous = applied.current;
    if (
      previous &&
      previous.reducedMotion === reducedMotion &&
      sameRows(previous.data, data) &&
      shallowEqual(previous.option, option)
    ) {
      return;
    }
    const first = previous === null;
    applied.current = { option, data, reducedMotion };
    chart.setOption(merged, { notMerge: true, lazyUpdate: true });
    if (first) onReadyRef.current?.(chart);
  }, [merged, option, data, reducedMotion]);

  return <div ref={hostRef} role="img" aria-label={ariaLabel} style={{ height, width: "100%" }} />;
}
