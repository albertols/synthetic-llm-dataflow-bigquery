/**
 * Chart builders for the compare view: trend small multiples with noise and
 * threshold bands, the family-score radar, the fidelity–privacy Pareto
 * scatter, and parallel coordinates from parameters to scores. Colour follows
 * the entity (engine or model, in first-seen order; runs by their position),
 * never the rank.
 */
import type { EChartsOption } from "echarts";

import type { ComparedMetric, Comparison, EvaluationSummary } from "@contracts/api";

import {
  echartsValueAxis,
  linearAxis,
  logAxis,
  scoreAxis,
  scoreAxisFloor,
  thresholdMarkLines,
  type AxisUnit,
  type ThresholdLine,
  type ValueAxis,
} from "@/lib/chartAxis";
import { formatDateTime } from "@/lib/format";

import { metricMeta, metricShort } from "./catalogue";
import { paretoFrontier, slotMap } from "./compare";
import { fmtMetric, fmtScore, scopeLabel, shortModel } from "./format";
import { undefinedValueText } from "./reading";
import type { ChartTokens } from "./tokens";

export type ColorBy = "engine" | "model";

export function colorKey(e: EvaluationSummary, by: ColorBy): string {
  return by === "engine" ? (e.engine ?? "—") : shortModel(e.llm_model_uri);
}

export function colorOf(slots: Map<string, number>, key: string, tokens: ChartTokens): string {
  const slot = slots.get(key) ?? 0;
  return slot ? (tokens.slots[slot - 1] ?? tokens.other) : tokens.other;
}

/** Indices of the compared evaluations, oldest first. */
export function timeOrder(evaluations: readonly EvaluationSummary[]): number[] {
  return evaluations
    .map((_, i) => i)
    .sort((a, b) => evaluations[a]!.evaluated_at.localeCompare(evaluations[b]!.evaluated_at));
}

/** The axis unit a metric's value_kind prints in (the shared axis helper formats the ticks). */
function axisUnit(kind: string | null | undefined): AxisUnit {
  switch (kind) {
    case "share":
    case "ratio":
    case "bits":
    case "count":
    case "score":
    case "auc":
      return kind;
    default:
      return "plain";
  }
}

/**
 * The trend's value axis, from the shared helper (@/lib/chartAxis): a score on [floor, 1]
 * (the radar's floor, never past 1), a lift — a ratio ≥ 0 spanning decades — on a log axis
 * with 0 at its floor, anything else linear on a nice step whose labels never repeat.
 */
export function trendAxis(
  metric: Pick<ComparedMetric, "metric_id" | "value_kind">,
  values: readonly number[],
): ValueAxis {
  const meta = metricMeta(metric.metric_id);
  if (metric.value_kind === "score") return scoreAxis(values);
  if (meta?.uses_ci_bound && metric.value_kind === "ratio") return logAxis(values, { unit: "ratio", include: [1] });
  return linearAxis(values, { unit: axisUnit(metric.value_kind) });
}

/**
 * One small multiple: the metric's value per run in time order, thresholds as lines, the noise
 * band as an area. A CI-bound metric (a lift) whose value is NULL — undefined with no copies, or
 * +∞ with copies only in R, which json_safe stores as NULL — is drawn at its gate (ci_low) with a
 * triangle, never dropped: that bound is what its status read (Ruling R38). `note` says so.
 */
