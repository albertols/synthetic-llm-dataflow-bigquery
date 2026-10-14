/**
 * ADR 0038 model adjustments, as the GUI reads them off the generator's own
 * `validation_runs` row: the rule a launch left out of one table's BLOCKER
 * gate (`excluded_blocker_rules`), the two key-repeat shares it compared, and
 * the verdict — plus the join of an evaluation's tables to those rows.
 *
 * A re-reading layered on the evaluator's rows, never a change to them: the
 * evaluator grades `table.pk_duplicate_rate` against the DECLARED key
 * (any value above 0 fails), while the launch, on a full-source measurement,
 * stopped treating that key as unique and copied the source's repetition
 * instead. Both are right; this module carries the generator's half.
 *
 * Dependency-free (no zod): the web shell may import it. Its zod twin,
 * `tableModelAdjustmentSchema`, is in api.ts.
 */
import type { ValidationRunsRow } from "../generated/schemas";
import { canonicalTimestamp } from "./timestamps";

/** The DLQ rule an adjusted table stops gating on (ADR 0038 D3); every other rule still blocks. */
export const PK_DUPLICATE_RULE = "pk.duplicate";

/**
 * How one table's declared key was treated by the launch, read off its `validation_runs` row:
 * - `not_adjusted` — the row's gate still weighs `pk.duplicate`: the normal "unique by construction" rule;
 * - `faithful` — excluded, and the landing repeat share is within tolerance of the source's;
 * - `under` / `over` — excluded and outside tolerance: the synthetic key repeats less / more than the source's;
 * - `unmeasured` — excluded, but the launch measured no source share for this table, so it wrote none (D4);
 * - `unknown` — no `validation_runs` row could be joined to the evaluation's table.
 */
export const adjustmentVerdicts = ["not_adjusted", "faithful", "under", "over", "unmeasured", "unknown"] as const;
export type AdjustmentVerdict = (typeof adjustmentVerdicts)[number];

/** Which key joined the table to its row (in this order; `joinModelAdjustments`). */
export const adjustmentJoins = ["run_id", "base_run_id", "window"] as const;
export type AdjustmentJoin = (typeof adjustmentJoins)[number];

/** One table of an evaluation with the generator's verdict on its declared key. */
export interface TableModelAdjustment {
  /** `tables[].name`, else the landing table's short name. */
  table: string;
  /** The joined `validation_runs.run_id`; null when no row was joined. */
  run_id: string | null;
  /** `pk.duplicate` is in `excluded_rules`; null when no row was joined. */
  adjusted: boolean | null;
  /** `excluded_blocker_rules`, split (the column is comma-separated text). */
  excluded_rules: string[];
  source_repeat_share: number | null;
  landing_repeat_share: number | null;
  /** landing − source. */
  repeat_share_delta: number | null;
  /** `repeat_share_within_tolerance`: the generator's own verdict, at its own tolerance. */
  within_tolerance: boolean | null;
  /** `dlq_by_rule["pk.duplicate"]`: rows beyond the first per key, counted and (on an adjusted table) not removed. */
  pk_duplicate_dlq: number | null;
  verdict: AdjustmentVerdict;
  joined_by: AdjustmentJoin | null;
}

/** "pk.duplicate, row.duplicate" → both ids; NULL and "" (what an unadjusted table writes) → none. */
export function parseExcludedRules(text: string | null | undefined): string[] {
  return (text ?? "")
    .split(",")
    .map((rule) => rule.trim())
    .filter(Boolean);
}

type RunGate = Pick<
  ValidationRunsRow,
  | "excluded_blocker_rules"
  | "source_repeat_share"
  | "landing_repeat_share"
  | "repeat_share_delta"
  | "repeat_share_within_tolerance"
>;

/** landing − source, as written, else from the two shares the row kept. */
function deltaOf(run: RunGate): number | null {
  if (run.repeat_share_delta !== null) return run.repeat_share_delta;
  if (run.landing_repeat_share === null || run.source_repeat_share === null) return null;
  return run.landing_repeat_share - run.source_repeat_share;
}

/**
 * The verdict of one `validation_runs` row (null = no row was joined). ADR 0038: the stored
 * `repeat_share_within_tolerance` is the generator's verdict and wins — it is never recomputed
 * here against a tolerance the row may not have been written with.
 */
