/**
 * The live maths chart of a knob sheet: the formula the knob enters, redrawn
 * at the current settings (a marker shows where the dial sits). Measured
 * numbers come from knobs.json `measured` with their MEASURED block cited.
 */
import { useMemo, type ReactNode } from "react";

import type { KnobValue } from "@contracts/knobs";
import { dkwEpsilon, poolReuse } from "@synthetic-platform/stats";

import { ChartFrame } from "@/components/ChartFrame";
import { formatCount, formatFixed } from "@/lib/format";
import { useTheme } from "@/lib/theme";

import { barOption, compact, lineOption, logSpace, pctFormat } from "../charts";
import { knobNumber, measured } from "../model/knobs";
import { MeasuredNote } from "../MeasuredNote";
import {
  ALPHA,
  CONSTANTS,
  duplicateShare,
  effectiveBatchSize,
  expectedDistinctUniform,
  MEASURED_PAIR_LABEL,
  poolDistinct,
  poolTarget,
  sampleDistinctFor,
  type ScenarioInputs,
} from "../model/scenario";

export type ChartSpec = {
  title: string;
  concept?: string;
  description: ReactNode;
  build: () => { option: ReturnType<typeof lineOption>; data: Array<Record<string, unknown>> };
  footer?: ReactNode;
};

