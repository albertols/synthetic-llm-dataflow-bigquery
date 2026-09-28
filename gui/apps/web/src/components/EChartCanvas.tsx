/**
 * Lazy chunk: ECharts core with the chart types and components the tabs use.
 * Registered once, on first chart: bar, line, scatter, heatmap, boxplot,
 * radar, parallel, gauge, graph, custom; grid, dataset + transform, tooltip,
 * legend, title, mark line/area/point, visual map, data zoom, brush, graphic,
 * aria. Pie (donut) is left out on purpose (dataviz: deprioritised). A tab
 * needing another module notes NEEDS_FOUNDATION instead of importing the
 * full `echarts` bundle.
 */
import type { EChartsOption } from "echarts";
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
};

export default function EChartCanvas({ option, data, height, ariaLabel }: EChartCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const { resolved } = useTheme();
  const reducedMotion = useReducedMotion();

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
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setTheme(buildEChartsTheme());
  }, [resolved]);

  useEffect(() => {
    chartRef.current?.setOption(merged, { notMerge: true, lazyUpdate: true });
  }, [merged]);

  return <div ref={hostRef} role="img" aria-label={ariaLabel} style={{ height, width: "100%" }} />;
}
