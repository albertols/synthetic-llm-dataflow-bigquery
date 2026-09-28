/**
 * The storyline holds, and the numbers are computed, not typed: metric values
 * recompute from the stored profiles with packages/stats, noise floors come
 * from the same functions, scores roll up per the catalogue's rules.
 */
import { beforeAll, describe, expect, it } from "vitest";

import {
  catalogueById,
  type EvaluationDataHistoryRow,
  type EvaluationMetricsRow,
  type EvaluationProfilesRow,
  type MetricId,
} from "@synthetic-platform/contracts";
import {
  ksBracket,
  ksCritical,
  modelFamilyScores,
  pitW1,
  scoreValue,
  statusFor,
  tableFamilyScores,
  tvd,
} from "@synthetic-platform/stats";

import { createMockDataset, type MockDataset } from "./index";

let data: MockDataset;
let latest: Map<string, EvaluationDataHistoryRow>;
beforeAll(() => {
  data = createMockDataset();
  latest = new Map();
  for (const row of [...data.registry].sort((a, b) => a.recorded_at.localeCompare(b.recorded_at)))
    latest.set(row.evaluation_id, row);
}, 120_000);

const metricsOf = (id: string) => data.metrics.filter((m) => m.evaluation_id === id);
const idx = (id: string) => Number(id.slice(-4));
const finals = () => [...latest.values()].filter((r) => r.event === "FINAL" && r.metrics_total);

function profile(
  m: EvaluationMetricsRow,
  kind: EvaluationProfilesRow["profile_kind"],
  side: EvaluationProfilesRow["side"],
  scope: { column?: string | null; edge?: string | null } = {},
) {
  const found = data.profiles.find(
    (p) =>
      p.evaluation_id === m.evaluation_id &&
      p.table_name === m.table_name &&
      p.profile_kind === kind &&
      p.side === side &&
      (scope.column === undefined || p.column_name === scope.column) &&
      (scope.edge === undefined || p.edge === scope.edge),
  );
  expect(found, `${m.evaluation_id} ${m.table_name} ${kind} ${side} ${scope.column ?? scope.edge ?? ""}`).toBeDefined();
  return found!.payload as Record<string, unknown>;
}

