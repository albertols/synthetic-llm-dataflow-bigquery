/**
 * Generation-side tables: `validation_runs` (one row per table per launch),
 * sample `dlq` rows, and `fk_fanout_stats` measurements. BLOCKER accounting
 * follows the code, not the docs: only the rule ids in the knob
 * `blocker_rule_ids` (BLOCKER_RULE_IDS) count toward observed_blocker_ratio,
 * so fk.orphan diverts rows without moving the gate (see the knob annotations).
 *
 * Every DLQ row's error_type, pipeline_step and stage come from the generated rule
 * map (`dlqRuleById`, exported from the DoFns and `dlq.normalize_dlq_record`), and
 * its error_detail / raw_record follow the envelope each DoFn builds. The mock never
 * writes two rules: `null.required` is declared in thresholds.yml but no DoFn emits
 * it, and `fk.unmatched` needs a non-nullable conditional edge (an FK sharing columns
 * with the driving edge, ADR 0037 ruling B) — thelook_demo has none: order_items'
 * other edges are the documented user_id and the external product_id.
 */
import {
  dlqRuleById,
  knobs,
  type DlqRow,
  type FkFanoutStatsRow,
  type ValidationRunsRow,
} from "@synthetic-platform/contracts";
import { Random, seedFrom, sha256Hex } from "@synthetic-platform/stats";

import { baseRunId, isoMicros, launchDigest, rowsFor, tableRunIds } from "./ids";
import { MODEL_URIS, type EvalSpec } from "./storyline";
import { EDGES, landingFqn, MOCK_RELATIONSHIP_MODEL, sourceFqn, TABLES } from "./thelook";

const knobValue = (id: string) => knobs.knobs.find((k) => k.id === id)?.value;
const BLOCKER_COUNTED = new Set((knobValue("blocker_rule_ids") as string[] | undefined) ?? []);

/** error_type / pipeline_step / stage exactly as the pipeline writes them (generated/dlq_rules.json). */
function ruleShape(rule: string): { errorType: string; step: string; stage: string } {
  const known = dlqRuleById.get(rule);
  if (!known?.emitted || !known.error_type) throw new Error(`the pipeline never emits DLQ rule "${rule}"`);
  return { errorType: known.error_type, step: known.pipeline_step ?? "", stage: known.stage ?? "pre_write" };
}

/** The DoFn envelope's (raw_record, error_detail) for one DLQ row of `rule`. */
function envelope(rule: string, record: Record<string, unknown>, i: number) {
  switch (rule) {
    case "fk.orphan":
      return {
        raw: record,
        detail: `product_id=(${9_000_000 + i},) is not a landed parent key — the row references a parent that does not exist`,
      };
    case "schema.types":
      return {
        raw: { ...record, age: "N/A" },
        detail: [
          {
            type: "int_parsing",
            loc: ["age"],
            msg: "Input should be a valid integer, unable to parse string as an integer",
            input: "N/A",
          },
        ],
      };
    case "schema.batch":
      // PanderaValidateBatchDoFn._summarize_for_row: the row's failure cases, every cell str()'d.
      return {
        raw: record,
        detail: {
          failure_count: 1,
          first_failures: [
            {
              schema_context: "Column",
              column: "num_of_item",
              check: "greater_than_or_equal_to(1)",
              check_number: "0",
              failure_case: "0",
              index: String(17 + i),
            },
          ],
        },
      };
    case "engine_failure":
      // GenerateRecordsDoFn: `f"{type(e).__name__}: {e}"`, and for a key batch (orders is driven
      // by users) `_failed_request`'s summary — batch_id, n, a ≤ 10-key sample and keys_total.
      return {
        raw: {
          batch_id: BATCH_ID_PLACEHOLDER,
          n: 10_000,
          keys: Array.from({ length: 10 }, (_, k) => [1 + (((i * 10 + k) * 7919) % 100_000)]),
          keys_total: 7_937,
        },
        detail: "APITimeoutError: Request timed out.",
      };
    default:
      return { raw: record, detail: `${rule}: duplicate of an earlier record in this run` };
  }
}