export function trendSpec(
  metric: ComparedMetric,
  evaluations: readonly EvaluationSummary[],
  by: ColorBy,
  tokens: ChartTokens,
): { option: EChartsOption; data: Array<Record<string, unknown>>; note: string | null } | null {
  const order = timeOrder(evaluations);
  const meta = metricMeta(metric.metric_id);
  const gateKey = meta?.direction === "higher_better" ? "ci_high" : "ci_low";
  const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);
  const points = order
    .map((i) => {
      const cell = metric.cells[i] ?? null;
      if (!cell || cell.status === "not_evaluated") return null;
      if (finite(cell.value)) return { i, e: evaluations[i]!, cell, y: cell.value, gated: false };
      const gate = meta?.uses_ci_bound ? cell[gateKey] : null;
      return finite(gate) ? { i, e: evaluations[i]!, cell, y: gate, gated: true } : null;
    })
    .filter((p): p is NonNullable<typeof p> => p !== null);
  if (!points.length) return null;
  const slots = slotMap(evaluations.map((e) => colorKey(e, by)));
  const labels = order.map((i) => evaluations[i]!.evaluation_id);
  const kind = metric.value_kind;
  const first = points[0]!.cell;
  const axis = trendAxis(
    metric,
    points.map((p) => p.y),
  );
  // Where a value is drawn: on a log axis 0 sits at the floor (the tooltip and table say so).
  const y = (v: number) => (axis.kind === "log" ? axis.place(v) : v);
  const floor = Math.max(0, ...points.map((p) => p.cell.noise_floor ?? 0));
  const ref = meta?.direction === "target" ? (meta.target ?? null) : meta?.direction === "higher_better" ? null : 0;
  const lineAt = (t: number) => (meta?.direction === "target" && meta.target !== null ? meta.target + t : t);
  const thresholds: ThresholdLine[] = [];
  if (first.threshold_warn !== null && first.threshold_warn !== 0)
    thresholds.push({
      value: lineAt(first.threshold_warn),
      label: `warn ${fmtMetric(first.threshold_warn, kind)}`,
      color: tokens.warn,
    });
  if (first.threshold_fail !== null && first.threshold_fail !== 0)
    thresholds.push({
      value: lineAt(first.threshold_fail),
      label: `fail ${fmtMetric(first.threshold_fail, kind)}`,
      color: tokens.critical,
    });
  const markLines = thresholdMarkLines(thresholds, axis, { labelColor: tokens.text3 });
  const atFloor = (v: number) => axis.kind === "log" && v <= axis.min;
  const plotted = (p: (typeof points)[number]) =>
    p.gated ? `${gateKey} ${fmtMetric(p.y, kind)} (value ${undefinedValueText({ ciLow: p.cell.ci_low })})` : null;
  const series: NonNullable<EChartsOption["series"]> = [
    {
      type: "line",
      name: "trend",
      data: points.map((p) => [labels.indexOf(p.e.evaluation_id), y(p.y)]),
      symbol: "none",
      lineStyle: { color: tokens.slate, width: 2 },
      silent: true,
      markLine: markLines.length ? { symbol: "none", silent: true, data: markLines } : undefined,
      markArea:
        floor > 0 && ref !== null && axis.kind === "linear"
          ? {
              silent: true,
              itemStyle: { color: tokens.slate, opacity: 0.22 },
              data: [[{ yAxis: Math.max(axis.min, ref - floor) }, { yAxis: Math.min(axis.max, ref + floor) }]] as never,
            }
          : undefined,
    },
    ...[...slots.entries()].map(([key]) => ({
      type: "scatter" as const,
      name: key,
      data: points
        .filter((p) => colorKey(p.e, by) === key)
        .map((p) => ({
          value: [labels.indexOf(p.e.evaluation_id), y(p.y), p.e.evaluation_id, p.y, p.gated ? 1 : 0],
          // The gate stands in for a NULL value: a triangle, not the value's dot.
          ...(p.gated ? { symbol: "triangle", symbolSize: 11 } : {}),
        })),
      symbolSize: 9,
      itemStyle: { color: colorOf(slots, key, tokens), borderColor: tokens.surface, borderWidth: 2 },
    })),
  ];
  const gatedCount = points.filter((p) => p.gated).length;
  const note = gatedCount
    ? `▲ ${gatedCount === 1 ? "one run has" : `${gatedCount} runs have`} no finite value (no copies, or none in the holdout): drawn at the gate ${gateKey}, the bound its status reads.`
    : null;
  return {
    note,
    data: points.map((p) => ({
      evaluation: p.e.evaluation_id,
      evaluated_at: formatDateTime(p.e.evaluated_at),
      [by]: colorKey(p.e, by),
      value: p.cell.value,
      ...(p.gated ? { plotted: plotted(p) } : {}),
      noise_floor: p.cell.noise_floor,
      status: p.cell.status,
      ...(atFloor(p.y) ? { drawn_at: `axis floor ${axis.format(axis.min)} (log scale)` } : {}),
    })),
    option: {
      dataset: [],
      legend: { top: 0, left: 0, data: [...slots.keys()], itemWidth: 10 },
      grid: { left: 4, right: 28, top: 30, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const { data, seriesName } = params as {
            data?: { value: [number, number, string, number, number] };
            seriesName?: string;
          };
          const v = data?.value;
          if (!v || !v[2]) return "";
          const shown = v[4]
            ? `${gateKey} ${fmtMetric(v[3], kind)} (value ${undefinedValueText({ ciLow: gateKey === "ci_low" ? v[3] : null })})`
            : fmtMetric(v[3], kind);
          const floorNote = atFloor(v[3]) ? " (drawn at the axis floor: log scale)" : "";
          return `${v[2]} · ${seriesName ?? ""}<br/>${shown}${floorNote}`;
        },
      },
      xAxis: { type: "category", data: labels, axisLabel: { hideOverlap: true, fontSize: 10 } },
      yAxis: echartsValueAxis(axis),
      series,
    },
  };
}