export function adjustmentVerdict(run: RunGate | null | undefined): AdjustmentVerdict {
  if (!run) return "unknown";
  if (!parseExcludedRules(run.excluded_blocker_rules).includes(PK_DUPLICATE_RULE)) return "not_adjusted";
  if (run.repeat_share_within_tolerance === true) return "faithful";
  const delta = deltaOf(run);
  if (run.repeat_share_within_tolerance === false && delta !== null && delta !== 0) return delta < 0 ? "under" : "over";
  return "unmeasured";
}

/** What the join reads of a `validation_runs` row (the BFF's `ValidationRun`, a row plus its parsed rule counts). */
export type AdjustmentRun = RunGate &
  Pick<ValidationRunsRow, "run_id" | "landing_table" | "created_at"> & {
    dlq_by_rule_map: Readonly<Record<string, number>>;
  };

export function tableModelAdjustmentOf(
  table: string,
  joined: { run: AdjustmentRun; joinedBy: AdjustmentJoin } | null,
): TableModelAdjustment {
  if (!joined)
    return {
      table,
      run_id: null,
      adjusted: null,
      excluded_rules: [],
      source_repeat_share: null,
      landing_repeat_share: null,
      repeat_share_delta: null,
      within_tolerance: null,
      pk_duplicate_dlq: null,
      verdict: "unknown",
      joined_by: null,
    };
  const { run, joinedBy } = joined;
  const excluded = parseExcludedRules(run.excluded_blocker_rules);
  return {
    table,
    run_id: run.run_id,
    adjusted: excluded.includes(PK_DUPLICATE_RULE),
    excluded_rules: excluded,
    source_repeat_share: run.source_repeat_share,
    landing_repeat_share: run.landing_repeat_share,
    repeat_share_delta: deltaOf(run),
    within_tolerance: run.repeat_share_within_tolerance,
    pk_duplicate_dlq: run.dlq_by_rule_map[PK_DUPLICATE_RULE] ?? null,
    verdict: adjustmentVerdict(run),
    joined_by: joinedBy,
  };
}

// ------------------------------------------------------------------- join --

/** What the join reads of a registry row (`evaluation_data_history`). */
export interface JoinableEvaluation {
  base_run_id: string | null;
  generation_started_at: string | null;
  generation_finished_at: string | null;
  tables: readonly {
    name: string | null;
    landing_table: string | null;
    run_id: string | null;
    role: string | null;
  }[];
}

/** A Beam table spec (`project:dataset.table`) and the dotted name are one table (the evaluator's `_dotted`). */
export function dottedTable(table: string): string {
  return table.trim().replace(":", ".");
}

const shortName = (table: string) => dottedTable(table).split(".").at(-1) ?? table;

/**
 * The launch a per-table run id belongs to: `<base>-NN-<table>` (`<table>` = the landing table's
 * short name, NN its position in the generation order) is `<base>`; anything else is a one-table
 * launch running as its own base (the evaluator's `split_run_id`, `run_pipeline.plan_launch`).
 */
export function launchOfRun(runId: string, landingTable: string | null): string {
  if (!landingTable) return runId;
  const suffix = `-${shortName(landingTable)}`;
  if (!runId.endsWith(suffix)) return runId;
  const match = /^(.+)-\d{2,}$/.exec(runId.slice(0, -suffix.length));
  return match ? match[1]! : runId;
}

/** How long after `generation_finished_at` a launch's summary row may still be stamped. */
export const JOIN_WINDOW_SLACK_MS = 60 * 60 * 1000;

/**
 * `[generation_started_at, generation_finished_at + 1 h]` as canonical timestamps (microseconds
 * kept), or null when the registry row lacks either end.
 */
export function joinWindow(
  evaluation: Pick<JoinableEvaluation, "generation_started_at" | "generation_finished_at">,
): { from: string; to: string } | null {
  const from = evaluation.generation_started_at ? canonicalTimestamp(evaluation.generation_started_at) : null;
  const end = evaluation.generation_finished_at ? canonicalTimestamp(evaluation.generation_finished_at) : null;
  if (!from || !end) return null;
  const shifted = new Date(Date.parse(`${end.slice(0, 19)}Z`) + JOIN_WINDOW_SLACK_MS).toISOString();
  return { from, to: `${shifted.slice(0, 19)}${end.slice(19)}` };
}