describe("the storyline", { timeout: 60_000 }, () => {
  it("has 40 evaluations over 8 weeks, RUNNING then FINAL", () => {
    expect(latest.size).toBe(40);
    const dates = [...latest.values()].map((r) => r.evaluated_at).sort();
    expect(dates[0]!.slice(0, 10)).toBe("2026-08-03");
    expect(dates.at(-1)!.slice(0, 10)).toBe("2026-09-27");
    for (const [id, row] of latest) {
      const events = data.registry.filter((r) => r.evaluation_id === id).map((r) => r.event);
      expect(events[0]).toBe("RUNNING");
      if (row.event === "FINAL") expect(events).toEqual(["RUNNING", "FINAL"]);
    }
  });

  it("covers the edge cases", () => {
    const rows = [...latest.values()];
    const count = (pred: (r: EvaluationDataHistoryRow) => boolean) => rows.filter(pred).length;
    expect(count((r) => r.status === "FAILED")).toBe(1);
    expect(count((r) => r.status === "SKIPPED")).toBe(1);
    expect(count((r) => r.status === "RUNNING")).toBe(1);
    expect(count((r) => r.tables.some((t) => t.scope_status === "count_mismatch"))).toBe(1);
    for (const status of ["expired", "contaminated", "empty"] as const)
      expect(
        count((r) => r.tables.some((t) => t.scope_status === status)),
        status,
      ).toBe(1);
    expect(count((r) => r.runner === "DirectRunner" && r.mode === "sampled")).toBe(1);
    expect(count((r) => r.tables.some((t) => t.reference_verified === false))).toBe(1);
    const unverified = rows.find((r) => r.tables.some((t) => t.reference_verified === false))!;
    const lifts = metricsOf(unverified.evaluation_id).filter((m) => m.metric_id.endsWith("memorization_lift"));
    expect(lifts.length).toBeGreaterThan(0);
    expect(lifts.every((m) => m.status === "not_evaluated")).toBe(true);
    const wide = rows.find((r) => r.tables.some((t) => t.name === "user_features"))!;
    expect(
      new Set(
        metricsOf(wide.evaluation_id)
          .map((m) => m.column_name)
          .filter(Boolean),
      ).size,
    ).toBe(200);
  });

  it("varies every generation parameter the GUI filters on", () => {
    const rows = [...latest.values()];
    const distinct = (f: (r: EvaluationDataHistoryRow) => unknown) => new Set(rows.map(f));
    expect(distinct((r) => r.engine)).toEqual(new Set(["b1_rag", "b2_library"]));
    expect(distinct((r) => r.llm_model_uri).size).toBe(3);
    expect(distinct((r) => r.embedder_id)).toEqual(new Set(["hashing-384", "bge-small-en-v1.5"]));
    expect(distinct((r) => r.retrieval_method)).toEqual(new Set(["centroid", "kcenter", "kcenter_rotate"]));
    expect(distinct((r) => r.seed)).toEqual(new Set(["derived", "42", "7"]));
    expect(distinct((r) => r.similarity)).toEqual(new Set([0.3, 0.5, 0.8]));
    expect(distinct((r) => r.reference_rows_limit)).toEqual(new Set([10_000, 50_000]));
    expect(distinct((r) => r.num_rows_requested)).toEqual(new Set([1_000_000, 10_000_000, 90_000_000]));
    expect(distinct((r) => r.source_stats_tier)).toEqual(new Set(["sample", "exact"]));
  });

  it("early b1 runs fail memorization; later runs pass it", () => {
    for (const row of finals().filter((r) => idx(r.evaluation_id) <= 10)) {
      const m = metricsOf(row.evaluation_id);
      expect(
        m.some((x) => x.metric_id === "row.memorization_lift" && x.status === "fail"),
        row.evaluation_id,
      ).toBe(true);
      expect(
        m.some(
          (x) => x.metric_id === "field.value_memorization_lift" && x.column_name === "email" && x.status === "fail",
        ),
        row.evaluation_id,
      ).toBe(true);
    }
    const clean = finals().filter((r) => idx(r.evaluation_id) >= 21 && ![25, 35].includes(idx(r.evaluation_id)));
    expect(clean.length).toBeGreaterThan(10);
    for (const row of clean) {
      const lifts = metricsOf(row.evaluation_id).filter((m) =>
        ["row.memorization_lift", "row.exposure_lift", "field.value_memorization_lift"].includes(m.metric_id),
      );
      expect(
        lifts.filter((m) => m.status === "fail"),
        row.evaluation_id,
      ).toEqual([]);
    }
  });

  it("the 512-value pool collapses on users.city early and not after identifier expansion", () => {
    const ceiling = (id: string) =>
      metricsOf(id).find((m) => m.metric_id === "column.distinct_ceiling_hit" && m.column_name === "city");
    expect(ceiling("eval-0001")?.value).toBe(1);
    expect(ceiling("eval-0001")?.status).toBe("fail");
    expect(ceiling("eval-0036")?.value).toBe(0);
  });

  it("fidelity and the overall score improve from weeks 1–2 to weeks 7–8", () => {
    const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
    const early = finals().filter((r) => idx(r.evaluation_id) <= 10);
    const late = finals().filter((r) => idx(r.evaluation_id) >= 31 && r.relationship_model);
    expect(mean(late.map((r) => r.fidelity_score!))).toBeGreaterThan(mean(early.map((r) => r.fidelity_score!)) + 0.03);
    expect(mean(late.map((r) => r.privacy_score!))).toBeGreaterThan(mean(early.map((r) => r.privacy_score!)) + 0.2);
    expect(mean(late.map((r) => r.overall_score!))).toBeGreaterThan(mean(early.map((r) => r.overall_score!)));
  });
});

