/**
 * Whether an evaluation that the registry lists as RUNNING can still be
 * running. A display state only: derived here from the row a view already
 * has and the clock. The registry's own status stays RUNNING, so the BFF, the
 * filters and the sort keep speaking of RUNNING.
 *
 *   RUNNING event ──► FINAL event            the evaluation finished: its status
 *        │
 *        ├─ ≤ 24 h, no FINAL                 running
 *        └─ > 24 h, no FINAL                 stale: the launch or the job most
 *                                            likely ended without recording a
 *                                            result
 *
 * The registry (evaluation_data_history) is append-only events. The launcher
 * writes RUNNING before it submits the pipeline (the Dataflow job is queued
 * meanwhile) and the job writes FINAL; when either dies, RUNNING stays the
 * last event until someone appends a FINAL row
 * (docs/RUN_PLAYBOOK.md, "A RUNNING row that never closes").
 *
 * Stale is an inference from the event's age, not a reading of the job: a
 * view may say "most likely ended", never that the evaluation is over.
 */

/**
 * Hours after which a RUNNING event is no longer read as "running". Every
 * table an evaluation prepares for its reads (scopes, source snapshots,
 * samples) expires 24 hours after it was created
 * (packages/sdfb-evaluation/src/sdfb_evaluation/context/scope.py, `_EXPIRY`).
 */
export const STALE_AFTER_HOURS = 24;

const HOUR_MS = 3_600_000;

/** What a view shows in place of RUNNING for a stale evaluation. Never a registry value. */
export const STALE = "STALE";

/** The hover text of a stale pill: what the registry itself says. */
export const STALE_TITLE = `Registry status RUNNING: no FINAL event more than ${STALE_AFTER_HOURS} hours after the RUNNING event`;

/**
 * Where closing a RUNNING row by hand is written down: a repository document,
 * the heading in it, and the anchor GitHub gives that heading
 * (runState.test.ts reads the document and fails when the heading moves).
 */
export const CLOSE_BY_HAND = {
  doc: "docs/RUN_PLAYBOOK.md",
  title: "A RUNNING row that never closes",
  anchor: "a-running-row-that-never-closes",
} as const;

/** An evaluation's last registry event, as a list row or a detail's `evaluation` carries it. */
export type RegistryEvent = { event: string; recorded_at: string };

/**
 * True when the last event is RUNNING and was recorded more than 24 hours
 * before `now` (epoch milliseconds; the clock by default). A time that cannot
 * be read, or lies in the future, is not stale: the row stays running.
 */
export function isStale(row: RegistryEvent, now: number = Date.now()): boolean {
  if (row.event !== "RUNNING") return false;
  const recorded = Date.parse(row.recorded_at);
  return Number.isFinite(recorded) && now - recorded > STALE_AFTER_HOURS * HOUR_MS;
}

/** The status a view shows: STALE for a stale evaluation, else the registry's own. */
export function displayStatus(row: RegistryEvent & { status: string }, now: number = Date.now()): string {
  return isStale(row, now) ? STALE : row.status;
}

/**
 * The facets' `latest` as a registry event. It carries the status and
 * `evaluated_at` only: a RUNNING status is a RUNNING last event, and the
 * evaluator writes that event with recorded_at = evaluated_at
 * (sdfb_evaluation/context/plan.py, `registry_seed`).
 */
export function latestEvent(latest: { status: string; evaluated_at: string }): RegistryEvent & { status: string } {
  return {
    event: latest.status === "RUNNING" ? "RUNNING" : "FINAL",
    status: latest.status,
    recorded_at: latest.evaluated_at,
  };
}
