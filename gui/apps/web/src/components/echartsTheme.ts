import { readToken } from "@/lib/theme";

/** Categorical slots in fixed order (tokens.css --chart-1..8); never cycled, never re-ordered by rank. */
export function chartPalette(): string[] {
  return [1, 2, 3, 4, 5, 6, 7, 8].map((i) => readToken(`--chart-${i}`, "#3987e5"));
}

/** Sequential ramp, "near zero" first (flips between themes so zero recedes into the surface). */
export function sequentialRamp(): string[] {
  return [1, 2, 3, 4, 5, 6, 7].map((i) => readToken(`--seq-${i}`));
}

/** Diverging stops: negative pole, neutral midpoint, positive pole. */
export function divergingStops(): [string, string, string] {
  return [readToken("--div-neg"), readToken("--div-mid"), readToken("--div-pos")];
}

/**
 * The ECharts theme built from the live tokens: thin marks, hairline solid
 * grid, recessive axes, text in text tokens (never series colours), tooltip
 * on surface-2. Rebuilt on every theme change (`chart.setTheme`).
 */
export function buildEChartsTheme(): Record<string, unknown> {
  const text1 = readToken("--text-1");
  const text2 = readToken("--text-2");
  const text3 = readToken("--text-3");
  const grid = readToken("--chart-grid");
  const axis = readToken("--chart-axis");
  const surface = readToken("--chart-surface");
  const font = readToken("--font-ui", "system-ui, sans-serif");
  const axisCommon = {
    axisLine: { show: true, lineStyle: { color: axis, width: 1 } },
    axisTick: { show: false },
    axisLabel: { color: text3, fontSize: 11 },
    splitLine: { show: true, lineStyle: { color: grid, width: 1, type: "solid" } },
    nameTextStyle: { color: text3, fontSize: 11 },
  };
  return {
    color: chartPalette(),
    backgroundColor: "transparent",
    textStyle: { color: text2, fontFamily: font, fontSize: 12 },
    title: { textStyle: { color: text1, fontWeight: 600 }, subtextStyle: { color: text3 } },
    legend: {
      textStyle: { color: text2, fontSize: 12 },
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 14,
      inactiveColor: text3,
      pageTextStyle: { color: text2 },
    },
    tooltip: {
      backgroundColor: readToken("--surface-2"),
      borderColor: readToken("--border-strong"),
      borderWidth: 1,
      textStyle: { color: text1, fontSize: 12 },
      extraCssText: "border-radius:8px;box-shadow:0 10px 28px -8px rgba(0,0,0,.5);",
      axisPointer: { lineStyle: { color: axis }, crossStyle: { color: axis } },
    },
    axisPointer: { lineStyle: { color: axis, width: 1 } },
    categoryAxis: { ...axisCommon, splitLine: { show: false } },
    valueAxis: { ...axisCommon, axisLine: { show: false } },
    logAxis: { ...axisCommon, axisLine: { show: false } },
    timeAxis: { ...axisCommon, splitLine: { show: false } },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    line: { lineStyle: { width: 2 }, symbol: "circle", symbolSize: 8, showSymbol: false, smooth: false },
    bar: { barMaxWidth: 24, itemStyle: { borderRadius: [4, 4, 0, 0] } },
    scatter: { symbolSize: 8, itemStyle: { borderColor: surface, borderWidth: 2 } },
    boxplot: { itemStyle: { borderWidth: 1.5 } },
    heatmap: { itemStyle: { borderColor: surface, borderWidth: 2 } },
    radar: {
      axisName: { color: text2 },
      splitLine: { lineStyle: { color: grid } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: axis } },
    },
    visualMap: { textStyle: { color: text2 }, inRange: { color: sequentialRamp() } },
    dataZoom: {
      textStyle: { color: text3 },
      borderColor: grid,
      fillerColor: "rgba(235,104,52,0.12)",
      handleStyle: { color: readToken("--surface-3"), borderColor: axis },
      dataBackground: { lineStyle: { color: axis }, areaStyle: { color: grid } },
    },
    markLine: { lineStyle: { color: text3, type: "solid", width: 1 }, label: { color: text2 }, symbol: "none" },
    markArea: { itemStyle: { color: "rgba(107,114,128,0.14)" }, label: { color: text2 } },
  };
}
