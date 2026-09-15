---
name: dsg-sync
description: Use when replicating a tag or branch of this repository into GoogleCloudPlatform/dataflow-solution-guides (the DSG), refreshing or replacing the open DSG sync PR, or checking that a ref is safe and ready to sync — covers the pre-checks, scripts/dsg/sync.py, its gates, the versioned sync branch and the PR.
---

# DSG sync — golden source → Dataflow Solution Guides

This repository is the **golden source** ([ADR 0040](../../../docs/adr/0040-dsg-donation-golden-source-sync.md)).
The DSG copy under `pipelines/`, `terraform/` and `use_cases/` is generated
by `scripts/dsg/sync.py` from one ref, and is **never edited by hand**. If a
DSG reviewer asks for a change, make it here, release, and re-sync.

```
ref ─ git archive ─▶ precheck ─▶ manifest select + dsg/ overlays + requirements from uv.lock
    ─▶ pin links to unshipped docs ─▶ replace owned DSG paths + index rows
    ─▶ gates (= DSG CI) ─▶ commit ─▶ push fork branch sync/<pipeline>-<ref>
    ─▶ open / update its PR ─▶ close older open sync PRs + delete their branches
```

One sync PR is under review at a time, and its head branch names the
version: `albertols:sync/synthetic-llm-dataflow-bigquery-v0.5.1`.

## Inputs

- `REF`: normally the release tag the release Action just created (`vX.Y.Z`). Use a branch only for a dry run.
- DSG checkout: `~/IdeaProjects/dataflow-solution-guides`, a clone of the
  **fork** with `origin` = `<you>/dataflow-solution-guides` and `upstream` =
  `GoogleCloudPlatform/dataflow-solution-guides`. If it is missing:
  ```bash
  gh repo fork GoogleCloudPlatform/dataflow-solution-guides --clone=false
  git clone https://github.com/<you>/dataflow-solution-guides ~/IdeaProjects/dataflow-solution-guides
  git -C ~/IdeaProjects/dataflow-solution-guides remote add upstream https://github.com/GoogleCloudPlatform/dataflow-solution-guides
  ```
- Tools on `PATH`: `uv`, `gh` (authenticated), `terraform` (or `TERRAFORM_BIN`), `pipenv`, `bash`.

## Steps

1. **Pre-checks in the source** (stop on any failure):
   ```bash
   git fetch --tags && git status --porcelain            # clean tree, REF exists
   uv sync --group dev
   uv run python scripts/dsg/precheck.py                 # sensitive-content gate
   uv run pytest -m "not gpu and not gcp" -q
   uv run ruff check . && uv run mypy packages/sdfb-core/src
   uv run yapf --diff -r --style yapf packages scripts dsg composer public_cloud
   uv run pylint --rcfile dsg/pylintrc packages scripts dsg composer public_cloud
   ```
   Check the CI run for REF is green: `gh run list --branch master --limit 3`.
2. **Dry run** (stages, installs and gates, but does not commit):
   ```bash
   uv run python scripts/dsg/sync.py --ref "$REF" --dsg ~/IdeaProjects/dataflow-solution-guides --gates full
   ```
   The full gate takes ~30 min: pipenv resolves every dependency, like DSG CI.
   Use `--gates fast` while iterating on the manifest.
3. **Review the diff** in the DSG checkout (`git -C … status`, `git -C … diff --stat`). Look for:
   - files outside the owned paths and index files → a manifest bug, stop;
   - anything under `docs/` you would not publish → tighten `include`;
   - `requirements.txt` changes you cannot trace to `uv.lock` → stop.