const num = (v: KnobValue, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

function dkwChart(n: number, label: string): ChartSpec {
  return {
    title: "DKW band ε(n) at α = 0.05",
    concept: "stats:dkw",
    description: `How far any sample CDF can sit from the truth; the marker is ${label} = ${formatCount(n)} (ε ≈ ${formatFixed(dkwEpsilon(n, ALPHA), 4)}).`,
    build: () => ({
      data: logSpace(1_000, 1_000_000, 31).map((x) => ({ n: Math.round(x), epsilon: dkwEpsilon(x, ALPHA) })),
      option: lineOption({
        x: "n",
        xType: "log",
        xName: "rows n",
        yName: "ε(n)",
        yMin: 0,
        series: [{ name: "ε(n)", y: "epsilon" }],
        markers: [{ x: n, label: `ε = ${formatFixed(dkwEpsilon(n, ALPHA), 4)}` }],
        xFormatter: compact,
      }),
    }),
  };
}

/** The chart spec for a knob, or null when the knob has no maths worth drawing (exported for the sheet tests). */
export function specFor(id: string, settings: Record<string, KnobValue>, inputs: ScenarioInputs): ChartSpec | null {
  const M = inputs.M;
  const n = inputs.n;
  switch (id) {
    case "reference_rows_limit":
      return dkwChart(n, "n");
    case "eval_sample_rows":
    case "eval_privacy_sample_rows":
    case "eval_detection_sample_rows":
      return dkwChart(num(settings[id] ?? null, 200_000), "the evaluation sample");
    case "source_stats": {
      const s = inputs.nonEmptyShare;
      return {
        title: "Distinct values each tier can report",
        concept: "stats:distinct-truncation",
        description: `The exact tier counts the true distinct D (HLL++); the sample tier sees at most n·s = ${formatCount(Math.round(n * s))} non-empty rows.`,
        build: () => ({
          data: logSpace(10, 1_000_000, 31).map((D) => ({
            D: Math.round(D),
            exact: D,
            sample: Math.min(expectedDistinctUniform(D, n * s), n * s),
          })),
          option: lineOption({
            x: "D",
            xType: "log",
            yType: "log",
            xName: "true distinct D",
            yName: "distinct reported",
            series: [
              { name: "exact tier", y: "exact" },
              { name: `sample tier (n = ${compact(n)}, ${pctFormat(s)} non-empty)`, y: "sample" },
            ],
            markers: [{ x: inputs.columnDistinct, label: `D = ${formatCount(inputs.columnDistinct)}` }],
            xFormatter: compact,
            yFormatter: compact,
          }),
        }),
      };
    }
    case "num_rows":
    case "free_text_pool_max": {
      const D = poolDistinct(inputs, sampleDistinctFor(inputs));
      const target = poolTarget(M, D.distinct);
      return {
        title: `Rows per pool value, M / min(M, D, ${formatCount(CONSTANTS.poolCap)})`,
        concept: "stats:pool-reuse",
        description: `The calculator's column: D = ${formatCount(D.distinct)} from the ${D.via}, so the pool holds ${formatCount(target)} values and at M = ${compact(M)} each repeats ≈ ${formatCount(Math.round(poolReuse(M, target)))} times.`,
        build: () => ({
          data: logSpace(1_000, 1_000_000_000, 28).map((m) => ({
            M: Math.round(m),
            reuse: poolReuse(m, poolTarget(m, D.distinct)),
          })),
          option: lineOption({
            x: "M",
            xType: "log",
            yType: "log",
            xName: "rows generated M",
            yName: "rows per value",
            series: [{ name: "rows per value", y: "reuse" }],
            markers: [{ x: M, label: `M = ${compact(M)}` }],
            xFormatter: compact,
            yFormatter: compact,
          }),
        }),
      };
    }
    case "similarity": {
      const s = num(settings.similarity ?? null, 0.5);
      return {
        title: "What similarity sets (b2)",
        description:
          "b2's categorical temperature 2(1 − s) and LLM temperature 1.3 − 1.2 s; b1 only blends its temporal draws.",
        build: () => ({
          data: Array.from({ length: 21 }, (_, i) => {
            const x = i / 20;
            return { s: x, categorical: 2 * (1 - x), llm: 1.3 - 1.2 * x };
          }),
          option: lineOption({
            x: "s",
            xName: "similarity s",
            yName: "temperature",
            yMin: 0,
            series: [
              { name: "categorical temperature", y: "categorical" },
              { name: "LLM temperature", y: "llm" },
            ],
            markers: [{ x: s, label: `s = ${s}` }],
          }),
        }),
      };
    }
    case "batch_size": {
      const requested = num(settings.batch_size ?? null, knobNumber("batch_size"));
      return {
        title: "Rows per element vs rows generated",
        description: `Effective batch at M = ${compact(M)}: ${formatCount(effectiveBatchSize(requested, M))} rows per element (${formatCount(Math.ceil(M / effectiveBatchSize(requested, M)))} elements).`,
        build: () => ({
          data: logSpace(1_000, 1_000_000_000, 28).map((m) => ({
            M: Math.round(m),
            batch: effectiveBatchSize(requested, m),
          })),
          option: lineOption({
            x: "M",
            xType: "log",
            yType: "log",
            xName: "rows generated M",
            yName: "rows per element",
            series: [{ name: "rows per element", y: "batch" }],
            markers: [{ x: M, label: `M = ${compact(M)}` }],
            xFormatter: compact,
            yFormatter: compact,
          }),
        }),
      };
    }
    case "fk_key_sample_margin":
      return {
        title: "Duplicate share of random key draws",
        concept: "stats:duplicate-share",
        description: `Drawing M keys uniformly from K = margin × M; at the ${CONSTANTS.keyMargin}× margin ≈ ${pctFormat(duplicateShare(1_000_000, CONSTANTS.keyMargin * 1_000_000))} of draws repeat.`,
        build: () => ({
          data: logSpace(1, 1_000, 31).map((margin) => ({
            margin,
            share: duplicateShare(1_000_000, margin * 1_000_000),
          })),
          option: lineOption({
            x: "margin",
            xType: "log",
            xName: "capacity K / M",
            yName: "duplicate share",
            yMin: 0,
            series: [{ name: "duplicate share", y: "share" }],
            markers: [{ x: CONSTANTS.keyMargin, label: `${CONSTANTS.keyMargin}×` }],
            yFormatter: pctFormat,
          }),
        }),
      };
    case "fk_candidate_cap":
    case "max_conditional_values_per_request": {
      const limit = knobNumber("max_conditional_values_per_request");
      const cap = num(settings.fk_candidate_cap ?? null, knobNumber("fk_candidate_cap"));
      return {
        title: "Keys per fan-out request vs the candidate cap",
        description: `A request carries keys × cap candidates per conditional edge, kept under ${formatCount(limit)} values.`,
        build: () => ({
          data: [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1_024].map((c) => ({
            cap: c,
            keys: Math.max(1, Math.floor(limit / c)),
          })),
          option: lineOption({
            x: "cap",
            xType: "log",
            yType: "log",
            xName: "candidates per shared key (M)",
            yName: "keys per request",
            series: [{ name: "keys per request", y: "keys", symbols: true }],
            markers: [{ x: cap, label: `cap = ${cap}` }],
            xFormatter: compact,
            yFormatter: compact,
          }),
        }),
      };
    }
    case "env":
    case "blocker_failure_ratio_dev":
    case "blocker_failure_ratio_uat":
    case "blocker_failure_ratio_prd": {
      const envs = ["dev", "uat", "prd"].filter((e) => {
        try {
          knobNumber(`blocker_failure_ratio_${e}`);
          return true;
        } catch {
          return false;
        }
      });
      return {
        title: `BLOCKER rows a run of ${compact(M)} may reach`,
        concept: "config:blocker-gate",
        description: "Rows failing a counted BLOCKER rule before the job fails, per env tier.",
        build: () => ({
          data: envs.map((e) => ({
            env: e,
            ratio: knobNumber(`blocker_failure_ratio_${e}`),
            rows: Math.floor(knobNumber(`blocker_failure_ratio_${e}`) * M),
          })),
          option: barOption({
            x: "env",
            y: "rows",
            name: "blocker rows allowed",
            yName: "rows",
            highlight: inputs.env,
            yFormatter: compact,
          }),
        }),
      };
    }
    case "uniqueness_mode":
    case "driven_uniqueness_mode": {
      const runs = measured("make_throughput_figures.ACCEPT_RUNS").value as string[];
      const phases = measured("make_throughput_figures.ACCEPT_PHASES_MIN").value as Record<string, number[]>;
      const rows = [0, 1, 2].map((i) => ({
        run: `${(runs[i] ?? "").replace(/\n/g, " ")}${i === 0 ? " · exact_chained" : " · exact"}`,
        minutes: phases["dedup + load C + A"]?.[i] ?? null,
      }));
      return {
        title: `Dedup + load minutes, ${MEASURED_PAIR_LABEL}`,
        description:
          "Measured: the R6 cold run used the three-barrier chain; the R7 pair one barrier. streaming has no barrier and no measured run.",
        build: () => ({
          data: rows,
          option: barOption({ x: "run", y: "minutes", name: "dedup + load", yName: "minutes", horizontal: true }),
        }),
        footer: <MeasuredNote ids={["make_throughput_figures.ACCEPT_PHASES_MIN"]} />,
      };
    }
    case "sdk_containers": {
      const phases = measured("make_throughput_figures.ACCEPT_PHASES_MIN").value as Record<string, number[]>;
      return {
        title: `Generation minutes, ${MEASURED_PAIR_LABEL}`,
        description: "Measured R7 pair: one SDK process per worker (single) vs one per vCPU (multi).",
        build: () => ({
          data: [
            { topology: "single", minutes: phases["generation C + A"]?.[1] ?? null },
            { topology: "multi", minutes: phases["generation C + A"]?.[2] ?? null },
          ],
          option: barOption({
            x: "topology",
            y: "minutes",
            name: "generation",
            yName: "minutes",
            highlight: inputs.sdkContainers,
          }),
        }),
        footer: <MeasuredNote ids={["make_throughput_figures.ACCEPT_PHASES_MIN"]} />,
      };
    }
    case "temperature_ladder": {
      const temps = (settings.temperature_ladder as number[] | undefined) ?? [];
      return {
        title: "Temperature per pool attempt",
        description: "Each retry after an all-copies yield climbs one step, up to the 1.3 ceiling.",
        build: () => ({
          data: temps.map((t, i) => ({ attempt: `attempt ${i + 1}`, temperature: t })),
          option: barOption({ x: "attempt", y: "temperature", name: "temperature", yName: "temperature" }),
        }),
      };
    }
    case "max_free_text_values_per_column": {
      const cap = CONSTANTS.valueChunkCap;
      const sampleD = Math.round(sampleDistinctFor(inputs));
      return {
        title: "Value chunks embedded per free-text column",
        description: `Distinct (column, value) pairs in the reference sample, capped at ${formatCount(cap)}: the calculator's column shows ≈ ${formatCount(sampleD)} distinct in the sample, so ${formatCount(Math.min(sampleD, cap))} chunks; top-${CONSTANTS.topK} seeds are picked from them.`,
        build: () => ({
          data: logSpace(1, 1_000_000, 31).map((d) => ({
            distinct: Math.round(d),
            chunks: Math.min(Math.round(d), cap),
          })),
          option: lineOption({
            x: "distinct",
            xType: "log",
            yType: "log",
            xName: "distinct values in the sample",
            yName: "value chunks embedded",
            series: [{ name: "value chunks", y: "chunks" }],
            markers: [{ x: Math.max(1, sampleD), label: `sample D ≈ ${compact(sampleD)}` }],
            xFormatter: compact,
            yFormatter: compact,
          }),
        }),
      };
    }
    case "max_row_doc_rows":
      return {
        title: "Share of the reference sample embedded as row documents",
        description: `${formatCount(CONSTANTS.rowDocCap)} of n rows (fingerprint order): ${pctFormat(Math.min(1, CONSTANTS.rowDocCap / n))} at n = ${compact(n)}.`,
        build: () => ({
          data: logSpace(1_000, 1_000_000, 31).map((x) => ({
            n: Math.round(x),
            share: Math.min(1, CONSTANTS.rowDocCap / x),
          })),
          option: lineOption({
            x: "n",
            xType: "log",
            xName: "reference rows n",
            yName: "embedded share",
            yMin: 0,
            yMax: 1,
            series: [{ name: "embedded share", y: "share" }],
            markers: [{ x: n, label: `n = ${compact(n)}` }],
            xFormatter: compact,
            yFormatter: pctFormat,
          }),
        }),
      };
    case "initial_workers":
    case "autoscaling": {
      const ramp = measured("make_throughput_figures.RAMP_C_COLD").value as number[];
      return {
        title: "C_TABLE rows/s per 2-minute bucket (R6 cold)",
        description:
          "The fleet started on 2 workers and the autoscaler added 2 more minutes into the stage: the slow first buckets.",
        build: () => ({
          data: ramp.map((r, i) => ({ minute: `${i * 2}–${i * 2 + 2}`, rows_per_s: r })),
          option: barOption({
            x: "minute",
            y: "rows_per_s",
            name: "rows/s",
            xName: "minutes into stage",
            yName: "rows/s",
          }),
        }),
        footer: <MeasuredNote ids={["make_throughput_figures.RAMP_C_COLD"]} />,
      };
    }
    case "build_pool_layer": {
      const perColumn = measured("make_ws5_figures.PER_COLUMN").value as Record<string, number>;
      return {
        title: "LLM seconds per pool column, 1M-row run built in-worker",
        concept: "config:pool-economics",
        description:
          "Pool builds repeated inside every worker's setup; a persisted pool layer builds each once per digest.",
        build: () => ({
          data: Object.entries(perColumn).map(([column, seconds]) => ({ column, seconds })),
          option: barOption({ x: "column", y: "seconds", name: "LLM seconds", yName: "seconds", yFormatter: compact }),
        }),
        footer: <MeasuredNote ids={["make_ws5_figures.PER_COLUMN", "make_ws5_figures.POOL_BUILD_LLM"]} />,
      };
    }
    default:
      return null;
  }
}

export function KnobChart({
  id,
  settings,
  inputs,
}: {
  id: string;
  settings: Record<string, KnobValue>;
  inputs: ScenarioInputs;
}) {
  const { resolved } = useTheme();
  const spec = useMemo(() => specFor(id, settings, inputs), [id, settings, inputs]);
  const built = useMemo(() => (spec ? { ...spec.build(), theme: resolved } : null), [spec, resolved]);
  if (!spec || !built) return null;
  return (
    <ChartFrame
      title={spec.title}
      concept={spec.concept}
      description={spec.description}
      option={built.option}
      data={built.data}
      height={220}
      footer={spec.footer}
    />
  );
}