interface Launch {
  spec: Pick<
    EvalSpec,
    | "id"
    | "evaluatedAt"
    | "engine"
    | "llm"
    | "env"
    | "numRows"
    | "referenceRowsLimit"
    | "sourceStatsTier"
    | "uniquenessMode"
    | "tables"
    | "relational"
    | "quality"
  >;
  base: string;
  createdAt: string;
  forceBlocker?: { table: string; rule: string; share: number };
}

/** Launches the registry never evaluated (so the runs view has more than the evaluations). */
const EXTRA_LAUNCHES: {
  day: string;
  engine: EvalSpec["engine"];
  llm: EvalSpec["llm"];
  blocker?: Launch["forceBlocker"];
}[] = [
  {
    day: "2026-08-08T19:10:00Z",
    engine: "b1_rag",
    llm: "gemma4-e4b",
    blocker: { table: "orders", rule: "pk.duplicate", share: 0.231 },
  },
  { day: "2026-08-22T06:45:00Z", engine: "b1_rag", llm: "gemma4-e4b" },
  { day: "2026-09-05T21:30:00Z", engine: "b2_library", llm: "qwen2.5-7b" },
  { day: "2026-09-19T05:55:00Z", engine: "b1_rag", llm: "gemma4-26b-a4b-awq" },
  { day: "2026-09-26T12:15:00Z", engine: "b2_library", llm: "gemma4-26b-a4b-awq" },
];

/**
 * A key batch's id is `blake2b(repr(keys[0]), 8 bytes) >> 1` (pipeline.py): a 63-bit
 * integer, past JavaScript's safe range, so it is spliced into the JSON as digits.
 */
const BATCH_ID_PLACEHOLDER = "__batch_id__";
function keyBatchId(runId: string, i: number): string {
  // Its own stream: the launch's seeded draws (and every number after them) stay put.
  const rng = new Random(seedFrom("dlq-batch-id", runId, i));
  return `${rng.int(1_000_000_000, 4_611_686_017)}${String(rng.int(0, 999_999_999)).padStart(9, "0")}`;
}

function sampleRecord(table: string, rng: Random): Record<string, unknown> {
  const def = TABLES[table]!;
  return def.row(rng, rng.int(0, 99_999));
}

