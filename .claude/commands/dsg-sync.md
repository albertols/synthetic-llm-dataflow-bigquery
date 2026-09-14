---
description: Sync a tag or branch of this repo into GoogleCloudPlatform/dataflow-solution-guides and open or update the PR
argument-hint: <ref> [--dry-run]
---

Load the `dsg-sync` skill and follow it for ref `$ARGUMENTS`.

- With `--dry-run`, stop after step 3 (review the staged diff). Do not commit, push or open a PR.
- Without it, run every step through the PR, and report the PR URL, the gate table, and whether a live Dataflow run backs this ref.
