# ADR 0045 — Whole-model launches: every enabled table of one model, in batch jobs

**Status:** Proposed (2026-10-21) — being built; completed with its figures
when the phase's documentation lands.
**Design:** [`docs/DESIGN.md` §4 Relational generation](../DESIGN.md#4-relational-generation)
**Builds on:** [ADR 0030](0030-single-job-relational-generation.md) (a component in one job) · [ADR 0032](0032-relationships-as-config.md) (relationships are configuration) · [ADR 0036](0036-parent-driven-fanout-generation.md) (a child's rows follow its parents) · [ADR 0043](0043-generator-config.md) (the config keys) · [ADR 0044](0044-generation-record.md) (the record each batch writes)

## Context

A relationships model can list 100+ unrelated landing tables, but a launch
generates only the tables named in `--landing_table` and their components,
every source is the reference table's dataset plus the table's name, every
table gets the launch's `--num_rows`, and one Flex launcher preps its tables
one after another inside a 12-minute window.

## Decision (summary)

- `launch.tables: all` generates every `enabled: true` table of one model
  file; `explicit` (the default) is today's launch.
- A table may set `source_table` (`[project.]dataset.table`) and `num_rows`.
- A deterministic first-fit-decreasing planner packs the selected tables
  into batch jobs of at most `launch.max_tables_per_job` tables and never
  splits a connected component.
- The Composer model DAG and `scripts/launch/run_model.py` run the batches;
  every batch's launcher plans again and checks the count.
- One evaluation covers every batch that landed.
