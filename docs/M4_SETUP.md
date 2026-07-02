# M4 Pro onboarding — synthetic-dataflow-bigquery

End-to-end setup for picking up the project on the M4 Pro after the laptop checkpoint. Covers venv, GCP auth, PyCharm wiring, the recommended task order, and the gotchas that bite later.

Companion to `MODEL_LAYOUT.md` — that doc covers weights only; this one covers everything else.

## TL;DR

```bash
brew install uv
cd ~/IdeaProjects/synthetic-dataflow-bigquery
git pull --rebase
uv sync --group dev
uv run pytest -m "not gpu and not gcp" -q          # expect "68 passed"
gcloud auth application-default login
gcloud config set project <your-dev-project>
```

Point PyCharm at `.venv/bin/python`. Done.

## Venv recommendation — `uv`, not PyCharm's built-in

Use **`uv`** to manage the venv (matches the repo's pyproject layout — three-member uv virtual workspace with dependency groups), and **point PyCharm at the resulting venv** as its interpreter. Don't use PyCharm's "New Environment → Virtualenv" wizard — it doesn't understand the workspace.

Why uv-first:
- One `uv sync` brings all three packages + dev deps + the pinned Python into `.venv/` deterministically.
- Same command runs in CI later — zero environment skew.
- PyCharm 2024.3+ has native uv support.

## Step-by-step

### 0. Prerequisites

```bash
which git python3 gcloud   # all three should resolve
python3 --version          # 3.11 or 3.12 (pyproject requires >=3.11,<3.13)
```

Install missing tools:
- `git` — should be there on macOS.
- `python3` — `brew install python@3.12` if missing.
- `gcloud` — https://cloud.google.com/sdk/docs/install-sdk

### 1. Clone (or pull)

```bash
cd ~/IdeaProjects
git clone <repo-url> synthetic-dataflow-bigquery   # skip if already cloned
cd synthetic-dataflow-bigquery
git pull --rebase                                   # if already cloned
```

### 2. Install `uv`

```bash
brew install uv
# OR: curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version   # confirm >= 0.4
```

### 3. Bootstrap the workspace

```bash
uv sync --group dev
```

One-time ~2–3 min on first install, ~600 MB on disk. Installs `sdfb-core` + `sdfb-beam[gcp]` + `sdfb-tests` + dev tools (ruff, mypy, pytest, hypothesis). Creates `.venv/` at the repo root.

> **JFrog index**: `pyproject.toml` pins the default index to
> `https://the_artifactory.com/artifactory/api/pypi/pypi-all/simple`
> ([`tool.uv.index`](../pyproject.toml)). On the corp network, direct
> `pypi.org` / `files.pythonhosted.org` are blocked — uv reads through
> the JFrog mirror instead. If the mirror requires auth, export
> `UV_INDEX_JFROG_PYPI_ALL_USERNAME` and `UV_INDEX_JFROG_PYPI_ALL_PASSWORD`
> before running `uv sync` (the standard Artifactory user + identity
> token from the "Set Me Up" dialog).

If you ever see `ModuleNotFoundError: No module named 'sdfb_core'` after a sync, it means the workspace members weren't installed (e.g. someone removed `sdfb-tests` from the root `dev` group). Quick fix: `uv sync --all-packages --group dev`.

Optional extras for M4 work:

```bash
uv sync --group dev --extra embedding --extra library   # RAG (§7) / library (§6) dev
uv sync --package sdfb-beam --extra mlx                  # real-LLM smoke (see M4_LOCAL_SMOKE.md)
```

**Do NOT install `--extra gpu` on the M4.** vLLM is CUDA-only and the `[gpu]` extra is marked `sys_platform == 'linux'`, so it's a no-op here by design — vLLM only ever runs inside the Dataflow container ([ADR 0010](adr/0010-m4-local-smoke-mlx.md)). On the M4 you exercise the LLM path through MLX, never vLLM. The `[gpu]` extra is resolved only when the linux GPU image is built ([ADR 0012](adr/0012-enterprise-image-build.md)).

### 4. Sanity check — the 68 laptop tests must pass on the M4

```bash
uv run pytest -m "not gpu and not gcp" -q
```

Expected last line: `68 passed in ~25s`.

If any test fails: paste the last ~30 lines back — that's the fastest signal something diverges between the laptop and M4 environments.

### 5. PyCharm wiring (one-time)

1. Settings → Project → Python Interpreter → Add Local Interpreter.
2. Choose the "uv" tab → "Existing environment" → select `<repo>/.venv/bin/python`.
3. In the Project view, right-click `packages/sdfb-tests/tests` → Mark Directory As → "Test Sources Root".
4. (Optional) Right-click `packages/sdfb-core/src` and `packages/sdfb-beam/src` → Mark Directory As → "Sources Root". PyCharm then resolves imports correctly without the `pythonpath` hack.

### 5b. Per-machine env vars via `direnv` (recommended)

If your machine sits behind a corporate proxy or needs a custom CA bundle, encode the env vars in a repo-local `.envrc` so every shell + IDE that enters the directory picks them up automatically. The real `.envrc` is gitignored; commit only `.envrc.example`.

```bash
brew install direnv
echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc       # or your shell rc
source ~/.zshrc
cp .envrc.example .envrc
# edit .envrc — uncomment + set HTTP_PROXY / HTTPS_PROXY / NO_PROXY for
# YOUR machine. For db.com networks the working egress proxy for
# googleapis.com is `the-proxy:8080`, NOT the general
# browsing proxy.
direnv allow
```

To verify direnv is active: `cd` out of the repo and back in — you should see `direnv: loading .envrc`. To verify the proxy actually reaches BigQuery:

```bash
uv run python -c "from google.cloud import bigquery; c = bigquery.Client(project='$(gcloud config get-value project)'); print(list(c.query('SELECT 1 AS x', timeout=30).result(timeout=30)))"
```

Expected: `[Row((1,), {'x': 0})]` in <5s.

PyCharm: install the **EnvFile** plugin (or 2024.3+ has direnv recognition) so the run configs inherit `.envrc` env vars without manual duplication.

### 6. GCP auth

```bash
gcloud auth application-default login    # browser flow
gcloud config set project <your-dev-project>
gcloud auth list                          # confirm active account
gcloud config get-value project           # confirm project
```

Application Default Credentials are what `google-cloud-bigquery` and `apache_beam[gcp]` pick up automatically — no env var plumbing needed.

## What to send back from the M4

After each step, paste these into chat (terse is fine):

1. **After step 2**: `uv --version` output (one line).
2. **After step 3**: last 5 lines of `uv sync --group dev` output, especially if it errored.
3. **After step 4**: the pytest summary line. If anything fails: the last ~30 lines.
4. **After step 6**: output of `gcloud config get-value project` (so we can use the right project ID in subsequent commands).

Once those four come back green, we pick the next task.

## Recommended task order from here

Three M4-doable tasks are unblocked. Suggested order with rationale:

### Step A — Extract a real `_ddl.json` (~10 min)

Sanity-checks the laptop → M4 transfer and confirms the canonical schema shape against a real table, not just our fixture mocks.

```bash
uv run python scripts/extract_ddl.py \
    --project "$(gcloud config get-value project)" \
    --dataset <some_small_dev_dataset> \
    --table <some_small_table> \
    --runner DirectRunner
```

Output lands in `./output/<dataset>/ddl_metadata_<dataset>_<table>.json`.

Paste back the `head -50` of the file (or the full JSON if small). If `TableSchema.model_validate()` accepts it cleanly on the M4, the whole DDL extractor chain is verified against real BigQuery — not just mocked clients.

### Step B — #10 (image) + #14 (CI workflows)

Per [ADR 0008](adr/0008-ci-driven-builds.md), image build/push/deploy happen in GitHub Actions, not on the M4. Full runbook: **[`CICD.md`](CICD.md)**. Covers the 3 workflows + DAG + secrets + Flex Template metadata. The M4's role here is `gh workflow run …` and observing the resulting Dataflow runs.

For real-LLM iteration on the M4 without burning Dataflow time, see **[`M4_LOCAL_SMOKE.md`](M4_LOCAL_SMOKE.md)** — the MLX-based smoke loop (#15).

**#6 (sdgx vs DataDreamer bake-off)** can use the MLX loop for fast fidelity-check iteration before any Dataflow run.

### Step C — #9 (vLLM `ModelHandler`)

Depends on having weights in GCS. Layout, download procedure, and runtime-load snippets: **[`MODEL_LAYOUT.md`](MODEL_LAYOUT.md)**. Stage Gemma 4 E4B-**it** weights to `gs://{bucket}/synthetic/models/gemma4/e4b-it/v1/` (use the `-it` variant — the base has no chat template) → then implement #9.

### Step D — #11 (end-to-end on Dataflow) + #12 (thresholds + `validation_runs` table)

After #9 + #10 are stable. This is the M1 finish line.

## Cross-doc map

When you need information on a concern, go here — don't restate it elsewhere.

- **CI/CD pipeline** (image build, Flex Template deploy, DAG import); L4 quota; region flags → [`CICD.md`](CICD.md)
- **Local smoke test on M4 with a real model** (no Dataflow) → [`M4_LOCAL_SMOKE.md`](M4_LOCAL_SMOKE.md)
- **Model weights** — GCS layout, Kaggle download, runtime load, Apple-Silicon caveat → [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md)
- **Locked architecture decisions**, package contract, hard constraints → [`../CLAUDE.md`](../CLAUDE.md)
- **Recipe cards** (DoFn lifecycle, model handler, Mode A validation, …) → `../.claude/skills/*.md`
- **Sub-agent boundaries** (ddl-codegen, gpu-image-builder, b1-rag-engineer, …) → `../.claude/agents/*.md`

## Repo layout

```
packages/sdfb-core/               pure-Python contracts + ABC + codegen (laptop-installable)
packages/sdfb-beam/               Beam pipeline + DoFns + DDL extractor + handlers + CLI
packages/sdfb-tests/              unit + integration tests
docker/                           Dockerfile + .dockerignore + flex_template_metadata.json
scripts/                          extract_ddl, probe_gpu_dataflow, hello_synthetic_mlx
config/                           thresholds.yml + models.yml
composer/                         synthetic_beam_bigquery.py (Airflow DAG template)
.github/workflows/                ci.yml + 1_build / 2_deploy / 3_import_dag
```

When in doubt: `CLAUDE.md` + `memory/MEMORY.md` in the project memory directory carry the locked decisions from planning. Re-read them if anything ever feels ambiguous.