const FAMILY_IDS = [
  "model.fidelity_score",
  "model.privacy_score",
  "model.integrity_score",
  "model.diversity_score",
] as const;
const FAMILY_NAMES = ["Fidelity", "Privacy", "Integrity", "Diversity"];

export function familyScore(
  comparison: Pick<Comparison, "metrics" | "evaluations">,
  i: number,
  id: string,
): number | null {
  const row = comparison.metrics.find((m) => m.metric_id === id && m.level === "model");
  const cell = row?.cells[i];
  if (cell && cell.value !== null) return cell.value;
  const e = comparison.evaluations[i];
  if (!e) return null;
  const key = id.replace("model.", "") as
    "fidelity_score" | "privacy_score" | "integrity_score" | "diversity_score" | "overall_score";
  return e[key] ?? null;
}

/** The low end of a 0–1 score axis (the radar, parallel coordinates and score trends share it). */
export { scoreAxisFloor };

/** The radar of family scores for up to three runs (a fourth polygon is unreadable; the table carries all). */
export function radarSpec(
  comparison: Pick<Comparison, "metrics" | "evaluations">,
  indices: readonly number[],
  tokens: ChartTokens,
) {
  const shown = indices.slice(0, 3);
  if (!shown.length) return null;
  const floor = scoreAxisFloor(shown.flatMap((i) => FAMILY_IDS.map((id) => familyScore(comparison, i, id))));
  const data = shown.map((i) => {
    const e = comparison.evaluations[i]!;
    return {
      name: e.evaluation_id,
      value: FAMILY_IDS.map((id) => {
        const v = familyScore(comparison, i, id);
        return v === null ? floor : Number(v.toFixed(4));
      }),
      lineStyle: { color: tokens.slots[i] ?? tokens.other, width: 2 },
      itemStyle: { color: tokens.slots[i] ?? tokens.other },
      areaStyle: { color: tokens.slots[i] ?? tokens.other, opacity: 0.08 },
    };
  });
  const option: EChartsOption = {
    dataset: [],
    legend: { top: 0, left: 0, data: data.map((d) => d.name) },
    tooltip: { trigger: "item" },
    radar: {
      center: ["50%", "56%"],
      radius: "62%",
      indicator: FAMILY_NAMES.map((name) => ({ name, min: floor, max: 1 })),
      axisName: { color: tokens.text2 },
    },
    series: [{ type: "radar", data, symbolSize: 6 }],
  };
  const rows = shown.map((i) => ({
    evaluation: comparison.evaluations[i]!.evaluation_id,
    ...Object.fromEntries(FAMILY_IDS.map((id, k) => [FAMILY_NAMES[k]!.toLowerCase(), familyScore(comparison, i, id)])),
  }));
  return { option, data: rows, floor };
}

