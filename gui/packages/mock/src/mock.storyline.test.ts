/**
 * The storyline holds, and the numbers are computed, not typed: metric values
 * recompute from the stored profiles with packages/stats, noise floors come
 * from the same functions, scores roll up per the catalogue's rules.
 */
import { beforeAll, describe, expect, it } from "vitest";

import {
  catalogue,
  catalogueById,
  type EvaluationDataHistoryRow,
  type EvaluationMetricsRow,
  type EvaluationProfilesRow,
  type MetricId,
} from "@synthetic-platform/contracts";
import {
  aggregateScores,
  headlineCounts,
  isAggregateMetric,
  ksBracket,
  ksCritical,
  MODEL_KEY,
  pitW1,
  scoreRow,
  tvd,
  zeroToleranceIds,
} from "@synthetic-platform/stats";

import {
  canonicalTimestamp,
  dlqRuleById,
  LIFT_COUNT_KEYS,
  isCanonicalTimestamp,
  parseEdge,
  snapshotKey,
} from "@synthetic-platform/contracts";

import {
  catalogueVersionOf,
  createMockDataset,
  EDGES,
  edgeLabel,
  MOCK_RELATIONSHIP_MODEL,
  type MockDataset,
} from "./index";

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
    // No relationship model, so no model entry for the table: the evaluator's `standalone` role.
    expect([wide.relationship_model, wide.tables.map((t) => t.role)]).toEqual([null, ["standalone"]]);
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
    // The 60-row smoke evaluation (eval-0003) is too small for a lift to reach its thresholds.
    for (const row of finals().filter((r) => idx(r.evaluation_id) <= 10 && r.mode !== "sampled")) {
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

  it("column.tvd from full top-k profiles", () => {
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

  it("status, score and detail are what the evaluator's to_metric_row writes; scores roll up the rows", () => {
    for (const m of data.metrics) {
      const metric = catalogueById[m.metric_id as MetricId];
      const detail = (m.detail ?? {}) as Record<string, unknown>;
      // What the producer handed the scorer: the stored numbers, its own detail (the scorer's
      // notes are re-derived) and, for an edge, whether the launch enforced it.
      const { reason: _r, noise_downgraded_from: _d, noise_check: _c, nonfinite: _n, ...producer } = detail;
      // A stored NULL that detail.nonfinite marks was ±∞ when the scorer read it (json_safe).
      const inf = detail.nonfinite === "+inf" ? Infinity : detail.nonfinite === "-inf" ? -Infinity : null;
      const reading = {
        value: m.value ?? (catalogueById[m.metric_id as MetricId].uses_ci_bound ? null : inf),
        ciLow: m.ci_low,
        ciHigh: m.ci_high,
        noiseFloor: m.noise_floor,
        sourceValue: m.source_value,
        detail: m.status === "not_evaluated" && typeof detail.reason === "string" ? detail : producer,
        columnKind: m.column_kind,
      };
      const scored = scoreRow(metric, reading, { enforced: detail.enforced !== false });
      const where = `${m.evaluation_id} ${m.metric_id} ${m.table_name}.${m.column_name ?? m.edge ?? ""}`;
      expect(m.status, where).toBe(scored.status);
      expect(m.score, where).toBe(scored.score);
      expect(m.detail ?? null, where).toEqual(scored.detail);
    }
    const zeroTolerance = zeroToleranceIds(catalogue);
    for (const row of finals()) {
      const rows = metricsOf(row.evaluation_id);
      const measured = rows.filter((m) => !isAggregateMetric(m.metric_id));
      const aggregates = aggregateScores(measured, zeroTolerance);
      for (const table of Object.keys(aggregates).filter((t) => t !== MODEL_KEY)) {
        const stored = rows.find((m) => m.table_name === table && m.metric_id === "table.fidelity_score")!;
        expect(stored.value ?? null).toBeCloseTo(aggregates[table]!.fidelity ?? Number.NaN, 12);
      }
      expect(row.overall_score!).toBeCloseTo(aggregates[MODEL_KEY]!.overall!, 12);
      // headline_counts: aggregate ids excluded, and the five buckets reconcile (R37, R43).
      const counts = headlineCounts(rows);
      expect(row.metrics_total).toBe(measured.length);
      expect(row.metrics_total).toBe(counts.total);
      expect([
        row.metrics_fail,
        row.metrics_warn,
        row.metrics_pass,
        row.metrics_info,
        row.metrics_not_evaluated,
      ]).toEqual([counts.fail, counts.warn, counts.pass, counts.info, counts.not_evaluated]);
    }
  });
});

describe("the mock mirrors what the pipeline writes", { timeout: 60_000 }, () => {
  it("every relational model has a documented edge with an INFO orphan metric and a source baseline", () => {
    const documented = EDGES.filter((e) => !e.enforced);
    expect(documented.length).toBeGreaterThan(0);
    const modelEdges = MOCK_RELATIONSHIP_MODEL.tables.flatMap((t) => t.fk);
    expect(modelEdges.filter((e) => e.role === "documented" && !e.enforced)).toHaveLength(documented.length);
    for (const row of finals().filter((r) => r.relationship_model)) {
      for (const edge of documented) {
        const label = edgeLabel(edge);
        const rows = metricsOf(row.evaluation_id).filter((m) => m.edge === label);
        const orphan = rows.find((m) => m.metric_id === "relationship.orphan_rate");
        const source = rows.find((m) => m.metric_id === "relationship.orphan_rate_source");
        expect(orphan?.status, `${row.evaluation_id} ${label}`).toBe("info");
        expect(orphan?.detail).toMatchObject({ enforced: false, role: "documented" });
        expect(source?.value).toBeGreaterThan(0);
      }
    }
    // Labels are the evaluator's form; an external parent keeps its dataset.
    for (const edge of EDGES) expect(parseEdge(edgeLabel(edge))?.child).toBe(edge.child);
    expect(EDGES.filter((e) => e.external).map(edgeLabel)).toEqual([
      "order_items(product_id) -> synthetic_data.products(id)",
    ]);
  });

  it("every not_evaluated metric says why", () => {
    const missing = data.metrics.filter(
      (m) =>
        m.status === "not_evaluated" &&
        !(m.detail && typeof m.detail === "object" && typeof (m.detail as { reason?: unknown }).reason === "string"),
    );
    expect(missing.map((m) => `${m.evaluation_id} ${m.metric_id}`).slice(0, 5)).toEqual([]);
    expect(data.metrics.filter((m) => m.metric_id.startsWith("pair.") && m.status === "not_evaluated").length).toBe(72);
  });

  it("interval-method rows carry their CI, never a scalar floor, as the producers do (Ruling R41)", () => {
    const interval = new Set(["wilson", "newcombe", "delong"]);
    const scalar = new Set(["ks_two_sample", "tvd_null", "jsd_null", "fisher_z", "mi_bias"]);
    for (const m of data.metrics) {
      const where = `${m.evaluation_id} ${m.metric_id}`;
      if (m.noise_floor_method && interval.has(m.noise_floor_method)) expect(m.noise_floor, where).toBeNull();
      if (m.noise_floor_method === null || m.noise_floor_method === "rate_ratio")
        expect(m.noise_floor, where).toBeNull();
      // Every graded crossing had the input its noise check needs — but for the one staged gap.
      if (m.metric_id === "row.exact_match_rate_nonkey" && m.evaluation_id === "eval-0006") continue;
      if (m.status === "warn" || m.status === "fail") {
        if (m.noise_floor_method && interval.has(m.noise_floor_method)) {
          expect(m.ci_low, where).not.toBeNull();
          expect(m.ci_high, where).not.toBeNull();
        }
        if (m.noise_floor_method && scalar.has(m.noise_floor_method)) expect(m.noise_floor, where).not.toBeNull();
      }
      expect((m.detail as { noise_check?: string } | null)?.noise_check, where).toBeUndefined();
    }
  });

  it("shows every scorer state the EVALUATION tab explains", () => {
    const detailOf = (m: EvaluationMetricsRow) => (m.detail ?? {}) as Record<string, unknown>;
    // R40: a crossing within sampling noise is PASS, says what it was — WARN and FAIL alike —
    // and scores at the reference (the smoke run's wide floors make several).
    const downgraded = data.metrics.filter((m) => detailOf(m).noise_downgraded_from);
    for (const from of ["warn", "fail"])
      expect(
        downgraded.some((m) => detailOf(m).noise_downgraded_from === from),
        `was ${from}`,
      ).toBe(true);
    for (const m of downgraded) {
      expect(m.status).toBe("pass");
      expect(m.score).toBe(1);
    }
    // R41: a WARN/FAIL whose noise input is missing keeps its status and says the check was unavailable.
    const unavailable = data.metrics.filter((m) => detailOf(m).noise_check === "unavailable");
    expect(unavailable.map((m) => `${m.evaluation_id} ${m.table_name} ${m.metric_id} ${m.status}`).sort()).toEqual([
      "eval-0006 order_items row.exact_match_rate_nonkey fail",
      "eval-0006 orders row.exact_match_rate_nonkey fail",
      "eval-0006 users row.exact_match_rate_nonkey fail",
    ]);
    // R43: an infinite value past the bad side is a FAIL stored as NULL with detail.nonfinite.
    const nonfinite = data.metrics.filter((m) => detailOf(m).nonfinite);
    expect(nonfinite.map((m) => [m.evaluation_id, m.metric_id, m.column_name, m.status, m.value, m.score])).toEqual([
      ["eval-0009", "column.std_ratio", "age", "fail", null, 0],
    ]);
    // R38: a lift with no copies on either side has no value but PASSes on ci_low 0 at score 1 …
    const lifts = data.metrics.filter((m) => m.metric_id.endsWith("_lift") && m.status !== "not_evaluated");
    const clean = lifts.filter((m) => m.value === null && m.ci_low === 0);
    expect(clean.length).toBeGreaterThan(0);
    for (const m of clean) {
      expect(m.status).toBe("pass");
      expect(m.score).toBe(1);
    }
    // … and one with copies only in R stores NULL too (Python's rate_ratio gives +∞), gated on ci_low > 0.
    const holdoutZero = lifts.filter((m) => {
      // The two event counts sit under the producer's keys for that lift (copies_*, events_*).
      const [reference, holdout] = LIFT_COUNT_KEYS[m.metric_id as keyof typeof LIFT_COUNT_KEYS];
      const d = detailOf(m) as Record<string, number | undefined>;
      return d[holdout] === 0 && (d[reference] ?? 0) > 0;
    });
    expect(holdoutZero.length).toBeGreaterThan(0);
    for (const m of holdoutZero) {
      expect(m.value, `${m.evaluation_id} ${m.metric_id}`).toBeNull();
      expect(m.ci_low!).toBeGreaterThan(0);
      expect(m.ci_high).toBeNull();
    }
    expect(holdoutZero.some((m) => m.status === "fail")).toBe(true);
    // R42: documented edges' orphan rates are INFO; R39: zero-tolerance FAILs exist to badge.
    expect(data.metrics.some((m) => m.metric_id === "relationship.orphan_rate" && m.status === "info")).toBe(true);
    const zeroTolerance = zeroToleranceIds(catalogue);
    expect(data.metrics.some((m) => zeroTolerance.has(m.metric_id) && m.status === "fail")).toBe(true);
  });

  it("DLQ rows carry the rule's error_type, step and stage exactly as the code writes them", () => {
    const rules = new Set(data.dlq.map((d) => d.rule_id));
    for (const rule of ["fk.orphan", "schema.types", "schema.batch", "row.duplicate", "pk.duplicate", "engine_failure"])
      expect(rules.has(rule), rule).toBe(true);
    expect(rules.has("null.required")).toBe(false);
    // fk.unmatched needs a conditional edge: an enforced, non-driving FK sharing a column with the
    // table's driving FK (ADR 0037). thelook_demo has none, so the pipeline could never write one.
    for (const table of MOCK_RELATIONSHIP_MODEL.tables) {
      const driving = table.fk.find((fk) => fk.drives);
      const conditional = table.fk.filter(
        (fk) => fk !== driving && fk.enforced && !fk.external && fk.cols.some((c) => driving?.cols.includes(c)),
      );
      expect(conditional, table.name).toEqual([]);
    }
    expect(rules.has("fk.unmatched")).toBe(false);
    for (const row of data.dlq) {
      const rule = dlqRuleById.get(row.rule_id ?? "")!;
      expect(rule.emitted, row.rule_id!).toBe(true);
      expect([row.error_type, row.pipeline_step, row.stage], row.rule_id!).toEqual([
        rule.error_type,
        rule.pipeline_step,
        rule.stage,
      ]);
    }
    const orphan = data.dlq.find((d) => d.rule_id === "fk.orphan")!;
    expect([orphan.error_type, orphan.pipeline_step]).toEqual(["referential_integrity", "EnforceFkIntegrityDoFn"]);
  });

  it("error_detail and raw_record follow each DoFn's envelope", () => {
    // PanderaValidateBatchDoFn._summarize_for_row: {failure_count, first_failures: [str()'d failure-case rows]}.
    const batch = data.dlq.find((d) => d.rule_id === "schema.batch")!;
    const summary = JSON.parse(batch.error_detail!) as {
      failure_count: number;
      first_failures: Record<string, unknown>[];
    };
    expect(Object.keys(summary).sort()).toEqual(["failure_count", "first_failures"]);
    expect(summary.failure_count).toBe(summary.first_failures.length);
    for (const failure of summary.first_failures) {
      expect(Object.keys(failure).sort()).toEqual([
        "check",
        "check_number",
        "column",
        "failure_case",
        "index",
        "schema_context",
      ]);
      for (const cell of Object.values(failure)) expect(typeof cell).toBe("string");
    }
    // GenerateRecordsDoFn: f"{type(e).__name__}: {e}" over _failed_request's key-batch summary.
    const crash = data.dlq.find((d) => d.rule_id === "engine_failure")!;
    expect(JSON.parse(crash.error_detail!)).toMatch(/^[A-Za-z]+Error: \S/);
    expect(crash.raw_record).toMatch(
      /^\{"batch_id":\d{18,19},"keys":\[\[\d+\](,\[\d+\]){0,9}\],"keys_total":\d+,"n":\d+\}$/,
    );
    // pydantic ValidationError.errors(): a list of {type, loc, msg, input}.
    const types = JSON.parse(data.dlq.find((d) => d.rule_id === "schema.types")!.error_detail!) as object[];
    expect(Object.keys(types[0]!).sort()).toEqual(["input", "loc", "msg", "type"]);
  });

  it("the sample tier keeps a primary key distinct: distinct = the sampled rows", () => {
    const pks = data.sourceStats.filter((r) => r.is_pk && r.stats_tier === "sample");
    expect(pks.length).toBeGreaterThan(0);
    for (const r of pks) {
      expect(r.distinct, `${r.table_fqn}.${r.column}`).toBe(Math.round(r.sample_rows! * (1 - r.null_fraction!)));
      expect(r.distinct_ratio).toBe(1);
    }
  });

  it("per-table run ids are <base>-NN-<table> in generation order; one table keeps the base", () => {
    for (const row of [...latest.values()]) {
      const base = row.base_run_id!;
      if (row.relationship_model)
        expect(row.run_ids).toEqual([`${base}-00-users`, `${base}-01-orders`, `${base}-02-order_items`]);
      else expect(row.run_ids).toEqual([base]);
      for (const t of row.tables) if (t.role !== "external") expect(row.run_ids).toContain(t.run_id);
    }
    const runIds = new Set(data.validationRuns.map((r) => r.run_id));
    for (const row of [...latest.values()]) for (const id of row.run_ids) expect(runIds.has(id), id).toBe(true);
  });

  it("the catalogue version changes once across the storyline", () => {
    const versions = [...latest.values()].map((r) => [idx(r.evaluation_id), r.catalogue_version] as const);
    expect(new Set(versions.map(([, v]) => v))).toEqual(new Set(["0.9.0", "1.0.0"]));
    for (const [i, v] of versions) expect(v).toBe(catalogueVersionOf({ index: i }));
  });

  it("one reference digest is profiled on both tiers; a legacy snapshot has a NULL tier", () => {
    const tiersByDigest = new Map<string, Set<string>>();
    for (const r of data.sourceStats) {
      const tiers = tiersByDigest.get(`${r.table_fqn}|${r.reference_digest}`) ?? new Set<string>();
      tiers.add(r.stats_tier ?? "NULL");
      tiersByDigest.set(`${r.table_fqn}|${r.reference_digest}`, tiers);
    }
    const both = [...tiersByDigest.entries()].filter(([, t]) => t.has("sample") && t.has("exact"));
    expect(both.map(([k]) => k.split("|")[0]!.split(".").at(-1))).toEqual(
      expect.arrayContaining(["users", "orders", "order_items"]),
    );
    const legacy = data.sourceStats.filter((r) => r.stats_tier === null);
    expect(legacy.length).toBeGreaterThan(5);
    expect(new Set(legacy.map(snapshotKey)).size).toBe(1);
    expect(JSON.parse(legacy[0]!.stats!)).not.toHaveProperty("stats_tier");
    // Keys never collide across tiers of one digest.
    const keys = new Set(data.sourceStats.map(snapshotKey));
    const pairs = new Set(data.sourceStats.map((r) => `${r.reference_digest}|${r.stats_tier}|${r.run_id}`));
    expect(keys.size).toBe(pairs.size);
  });

  it("every TIMESTAMP is canonical (six fraction digits), and evaluated_at keeps its microseconds", () => {
    const stamps = [
      ...data.registry.flatMap((r) => [r.recorded_at, r.evaluated_at, r.finished_at]),
      ...data.metrics.slice(0, 2000).map((m) => m.evaluated_at),
      ...data.validationRuns.map((r) => r.created_at),
      ...data.dlq.map((d) => d.dlq_inserted_at),
      ...data.sourceStats.map((r) => r.computed_at),
      ...data.fanoutStats.map((r) => r.measured_at),
      ...data.rag.flatMap((s) => s.chunks.slice(0, 3).map((c) => c.created_at)),
    ].filter((t): t is string => t !== null);
    expect(stamps.filter((t) => !isCanonicalTimestamp(t)).slice(0, 3)).toEqual([]);
    const evaluated = [...latest.values()].map((r) => r.evaluated_at);
    expect(evaluated.every((t) => !t.endsWith("000Z"))).toBe(true);
    expect(canonicalTimestamp(evaluated[0]!)).toBe(evaluated[0]);
  });
});
