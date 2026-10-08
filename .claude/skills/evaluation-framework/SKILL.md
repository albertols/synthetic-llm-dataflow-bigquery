---
name: evaluation-framework
description: Use when scoring a generation run's landed tables against their source with sdfb-eval, reading an evaluation (baselines, noise floors, lifts, run status), querying the evaluation_* tables, or adding or changing a metric in the evaluator's catalogue.
---

# Evaluation framework (`sdfb-evaluation`)

Decision: [ADR 0041](../../../docs/adr/0041-evaluation-standalone-package.md). Design and figures:
[`docs/designs/2026-07-07-evaluation-framework-design.md`](../../../docs/designs/2026-07-07-evaluation-framework-design.md)
and [`docs/DESIGN.md` §11](../../../docs/DESIGN.md#11-evaluation-sdfb-evaluation). Commands and flags:
[`packages/sdfb-evaluation/README.md`](../../../packages/sdfb-evaluation/README.md) (the source of truth for every flag).

**State: built and reviewed on a laptop with invented data. Nothing has run on Google Cloud**: the image is
unbuilt with the evaluator in it, the template unlaunched for an evaluation, neither Composer DAG parsed by
Airflow, the scoping SQL never executed by BigQuery. Never write that an evaluation "was run", and never quote a
cloud measurement, until the first run's evidence exists.

**One image, one template, one DAG import** (ADR 0041, amendment of 2026-10-06). The evaluator has no image or
template of its own: `docker/Dockerfile` carries its source, `docker/flex_entry.py` dispatches on the template
parameter `sdfb_job` (absent = generation, `evaluation` = this package), and the generation DAG evaluates its
own run when `run_evaluation` is true. The package keeps its own lock for development and CI only; in the image
it runs on the generator's environment. Never add an import between the two sides: the dispatcher is the only
file that names both.

## When to run it

- After a generation job has landed and **before the time-travel window closes**: the evaluator pins the source
  and recovers the job's rows through BigQuery time travel. Past the window a scope is `expired` and the source
  is read as it is now.
- To compare engines, flags or releases on the same source (`sdfb-eval compare`).
- Not as a gate on generation: an evaluation is a separate job with its own status, and never fails the
  generation run.

## The surfaces

| Surface | Use |
| :-- | :-- |
| `sdfb-eval plan --dry_run …` | scope, panel, bytes and predicted shuffle; no registry row |
| `sdfb-eval run …` | one evaluation; exit 0 ok, 1 the optional `--fail_on` gate, 2 usage (nothing started), 3 evaluation failed |
| `sdfb-eval report` / `compare` / `catalogue` / `schemas` | read a stored run (markdown or `--format json`), compare two, print the catalogue, create the tables |
| the generation DAG with `run_evaluation` true | the same job, chained after the run: waits for the generation job, then launches the generation template with `sdfb_job=evaluation`, `generation_job_id` (the launch's identity), the launched table (`seed_table`), `relationships_uri`, the landing and source datasets, `scope=manual` and the generation's `reference_rows_limit`: it evaluates what the launch generated and reads no job log (unparsed and unrun) |
| the template with `sdfb_job=evaluation,generation_job_id=<ID>,disk_size_gb=200` (not `job_id`: the launcher drops that name); the optional DAG `sdfb_evaluation_framework` (`generation_job_id`, or `generation_job_ids` for several) | the same job, for a run that already finished; the standalone DAG waits for its job and closes the registry row (unbuilt and unrun) |
| BigQuery | `evaluation_data_history` (events), `evaluation_metrics`, `evaluation_profiles`, `evaluation_row_flags`; views `evaluation_latest`, `evaluation_latest_per_job` |
| E2E validation prompt | optional Step 3.6 folds the report into the evidence bundle |

`--runner DirectRunner` is the local spelling; the pipeline runs on Beam's in-process runner, never Prism
(Prism can read a side input before it is complete; `--runner PrismRunner` is a usage error).

## Reading a result

- **Run status** (`SUCCEEDED`, `PARTIAL`, `SKIPPED`, `FAILED`, …) describes the evaluation, not the data. A run
  whose metrics fail still `SUCCEEDED`.
- **Baseline.** Every fidelity row has `baseline_value = metric(R, source)`: the floor a perfect copier of the
  reference sample reaches. Judge `value` against it, not against zero.
- **Noise floor.** A metric fails only when `value` crosses the threshold *and* exceeds its noise floor. No
  p-values. Size-dependent metrics (entropy, distinct, coverage, DCR, density, detection AUC) are computed at
  matched n. In `compare`, `≈` means the delta is inside both rows' noise.
- **Lifts.** Memorization, exposure and value lifts are matches to R over matches to the holdout H; 1 is chance,
  and status reads the confidence **lower bound**. The holdout share is 0.5 at chance.
- **Narrow tables.** `row.near_match_rate` and `row.exact_match_rate_nonkey` are absolute rates against fixed
  thresholds: with few non-key columns they FAIL by chance, with nothing copied. Read them next to their lifts,
  which are the calibrated signal.
- **Sampled mode.** A row computed from a sampled side carries `method = sample` and `sample_rate`, over the rows
  read. A metric that needs every row of a side is `not_evaluated` ("sampled mode cannot measure …; run exact
  mode"): match and duplicate rates, a sampled edge's orphans and fan-out, and per column the adherence, novelty,
  copy-rate, coverage, pool-cap and range-coverage metrics. Use `--mode exact` for those verdicts.
- Privacy metrics are risk indicators, not guarantees. Outputs honour the literal policy: values appear literally
  only for at most 50 distinct source values seen at least 10 times; the rest are keyed hashes.

## Extending the catalogue

`catalogue/metrics.yaml` is the single definition of a metric. In order:

1. **YAML** — add the entry (id `<level>.<name>`, family, direction, thresholds, noise method, text, references).
   The loader is strict; a bad key or vocabulary value fails at load.
2. **Code** — implement it under `stats/` and the Beam transform that owns it (`beam/dense.py`, `census.py`,
   `membership.py`, `privacy.py`, `relational.py`); status and score come from the catalogue via `scoring`,
   never from per-metric code. New code cites sources by author and year; URLs live in the YAML and the design.
3. **Renderer** — `uv run python scripts/doc/render_eval_catalogue.py` rewrites the design's catalogue tables;
   `--check` (also in CI) must exit 0.
4. **GUI** — a GUI on another branch reads the catalogue and the table schemas as its contract; re-sync its
   contracts there (`contracts:sync`) after a catalogue or schema change. Not part of this tree.

Tests: `uv run --project packages/sdfb-evaluation pytest packages/sdfb-evaluation/tests -q -m "not gcp"` (always the
explicit path: a bare `pytest` picks up the root config) and `uv run --project packages/sdfb-evaluation mypy
packages/sdfb-evaluation/src`. The package must import none of `sdfb_core`, `sdfb_beam`, `sdfb_tests`; a piece
that must match the generator is mirrored and pinned by the two-sided golden file.

## Pitfalls

- The package has its own environment. With the root environment active in the shell, `uv` warns that `VIRTUAL_ENV`
  does not match: run `env -u VIRTUAL_ENV uv run …`, and never pass `--active` (it would install the evaluator's
  dependencies into the root environment). A test that hangs fails after 300 s (`pytest-timeout`) and prints every
  thread's stack.
- The holdout share cannot tell one heavy copied cluster from split noise; the exact-copy metrics own that case.
- The detection baseline is taken at n = |R| (`baseline_n`).
- An orphan rate whose rate x n is below k reveals sums only; the design (§4.9) says what is published and what is withheld.
- On a column of a few hundred rows the histogram and quantile payloads are coarse by design: published edges are
  at least 10 source records apart (design §4.9). The metrics still use the full grid.
- An edge whose key columns differ in type family (`INT64` against `NUMERIC`) is `not_evaluated`, not 100 % orphans.
- A scope whose window was contaminated by another writer reads nothing unless `--allow_contaminated`.
- BigQuery's `APPROX_*` aggregates are estimates: two plannings may differ slightly; metrics are deterministic for
  a given plan.
- A temporal column's mean keeps 9 significant digits of an epoch value (about ten seconds).
- Beam arguments are passed through; the experiment `enable_data_sampling` is refused.
- On the shared image an evaluation launch must pass `sdfb_job=evaluation` (else it is a generation launch) and
  `disk_size_gb=200` (the image is multi-GB; the evaluator pins no boot disk). Both DAGs do.
- A chained evaluation (`trigger=chained`, from the generation DAG) names its tables with `--seed_table` (its enabled component in the model, else the table alone) and the job id only as the launch's identity (`--generation_job_id` next to the seed: only the Dataflow job is read, needs `roles/dataflow.viewer`, never the log): its row carries the job id and window and is listed in `evaluation_latest_per_job`; the launch's own record of its tables, run ids and reference digest, and a model the launch adjusted, are not seen. It is not waited for: if its job dies after
  launch the `RUNNING` row stays open. Only the standalone DAG closes such a row.
- A change of the evaluator's dependencies must stay inside what the root `uv.lock` holds and the image's extras
  install: `packages/sdfb-tests/tests/unit/docker/test_shared_image.py` fails otherwise.
- Do not propose Vertex AI, Dataplex, Looker or an external LLM API for any of this.
