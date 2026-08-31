# 2026-08-26_05_01_16-3186876581127148459 — R6 10M/table relational run (C_TABLE parent + A_TABLE child)

Committed metrics bundle for the release report (`scripts/release/make_release_report.py`
discovers `integration_test/<JOB_ID>/real/*.json` at a git ref — nothing else is read).

- Reconstructed from the run's `_full_report.md` inlined JSON annexes (the oss
  export: table/column/value identifiers are already aliases — `PROJECT_ID`,
  `C_TABLE`/`A_TABLE`, `COL_nnn`); Dataflow network-tag names additionally
  scrubbed to `NETWORK_TAG_n` (ADR 0033 D7). No CSVs, no worker logs.
- `gcp_metrics.json` / `offline_metrics.json` / `stats_diff_metrics.json` /
  `freetext_crosscheck_metrics.json` = the **parent** (`C_TABLE`, 10,000,000 rows) —
  the basenames the generator matches. `*_a_table.json` = the **child**
  (`A_TABLE`, 10,000,000 rows, FK-enforced, 0 orphans) — kept for the
  `## Insights` section (`.claude/skills/release-report`), not discovered.
- Evidence and interpretation: `docs/designs/2026-08-29-r6-scale-pool-ladder-integrity.md`,
  `docs/adr/0033-pool-ladder-integrity-at-scale.md`.
