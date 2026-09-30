/**
 * Design tokens resolved for chart options (ECharts paints on a canvas and
 * cannot read CSS variables). Re-read when the theme changes: every chart
 * option that uses them lists `tokens` in its memo dependencies.
 */
import { useMemo } from "react";

import { readToken, useTheme } from "@/lib/theme";

export interface ChartTokens {
  /** Categorical slots 1–8 in fixed order. */
  slots: string[];
  other: string;
  text1: string;
  text2: string;
  text3: string;
  grid: string;
  axis: string;
  surface: string;
  slate: string;
  accent: string;
  good: string;
  warn: string;
  critical: string;
  info: string;
  neutral: string;
  seq: string[];
  divNeg: string;
  divMid: string;
  divPos: string;
}

export const FALLBACK_TOKENS: ChartTokens = {
  slots: ["#3987e5", "#d95926", "#1baf7a", "#c98500", "#d55181", "#008300", "#7a3fd1", "#e66767"],
  other: "#6b7280",
  text1: "#f2f4f7",
  text2: "#b8bfcc",
  text3: "#939aa7",
  grid: "#262c37",
  axis: "#3a4150",
  surface: "#12151b",
  slate: "#6b7280",
  accent: "#eb6834",
  good: "#1baf7a",
  warn: "#fab219",
  critical: "#d03b3b",
  info: "#2a78d6",
  neutral: "#6b7280",
  seq: ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
  divNeg: "#3987e5",
  divMid: "#383835",
  divPos: "#e66767",
};

export function readChartTokens(): ChartTokens {
  const t = (name: `--${string}`, fallback: string) => readToken(name, fallback);
  const f = FALLBACK_TOKENS;
  return {
    slots: f.slots.map((fallback, i) => t(`--chart-${i + 1}`, fallback)),
    other: t("--chart-other", f.other),
    text1: t("--text-1", f.text1),
    text2: t("--text-2", f.text2),
    text3: t("--text-3", f.text3),
    grid: t("--chart-grid", f.grid),
    axis: t("--chart-axis", f.axis),
    surface: t("--chart-surface", f.surface),
    slate: t("--slate", f.slate),
    accent: t("--accent", f.accent),
    good: t("--status-good", f.good),
    warn: t("--status-warn", f.warn),
    critical: t("--status-critical", f.critical),
    info: t("--status-info", f.info),
    neutral: t("--status-neutral", f.neutral),
    seq: f.seq.map((fallback, i) => t(`--seq-${i + 1}`, fallback)),
    divNeg: t("--div-neg", f.divNeg),
    divMid: t("--div-mid", f.divMid),
    divPos: t("--div-pos", f.divPos),
  };
}

/** Tokens for the current theme; a new object when the theme changes. */
export function useChartTokens(): ChartTokens {
  const { resolved } = useTheme();
  // `resolved` is the dependency on purpose: the CSS variables change with it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readChartTokens(), [resolved]);
}