describe("metrics recompute from the profiles with packages/stats", { timeout: 60_000 }, () => {
  it("column.ks, its baseline and its noise floor", () => {
    const rows = data.metrics.filter((m) => m.metric_id === "column.ks" && m.value !== null);
    expect(rows.length).toBeGreaterThan(100);
    for (const m of rows) {
      const src = profile(m, "histogram", "source", { column: m.column_name });
      const syn = profile(m, "histogram", "synthetic", { column: m.column_name });
      const ref = profile(m, "histogram", "reference", { column: m.column_name });
      expect(m.value!).toBeCloseTo(ksBracket(src.counts as number[], syn.counts as number[])!.dLo, 10);
      expect(m.baseline_value!).toBeCloseTo(ksBracket(src.counts as number[], ref.counts as number[])!.dLo, 10);
      expect(m.noise_floor!).toBeCloseTo(ksCritical(m.n_source!, m.n_synthetic!), 10);
    }
  });

  it("column.pit_w1 and the temporal-mix TVDs", () => {
    for (const m of data.metrics.filter((x) => x.metric_id === "column.pit_w1" && x.value !== null).slice(0, 300)) {
      const src = profile(m, "histogram", "source", { column: m.column_name });
      const syn = profile(m, "histogram", "synthetic", { column: m.column_name });
      expect(m.value!).toBeCloseTo(pitW1(src.counts as number[], syn.counts as number[])!, 10);
    }
    for (const m of data.metrics.filter((x) => x.metric_id === "column.dow_tvd").slice(0, 100)) {
      const src = profile(m, "temporal_mix", "source", { column: m.column_name });
      const syn = profile(m, "temporal_mix", "synthetic", { column: m.column_name });
      expect(m.value!).toBeCloseTo(tvd(src.dow as number[], syn.dow as number[])!, 10);
    }
  });

  it("column.tvd from full top-k profiles, and relationship.fanout_tvd from fan-out histograms", () => {
    let checked = 0;
    for (const m of data.metrics.filter((x) => x.metric_id === "column.tvd")) {
      const src = profile(m, "topk", "source", { column: m.column_name }) as {
        items: { label: string; count: number }[];
        other_count: number;
      };
      const syn = profile(m, "topk", "synthetic", { column: m.column_name }) as {
        items: { label: string; count: number }[];
        other_count: number;
      };
      if (src.other_count || syn.other_count) continue;
      const labels = [...new Set([...src.items, ...syn.items].map((i) => i.label))];
      const counts = (p: typeof src) => labels.map((l) => p.items.find((i) => i.label === l)?.count ?? 0);
      expect(m.value!).toBeCloseTo(tvd(counts(src), counts(syn))!, 10);
      checked += 1;
    }
    expect(checked).toBeGreaterThan(50);
    for (const m of data.metrics.filter((x) => x.metric_id === "relationship.fanout_tvd").slice(0, 100)) {
      const src = profile(m, "fanout_hist", "source", { edge: m.edge });
      const syn = profile(m, "fanout_hist", "synthetic", { edge: m.edge });
      expect(m.value!).toBeCloseTo(tvd(src.counts as number[], syn.counts as number[])!, 10);
    }
  });

  it("detection AUC equals its ROC profile; corr_rms_delta equals the matrices", () => {
    for (const m of data.metrics.filter((x) => x.metric_id === "table.detection_auc")) {
      expect(profile(m, "roc_curve", "both").auc).toBeCloseTo(m.value!, 12);
    }
    for (const m of data.metrics
      .filter((x) => x.metric_id === "table.corr_rms_delta" && x.value !== null)
      .slice(0, 60)) {
      const src = profile(m, "corr_matrix", "source").values as (number | null)[][];
      const syn = profile(m, "corr_matrix", "synthetic").values as (number | null)[][];
      const deltas: number[] = [];
      for (let i = 0; i < src.length; i += 1)
        for (let j = i + 1; j < src.length; j += 1) {
          const a = src[i]![j];
          const b = syn[i]![j];
          if (a !== null && a !== undefined && b !== null && b !== undefined) deltas.push(Math.abs(a - b));
        }
      expect(m.value!).toBeCloseTo(Math.sqrt(deltas.reduce((acc, d) => acc + d * d, 0) / deltas.length), 10);
    }
  });

  it("score and status follow the catalogue; table and model scores roll up the rows", () => {
    for (const m of data.metrics) {
      const metric = catalogueById[m.metric_id as MetricId];
      const reading = {
        value: m.value,
        ciLow: m.ci_low,
        ciHigh: m.ci_high,
        noiseFloor: m.noise_floor,
        sourceValue: m.source_value,
      };
      expect(m.status, `${m.evaluation_id} ${m.metric_id}`).toBe(statusFor(metric, reading));
      const score = scoreValue(metric, reading);
      if (score === null) expect(m.score).toBeNull();
      else expect(m.score!).toBeCloseTo(score, 9);
    }
    const exclude = (id: string) => catalogueById[id as MetricId].score === "none";
    for (const row of finals()) {
      const rows = metricsOf(row.evaluation_id);
      const tables = [...new Set(rows.filter((m) => m.level !== "model").map((m) => m.table_name))];
      const perTable = tables.map((t) => {
        const scores = tableFamilyScores(
          rows.filter((m) => m.table_name === t),
          { exclude },
        );
        const stored = rows.find((m) => m.table_name === t && m.metric_id === "table.fidelity_score")!;
        expect(stored.value ?? null).toBeCloseTo(scores.fidelity ?? Number.NaN, 9);
        return scores;
      });
      expect(row.overall_score!).toBeCloseTo(modelFamilyScores(perTable).overall!, 9);
      expect(row.metrics_total).toBe(rows.length);
      expect(row.metrics_fail).toBe(rows.filter((m) => m.status === "fail").length);
    }
  });
});