/** Fidelity vs privacy for the compared runs over the whole history in grey; the frontier (max both) as a step line. */
export function paretoSpec(
  comparison: Pick<Comparison, "metrics" | "evaluations">,
  history: readonly EvaluationSummary[],
  by: ColorBy,
  tokens: ChartTokens,
) {
  const compared = comparison.evaluations
    .map((e, i) => ({
      e,
      x: familyScore(comparison, i, "model.fidelity_score"),
      y: familyScore(comparison, i, "model.privacy_score"),
    }))
    .filter((p): p is { e: EvaluationSummary; x: number; y: number } => p.x !== null && p.y !== null);
  if (!compared.length) return null;
  const ids = new Set(compared.map((p) => p.e.evaluation_id));
  const background = history
    .filter((e) => !ids.has(e.evaluation_id) && e.fidelity_score !== null && e.privacy_score !== null)
    .map((e) => ({ e, x: e.fidelity_score!, y: e.privacy_score! }));
  const frontier = paretoFrontier([...compared, ...background]);
  const frontierLine = [...frontier].sort((a, b) => a.x - b.x);
  const slots = slotMap(comparison.evaluations.map((e) => colorKey(e, by)));
  const series: NonNullable<EChartsOption["series"]> = [
    {
      type: "scatter",
      name: "other evaluations",
      data: background.map((p) => [p.x, p.y, p.e.evaluation_id]),
      symbolSize: 7,
      itemStyle: { color: tokens.other, opacity: 0.7, borderColor: tokens.surface, borderWidth: 1 },
    },
    {
      type: "line",
      name: "frontier",
      data: frontierLine.map((p) => [p.x, p.y]),
      step: "end",
      symbol: "none",
      lineStyle: { color: tokens.text3, width: 1 },
      silent: true,
    },
    ...[...slots.keys()].map((key) => ({
      type: "scatter" as const,
      name: key,
      data: compared.filter((p) => colorKey(p.e, by) === key).map((p) => [p.x, p.y, p.e.evaluation_id]),
      symbolSize: 12,
      itemStyle: { color: colorOf(slots, key, tokens), borderColor: tokens.surface, borderWidth: 2 },
      label: {
        show: true,
        position: "right" as const,
        formatter: (params: unknown) => ((params as { data?: unknown[] }).data?.[2] as string) ?? "",
        color: tokens.text2,
        fontSize: 10,
      },
    })),
  ];
  const all = [...compared, ...background];
  const lo = (key: "x" | "y") =>
    Number(Math.max(0, Math.floor(Math.min(...all.map((p) => p[key])) * 20) / 20 - 0.05).toFixed(2));
  return {
    option: {
      dataset: [],
      legend: { top: 0, left: 0, data: ["other evaluations", "frontier", ...slots.keys()] },
      grid: { left: 8, right: 56, top: 36, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const { data } = params as { data?: [number, number, string] };
          return data && data[2] ? `${data[2]}<br/>fidelity ${fmtScore(data[0])} · privacy ${fmtScore(data[1])}` : "";
        },
      },
      xAxis: { type: "value", name: "fidelity score", nameLocation: "middle", nameGap: 26, min: lo("x"), max: 1 },
      yAxis: { type: "value", min: lo("y"), max: 1 },
      series,
    } satisfies EChartsOption,
    data: compared.map((p) => ({
      evaluation: p.e.evaluation_id,
      [by]: colorKey(p.e, by),
      fidelity: p.x,
      privacy: p.y,
      on_frontier: frontier.has(p),
    })),
  };
}

const PARALLEL_PARAMS: { key: keyof EvaluationSummary; label: string; numeric: boolean }[] = [
  { key: "engine", label: "engine", numeric: false },
  { key: "llm_model_uri", label: "model", numeric: false },
  { key: "retrieval_method", label: "retrieval", numeric: false },
  { key: "similarity", label: "similarity", numeric: true },
  { key: "reference_rows_limit", label: "ref rows", numeric: true },
  { key: "num_rows_requested", label: "rows", numeric: true },
  { key: "source_stats_tier", label: "stats tier", numeric: false },
  { key: "freetext_expansion", label: "expansion", numeric: false },
  { key: "seed", label: "seed", numeric: false },
];

