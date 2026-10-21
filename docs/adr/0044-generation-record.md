# ADR 0044 — The generation record: `validation_runs` records what generated it

**Status:** Proposed (2026-10-21) — being built in phase A2 of the
[config / record / model-launch design](../superpowers/specs/2026-10-20-config-record-and-model-launch-design.md);
completed with its figures when the phase's documentation lands.
**Design:** [`docs/DESIGN.md` §7 Validation and the dead-letter queue](../DESIGN.md#7-validation-and-the-dead-letter-queue)
**Builds on:** [ADR 0043](0043-generator-config.md) (the resolved config this record snapshots) · [ADR 0041](0041-evaluation-standalone-package.md) (the evaluator that reads it) · [ADR 0042](0042-self-hosted-platform-gui.md) (the GUI that shows it)

## Context

An evaluation could not say which engine, model, embedder, retrieval, seed or
tier produced the rows it scored: the chained and Composer evaluations read
only four manual parameters, and `validation_runs` had no column for most of
them. The generator's version existed only as a git tag.

## Decision (summary)

- `validation_runs` gains fourteen NULLABLE columns: the Dataflow job, the
  launch's base run id, the worker's generator version, the launch time, five
  generation settings, the relationships model's URI and digest, the config
  digest, and `config`, a JSON snapshot of the resolved config.
- `generator_version` is `<base>-<branch>-<sha7>[-dirty]` (`sdfb_core/version.py`).
- A table without the columns gets today's row and one WARNING.
- The evaluator reads the record first, then the launch log, the Dataflow
  display data and manual input, each filling only what is missing.