const newestFirst = (a: AdjustmentRun, b: AdjustmentRun) => b.created_at.localeCompare(a.created_at);

function joinTable(
  evaluation: JoinableEvaluation,
  table: JoinableEvaluation["tables"][number],
  runs: readonly AdjustmentRun[],
): { run: AdjustmentRun; joinedBy: AdjustmentJoin } | null {
  // 1. The table's own run id. A known id is never traded for a fuzzier key: a row with another
  //    run id is another run (ADR 0038 adjustments are per launch).
  if (table.run_id) {
    const exact = runs.filter((r) => r.run_id === table.run_id).sort(newestFirst)[0];
    return exact ? { run: exact, joinedBy: "run_id" } : null;
  }
  const landing = table.landing_table ? dottedTable(table.landing_table) : null;
  const sameLanding = (r: AdjustmentRun) =>
    landing !== null && r.landing_table !== null && dottedTable(r.landing_table) === landing;
  // 2. The launch's base run id + this table's suffix (`<base>-NN-<table>`; a one-table launch is `<base>`).
  const base = evaluation.base_run_id;
  if (base) {
    const names = new Set([table.name, landing ? shortName(landing) : null].filter((n): n is string => !!n));
    const ofLaunch = runs.filter((r) => {
      if (r.run_id === base) return sameLanding(r);
      if (!r.run_id.startsWith(`${base}-`)) return false;
      const match = /^\d{2,}-(.+)$/.exec(r.run_id.slice(base.length + 1));
      return !!match && names.has(match[1]!);
    });
    const hit = ofLaunch.sort(newestFirst)[0];
    // The base is known, so the window below could only offer another launch's row.
    return hit ? { run: hit, joinedBy: "base_run_id" } : null;
  }
  // 3. No id at all: the landing table, inside the generation job's window — and only when every
  //    candidate belongs to ONE launch. Two launches in the window are ambiguous, not "the newer one".
  const window = joinWindow(evaluation);
  if (!window || !landing) return null;
  const inWindow = runs.filter((r) => {
    const at = canonicalTimestamp(r.created_at) ?? r.created_at;
    return sameLanding(r) && at >= window.from && at <= window.to;
  });
  if (new Set(inWindow.map((r) => launchOfRun(r.run_id, r.landing_table))).size !== 1) return null;
  return { run: inWindow.sort(newestFirst)[0]!, joinedBy: "window" };
}

/**
 * One entry per table the launch generated (an external parent has no `validation_runs` row),
 * in registry order. Join key, in this order — and never across launches (ADR 0038):
 *   1. `tables[].run_id` = `validation_runs.run_id`;
 *   2. else `base_run_id` + the table's `-NN-<table>` suffix;
 *   3. else the landing table with `created_at` inside the generation window (+ 1 h);
 *   4. else no row: verdict `unknown`.
 */
export function joinModelAdjustments(
  evaluation: JoinableEvaluation,
  runs: readonly AdjustmentRun[],
): TableModelAdjustment[] {
  return evaluation.tables
    .filter((table) => table.role !== "external")
    .map((table) =>
      tableModelAdjustmentOf(
        table.name ?? (table.landing_table ? shortName(table.landing_table) : ""),
        joinTable(evaluation, table, runs),
      ),
    );
}

// ------------------------------------------------------- the evaluator's job --

/**
 * Dataflow job names the EVALUATOR runs under: the composer DAGs launch it as
 * `sdfb-evaluation-v<version>-<timestamp>` (composer/evaluation_framework.py and the chained task
 * of composer/synthetic_beam_bigquery.py), and its CLI defaults to `sdfb-<evaluation_id>`, i.e.
 * `sdfb-eval-…` (sdfb_evaluation/cli/driver.py). A generation launch is `synthetic-sdfb-v…`.
 * Kept by hand: no exporter carries these names.
 */
export const EVALUATOR_JOB_NAME_PREFIXES = ["sdfb-evaluation-", "sdfb-eval-"] as const;

/** A registry row's `generation_job_name` that is in fact an evaluator job (the row points at the wrong job). */
export function isEvaluatorJobName(name: string | null | undefined): boolean {
  const lower = (name ?? "").trim().toLowerCase();
  return EVALUATOR_JOB_NAME_PREFIXES.some((prefix) => lower.startsWith(prefix));
}