4. **Commit, push and open or update the PR.** The dry run left the DSG
   checkout dirty, and the sync refuses a dirty checkout, so discard it first:
   ```bash
   git -C ~/IdeaProjects/dataflow-solution-guides switch -f main
   git -C ~/IdeaProjects/dataflow-solution-guides clean -fd -- \
       pipelines/synthetic-llm-dataflow-bigquery terraform/synthetic-llm-dataflow-bigquery \
       use_cases/Synthetic_Data_Generation.md
   uv run python scripts/dsg/sync.py --ref "$REF" --dsg ~/IdeaProjects/dataflow-solution-guides \
       --gates full --open-pr [--cloud-run <dataflow job URL>]
   ```
   **Attribution:** the DSG is a Google-owned repository. Its commits and PR
   carry only the maintainer's git identity. Pass no `--trailer` (no AI
   co-author or session trailers) and never add an AI footer to the PR body.

   **Branch and PR:** the sync publishes REF on `branch_prefix` + REF from
   `dsg/manifest.yaml`, e.g. `sync/synthetic-llm-dataflow-bigquery-v0.5.1`.
   | Situation | What `--open-pr` does |
   | :-- | :-- |
   | No open PR for this branch | Opens one from `<you>:<branch>` |
   | Re-sync of the same REF | Force-pushes the branch, updates the PR title and body |
   | Older sync PRs still open (earlier versions, or the unversioned `sync/synthetic-llm-dataflow-bigquery`) | Comments "Superseded by <new PR>", closes them and deletes their fork branches |

   Only PRs from your fork whose head matches the prefix are closed, never
   anyone else's. Check the output for `closed … (superseded)` lines.
5. **Watch DSG CI**: `gh pr checks <url> --watch`. Fix any red check **in the
   source**: add a gate to `sync.py` if CI caught something the gates missed,
   then release and re-sync. Never push a fix to the DSG branch by hand.
6. Report the PR URL, the gate table, and whether a live Dataflow run backs
   this ref. If none does, the PR body already says so; don't overstate it.

## Stop conditions

- The precheck finds anything, in the source or in the staged tree.
- `SyncError`: an inconsistent manifest (a shipped test loads an unshipped
  script), a patch whose target and replacement are both missing (the DSG
  file changed; revisit `patches`), a missing index anchor (the DSG index changed; update
  `index_rows`), broken links, or a dirty DSG checkout.
- `pylintrc-parity` fails: DSG changed `pipelines/pylintrc`. Re-vendor it to
  `dsg/pylintrc`, re-lint the source, release, and re-sync.
- Any gate fails. Nothing is committed; the DSG checkout stays on the sync
  branch for inspection (`git -C … switch main` to discard).
- `unknown manifest keys: ['branch']`: REF predates versioned branches
  (before v0.5.1). Sync a newer tag instead.

## Where things live

| Concern | File |
| :-- | :-- |
| What ships, owned paths, index rows, patches to DSG files (e.g. its CI) | `dsg/manifest.yaml` |
| DSG-only files (launch scripts, setup.py, Cloud Build) | `dsg/pipeline/` |
| Terraform module (tables from `config/bq_schema`, landing tables `LIKE` the public ones, optional Flex Template job) | `dsg/terraform/` |
| Public FK model the DSG launches generate | `config/relationships/gcp_public_fk_example.yaml` (a sample: directory scans skip `*_example.yaml`) |
| Model staging (Hugging Face or ModelScope → GCS, no credentials) | `dsg/pipeline/cloudbuild_stage_models.yaml`, `dsg/pipeline/scripts/02_stage_models.sh` |
| Solution guide page | `dsg/use_cases/Synthetic_Data_Generation.md` |
| Launch parameters (kept identical; `terraform test` fails on drift) | `dsg/pipeline/scripts/04_run_dataflow.sh`, `dsg/terraform/dataflow.tf` |
| Sensitive-content rules and hashed tokens | `dsg/precheck.yaml`, `dsg/sensitive_token_hashes.txt` |
| PR body | `dsg/PR_TEMPLATE.md` |
| Engine | `scripts/dsg/sync.py`, `scripts/dsg/precheck.py` (+ tests in `packages/sdfb-tests/tests/unit/scripts/test_dsg_*.py`) |