/** Parameters (those that differ, at most six) → the five scores, one line per run. */
export function parallelSpec(
  comparison: Pick<Comparison, "metrics" | "evaluations">,
  by: ColorBy,
  tokens: ChartTokens,
) {
  const evals = comparison.evaluations;
  if (evals.length < 2) return null;
  const value = (e: EvaluationSummary, key: keyof EvaluationSummary) =>
    key === "llm_model_uri" ? shortModel(e.llm_model_uri) : (e[key] as string | number | null);
  const params = PARALLEL_PARAMS.filter((p) => new Set(evals.map((e) => String(value(e, p.key)))).size > 1).slice(0, 6);
  const scores = ["model.overall_score", ...FAMILY_IDS];
  const scoreFloor = scoreAxisFloor(evals.flatMap((_, i) => scores.map((id) => familyScore(comparison, i, id))));
  const axes = [
    ...params.map((p, dim) =>
      p.numeric
        ? { dim, name: p.label, type: "value" as const, scale: true }
        : {
            dim,
            name: p.label,
            type: "category" as const,
            data: [...new Set(evals.map((e) => String(value(e, p.key) ?? "—")))],
          },
    ),
    ...scores.map((id, k) => ({
      dim: params.length + k,
      name: id.replace("model.", "").replace("_score", ""),
      type: "value" as const,
      min: scoreFloor,
      max: 1,
    })),
  ];
  const slots = slotMap(evals.map((e) => colorKey(e, by)));
  const rows = evals.map((e, i) => [
    ...params.map((p) => (p.numeric ? (value(e, p.key) as number | null) : String(value(e, p.key) ?? "—"))),
    ...scores.map((id) => {
      const v = familyScore(comparison, i, id);
      return v === null ? null : Number(v.toFixed(4));
    }),
  ]);
  return {
    option: {
      dataset: [],
      legend: { top: 0, left: 0, data: [...slots.keys()] },
      parallel: { left: 40, right: 60, top: 60, bottom: 24 },
      parallelAxis: axes,
      tooltip: { trigger: "item" },
      series: [...slots.keys()].map((key) => ({
        type: "parallel" as const,
        name: key,
        lineStyle: { color: colorOf(slots, key, tokens), width: 2, opacity: 0.9 },
        data: rows.filter((_, i) => colorKey(evals[i]!, by) === key),
      })),
    } satisfies EChartsOption,
    data: evals.map((e, i) => ({
      evaluation: e.evaluation_id,
      ...Object.fromEntries(params.map((p, k) => [p.label, rows[i]![k]])),
      ...Object.fromEntries(scores.map((id, k) => [id.replace("model.", ""), rows[i]![params.length + k]])),
    })),
  };
}

/**
 * Metric keys that move most between the first and last run in time: a status
 * change first, then |Δ| relative to the larger of the noise floor and the warn
 * threshold. Metrics without thresholds (INFO, column units) are left out — their
 * raw deltas are not comparable across metrics.
 */
export function topMovers(comparison: Pick<Comparison, "metrics" | "evaluations">, n = 4): ComparedMetric[] {
  const order = timeOrder(comparison.evaluations);
  const first = order[0];
  const last = order.at(-1);
  if (first === undefined || last === undefined || first === last) return [];
  return comparison.metrics
    .filter((m) => m.level !== "model" && !m.metric_id.endsWith("_score"))
    .map((m) => {
      const a = m.cells[first];
      const b = m.cells[last];
      if (!a || !b || a.value === null || b.value === null || a.threshold_warn === null || a.threshold_warn === 0)
        return { m, z: 0 };
      const scale = Math.max(a.noise_floor ?? 0, b.noise_floor ?? 0, Math.abs(a.threshold_warn), 1e-9);
      const z = Math.abs(b.value - a.value) / scale;
      return { m, z: z + (a.status !== b.status ? 100 : 0) };
    })
    .filter((x) => x.z > 1)
    .sort((x, y) => y.z - x.z)
    .slice(0, n)
    .map((x) => x.m);
}

export function metricLabel(
  m: Pick<ComparedMetric, "metric_id" | "level" | "table_name" | "column_name" | "column_name_2" | "edge">,
): string {
  return `${metricShort(m.metric_id)} · ${scopeLabel(m)}`;
}