export function buildRuns(storyline: readonly EvalSpec[], seed: number) {
  const launches: Launch[] = storyline.map((spec) => ({
    spec,
    base: baseRunId(spec),
    createdAt: isoMicros(Date.parse(spec.evaluatedAt) - 30 * 60_000, 412),
  }));
  EXTRA_LAUNCHES.forEach((extra, i) => {
    const template = storyline[0]!;
    const spec: Launch["spec"] = {
      ...template,
      id: `launch-extra-${i + 1}`,
      evaluatedAt: extra.day,
      engine: extra.engine,
      llm: extra.llm,
      quality: { ...template.quality, valueLeak: 0, rowLeak: 0, poolCollapse: false, orphanShare: 0, invalidShare: 0 },
    };
    launches.push({
      spec,
      base: `sdfb-${extra.day.slice(0, 16).replace(/[-:]/g, "").replace("T", "-")}-${sha256Hex(spec.id).slice(0, 4)}`,
      createdAt: isoMicros(Date.parse(extra.day), 250),
      ...(extra.blocker ? { forceBlocker: extra.blocker } : {}),
    });
  });

  const validationRuns: ValidationRunsRow[] = [];
  const dlq: DlqRow[] = [];
  for (const launch of launches) {
    const { spec } = launch;
    const rng = new Random(seedFrom(seed, "runs", spec.id));
    const tables = spec.relational ? ["users", "orders", "order_items"] : spec.tables;
    const runIds = tableRunIds(launch.base, tables);
    for (const name of tables) {
      const table = TABLES[name]!;
      const rows = rowsFor(spec, table);
      const byRule: Record<string, number> = {};
      const add = (rule: string, count: number) => {
        if (count > 0) byRule[rule] = (byRule[rule] ?? 0) + count;
      };
      add("row.duplicate", rng.poisson(rows * 3e-7));
      if (spec.uniquenessMode === "streaming") add("pk.duplicate", rng.poisson(rows * 2e-7));
      if (name === "users") add("schema.types", rng.binomial(rows, spec.quality.invalidShare));
      if (name === "order_items") add("fk.orphan", rng.binomial(rows, spec.quality.orphanShare));
      // fk.unmatched is impossible here (no conditional edge; see the header). The draw that
      // used to size it stays, so every seeded number after it keeps its value.
      if (name === "order_items" && spec.engine === "b1_rag" && spec.quality.temporalBlend) rng.poisson(3);
      if (name === "orders" && spec.engine === "b2_library") add("schema.batch", rng.poisson(rows * 4e-7));
      if (spec.quality.drift >= 0.05 && name === "orders") add("engine_failure", 10_000);
      if (launch.forceBlocker?.table === name)
        add(launch.forceBlocker.rule, Math.round(rows * launch.forceBlocker.share));
      const dlqCount = Object.values(byRule).reduce((a, b) => a + b, 0);
      const blockerCount = Object.entries(byRule).reduce(
        (acc, [rule, c]) => acc + (BLOCKER_COUNTED.has(rule) ? c : 0),
        0,
      );
      const generated = rows;
      const ratio = Number(knobValue(`blocker_failure_ratio_${spec.env}`) ?? 0.2);
      const observed = blockerCount / Math.max(generated, 1);
      const driven = name !== "users" && name !== "user_features";
      const repeatSource = driven ? (name === "orders" ? 0.358 : 0.412) : null;
      const repeatLanding = repeatSource === null ? null : repeatSource + rng.normal(0, 0.004);
      const runId = runIds.get(name)!;
      validationRuns.push({
        run_id: runId,
        reference_digest: launchDigest(spec, name),
        reference_table: sourceFqn(name),
        landing_table: landingFqn(name),
        engine: spec.engine,
        model_uri: MODEL_URIS[spec.llm],
        env: spec.env,
        num_rows_requested: rows,
        valid_count: generated - dlqCount,
        dlq_count: dlqCount,
        blocker_count: blockerCount,
        dlq_by_rule: JSON.stringify(byRule),
        blocker_failure_ratio: ratio,
        observed_blocker_ratio: observed,
        status: observed > ratio ? "FAILED_BLOCKER" : "PASSED",
        excluded_blocker_rules: null,
        source_repeat_share: repeatSource,
        landing_repeat_share: repeatLanding,
        repeat_share_delta: repeatSource === null || repeatLanding === null ? null : repeatLanding - repeatSource,
        repeat_share_within_tolerance:
          repeatSource === null || repeatLanding === null ? null : Math.abs(repeatLanding - repeatSource) <= 0.05,
        created_at: launch.createdAt,
      });
      for (const [rule, count] of Object.entries(byRule)) {
        const { errorType, step, stage } = ruleShape(rule);
        for (let i = 0; i < Math.min(3, count); i += 1) {
          const { raw, detail } = envelope(rule, sampleRecord(name, rng), i);
          let rawJson = JSON.stringify(raw, Object.keys(raw).sort());
          if (rule === "engine_failure") rawJson = rawJson.replace(`"${BATCH_ID_PLACEHOLDER}"`, keyBatchId(runId, i));
          dlq.push({
            dlq_inserted_at: isoMicros(Date.parse(launch.createdAt) - (i + 1) * 47_000, 100 + i),
            run_id: runId,
            raw_record: rawJson,
            error_type: errorType,
            error_detail: JSON.stringify(detail),
            rule_id: rule,
            pipeline_step: step,
            stage,
          });
        }
      }
    }
  }

  const fanoutStats: FkFanoutStatsRow[] = EDGES.filter((e) => e.drives).map((edge, i) => {
    const parents = TABLES[edge.parent]!.sourceRows;
    const histogram = Object.fromEntries(edge.fanout.map((share, k) => [String(k), Math.round(share * parents)]));
    return {
      source_table: sourceFqn(edge.child),
      edge_cols: edge.cols.join(","),
      model_sha: MOCK_RELATIONSHIP_MODEL.sha12,
      measured_at: isoMicros(Date.UTC(2026, 7, 3, 6, 50 + i), 500),
      payload: JSON.stringify({
        histogram,
        mean: edge.fanout.reduce((acc, s, k) => acc + s * k, 0),
        pk_cells: edge.cols.length,
        pk: { rows: TABLES[edge.child]!.sourceRows, key_tuples: TABLES[edge.child]!.sourceRows, max_rows_per_key: 1 },
      }),
    };
  });
  return { validationRuns, dlq, fanoutStats };
}
