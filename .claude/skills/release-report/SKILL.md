---
name: release-report
description: Use after a PR merges to master (or when the release Action's deterministic report lands) to write the insights section — regression calls, bottleneck attribution, optimization candidates — on top of docs/releases/<version>/report.md.
---

# Release report — interpretation layer

The GH Action (`.github/workflows/release_tag_report.yaml`) already produced
the deterministic numbers via `scripts/release/make_release_report.py`.
This skill adds judgment. NEVER edit the generated numbers or charts.

1. Read `docs/releases/<version>/report.md` + the underlying
   `integration_test/<JOB_ID>/*.json` for both sides of the diff.
2. Append an `## Insights` section: regressions (call severity), bottleneck
   attribution (which Dataflow step/phase moved and which commit plausibly
   moved it), optimization candidates, follow-up backlog items.
3. Where ADC exists locally, optionally enrich with live Dataflow/BQ detail
   (`scripts/e2e/e2e_gcp_probe.py`) — never required.
4. Redaction rules are identical to the generator's: no FQNs, column names,
   or data values — `table_1`/`col_1` tokens only. Re-run the leak scan after
   editing: `scripts/e2e/redaction.py` gates what may be committed.
5. Commit as `docs(releases): <version> insights`.
