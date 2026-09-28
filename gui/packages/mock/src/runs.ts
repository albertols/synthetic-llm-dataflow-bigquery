/**
 * Generation-side tables: `validation_runs` (one row per table per launch),
 * sample `dlq` rows, and `fk_fanout_stats` measurements. BLOCKER accounting
 * follows the code, not the docs: only the rule ids in the knob
 * `blocker_rule_ids` (BLOCKER_RULE_IDS) count toward observed_blocker_ratio,
 * so fk.orphan diverts rows without moving the gate (see the knob annotations).
 */
import { knobs, type DlqRow, type FkFanoutStatsRow, type ValidationRunsRow } from "@synthetic-platform/contracts";
import { Random, seedFrom, sha256Hex } from "@synthetic-platform/stats";

import { baseRunId, referenceDigest, rowsFor } from "./ids";
import { MODEL_URIS, type EvalSpec } from "./storyline";
import { EDGES, landingFqn, sourceFqn, TABLES } from "./thelook";

const knobValue = (id: string) => knobs.knobs.find((k) => k.id === id)?.value;
const BLOCKER_COUNTED = new Set((knobValue("blocker_rule_ids") as string[] | undefined) ?? []);

const STEPS: Record<string, [string, string]> = {
  "row.duplicate": ["uniqueness", "DedupRowsDoFn"],
  "pk.duplicate": ["uniqueness", "DedupRowsDoFn"],
  "identity.unique": ["uniqueness", "DedupRowsDoFn"],
  "schema.types": ["pydantic", "ValidateRecordsDoFn"],
  "null.required": ["pandera", "ValidateBatchDoFn"],
  "fk.orphan": ["engine", "CheckForeignKeysDoFn"],
  engine_failure: ["engine", "GenerateRecordsDoFn"],
};

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

function sampleRecord(table: string, rng: Random): Record<string, unknown> {
  const def = TABLES[table]!;
  return def.row(rng, rng.int(0, 99_999));
}

export function buildRuns(storyline: readonly EvalSpec[], seed: number) {
  const launches: Launch[] = storyline.map((spec) => ({
    spec,
    base: baseRunId(spec),
    createdAt: new Date(Date.parse(spec.evaluatedAt) - 30 * 60_000).toISOString().replace(".000Z", "Z"),
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
      createdAt: extra.day,
      ...(extra.blocker ? { forceBlocker: extra.blocker } : {}),
    });
  });

  const validationRuns: ValidationRunsRow[] = [];
  const dlq: DlqRow[] = [];
  for (const launch of launches) {
    const { spec } = launch;
    const rng = new Random(seedFrom(seed, "runs", spec.id));
    const tables = spec.relational ? ["users", "orders", "order_items"] : spec.tables;
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
      const runId = `${launch.base}-${name}`;
      validationRuns.push({
        run_id: runId,
        reference_digest: referenceDigest(name, spec.referenceRowsLimit, spec.sourceStatsTier),
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
        const [errorType, step] = STEPS[rule] ?? ["engine", "GenerateRecordsDoFn"];
        for (let i = 0; i < Math.min(3, count); i += 1) {
          const record = sampleRecord(name, rng);
          dlq.push({
            dlq_inserted_at: new Date(Date.parse(launch.createdAt) - (i + 1) * 47_000)
              .toISOString()
              .replace(".000Z", "Z"),
            run_id: runId,
            raw_record: JSON.stringify(record),
            error_type: errorType,
            error_detail: JSON.stringify(
              rule === "fk.orphan"
                ? { rule, edge: "order_items.product_id->products.id", missing_key: [9_000_000 + i] }
                : rule === "schema.types"
                  ? { rule, column: "age", expected: "INT64", got: "N/A" }
                  : { rule, key: Object.values(record).slice(0, 1), occurrences: 2 },
            ),
            rule_id: rule,
            pipeline_step: step,
            stage: "pre_write",
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
      model_sha: sha256Hex(`config/relationships/gcp_public_fk_example.yaml@gcp_public_thelook`).slice(0, 12),
      measured_at: new Date(Date.UTC(2026, 7, 3, 6, 50 + i)).toISOString().replace(".000Z", "Z"),
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
