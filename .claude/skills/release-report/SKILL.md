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
   or data values — `table_1`/`col_1` tokens only. `scripts/e2e/redaction.py`
   has no CLI of its own — after editing the `## Insights` section, re-run
   the leak scan by sibling-importing it (same idiom
   `e2e_bundle_export.py`/`make_release_report.py` use) and scanning the
   version dir you just edited:

   ```bash
   uv run --no-sync python3 - <<'PY'
   import json, sys
   from pathlib import Path

   sys.path.insert(0, "scripts/e2e")
   import redaction

   job_id = "<JOB_ID>"    # the integration_test/<JOB_ID>/ this release's head side used
   version = "<version>"  # e.g. v0.4.0

   job_dir = Path("integration_test") / job_id
   basenames = {
       "gcp": "e2e_gcp_metrics.json",
       "offline": "e2e_validation_metrics.json",
       "stats_diff": "stats_diff.json",
       "crosscheck": "freetext_crosscheck_metrics.json",
   }
   metrics = {
       label: json.loads((job_dir / name).read_text())
       for label, name in basenames.items()
       if (job_dir / name).exists()
   }

   mapping = redaction.build_mapping(metrics)
   hits = redaction.leak_scan(Path("docs/releases") / version, mapping)
   if hits:
       print(f"LEAK: {len(hits)} token(s) survived:")
       for fname, tok in hits:
           print(f"  {fname}: {tok!r}")
       sys.exit(1)
   print("leak scan: clean")
   PY
   ```

   Run from the repo root (matches every other `scripts/e2e/` invocation).
   A nonzero exit means a real identifier/column survived into
   `docs/releases/<version>/` — fix the `## Insights` prose before committing.
5. Commit as `docs(releases): <version> insights`.
