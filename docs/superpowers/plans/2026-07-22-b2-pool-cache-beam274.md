# B2 pool-cache fix + gate write-ordering + Beam 2.74.0 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix the 2026-07-20 b2_library E2E failure (backlog #1/#2: pool cache never hits across batches → 63× LLM cost + ~95% batch failure), close the gate blind spot (#3: FAILED runs leave zero `validation_runs` trace), and bump apache-beam to 2.74.0 across all ongoing branches (enterprise policy) with full library compatibility.

**Evidence:** `integration_tests/2026-07-20_12_51_19-331310386983016047/report.md` (b2, JOB_STATE_FAILED, root cause verified in code: `freetext.py:146` keys the pool cache on `cfg.seed`; `generate.py:125` derives a fresh seed per batch). b1 report (`…11_57_54-…3679`) is healthy; its bounded-pool ceiling is already fixed by WS2 Phase A; its new image-correlated embed slowdown (158 s vs 24 s, image `fc52898` vs `83d5e8f`) is an M4/CI image-bisect item, out of laptop scope.

**Beam 2.74.0 compatibility (verified against PyPI metadata 2026-07-22):** requires `numpy<2.5`, `pyarrow<24`, `protobuf>=3.20.3,<7 (with excluded ranges)`, `requests>=2.32.4`, Python >=3.10 — no conflict with the installed stack (numpy 2.3.5, pandas 2.3.3, scipy 1.17.1, sklearn 1.9.0, sdmetrics 0.28.1, py3.11). Pin sites: `packages/sdfb-beam/pyproject.toml:12-16`, `docker/Dockerfile:34-42` (`BEAM_SDK_IMAGE` beam_python3.11_sdk tag + comments), `public_cloud/deploy/gcp/cloudbuild/build_image.yaml:23`.

## Global Constraints

- Work lands on `ws1-b2-memorization-fix` (Tasks 1-3), then propagates by merging ws1 → `ws2-rag-phase-a` → `ws3-eval-framework` (Task 4; ws2 already contains merged WS4/PR #5). On ws3, the same write-ordering pattern is additionally applied to `_MemorizationGateDoFn`.
- Strict-mode privacy contract unchanged: an empty NOVEL pool yield still raises `FreeTextEmptyYieldError` (never silently memorize). What changes is the retry economics: successful pools are reused across batches; failed builds are never cached (next batch retries).
- Laptop env: `uv run --no-sync` always; venv package installs via `uv pip install --index-url https://pypi.org/simple` (corporate index unreachable from laptop); `uv.lock` untouched (M4 regen).
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

### Task 1 (ws1): FreeTextHook pool cache — drop `seed` from the key

`packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py`:
- [ ] `_pool_for` key becomes `(profile.name, round(cfg.similarity, 4))`; `self._cache: dict[tuple[str, float], list[str]]`.
- [ ] Class docstring rewritten: pool cached per `(column, similarity)` → genuinely once per worker per column; batch-to-batch diversity comes from the per-batch-seeded **draw** (`rng` in `sample()`), not from rebuilding the pool; cite the 2026-07-20 defect (seed in key + `derive_batch_seed` per batch ⇒ zero cache hits, 63× LLM cost, ~95% batch failure).
- [ ] Tests (`packages/sdfb-tests/tests/unit/engines/`): (a) two `GenerationConfig`s differing only in `seed` hit one pool build (count client calls); (b) same cached pool + different rng seeds ⇒ different draws; (c) strict empty-yield build raises AND leaves the cache empty so the next call retries the build; (d) existing freetext suites stay green.

### Task 2 (ws1): BlockerGate waits on the validation_runs write

`packages/sdfb-beam/src/sdfb_beam/pipeline.py` (validation_runs block ~line 232):
- [ ] Capture `write_result = summary_rows | "WriteValidationRun" >> validation_runs_sink`; when `getattr(write_result, "destination_load_jobid_pairs", None)` is not None (real `WriteToBigQuery` FILE_LOADS `WriteResult`), pass it to the gate as `beam.pvalue.AsIter(...)` side input so the gate's stage cannot run before the load jobs commit; non-BQ test sinks (PDone/PCollection) keep today's wiring.
- [ ] `_BlockerGateDoFn.process(self, row, wait_on_write=None)` — side input unused in the body, present only for ordering; docstring states why (2026-07-20 b2 run: gate tripped → job torn down → zero `validation_runs` trace for the FAILED run).
- [ ] Tests: fake sink whose `expand` returns an object exposing `destination_load_jobid_pairs` (a small `beam.Create([("t","job")])` PCollection) — graph builds, summary row still produced, gate still raises `BlockerThresholdExceeded` on a blocker row from `p.run()`; plus the existing sink shape still works.

### Task 3 (ws1): Beam 2.74.0 pins + local verification

- [ ] `packages/sdfb-beam/pyproject.toml`: `apache-beam[gcp]==2.74.0`, comments updated (`beam_python3.11_sdk:2.74.0`, ADR 0012 lockstep note kept).
- [ ] `docker/Dockerfile`: `BEAM_SDK_IMAGE` default tag → `2.74.0` (+ header comments); `public_cloud/deploy/gcp/cloudbuild/build_image.yaml`: `BEAM_SDK_IMAGE=docker.io/apache/beam_python3.11_sdk:2.74.0`.
- [ ] Venv: `uv pip install --index-url https://pypi.org/simple "apache-beam[gcp]==2.74.0"`; full ws1 suite green; ruff clean. `uv.lock` untouched.

### Task 4 (controller): propagate up the stack

- [ ] `git fetch origin`; merge `ws1-b2-memorization-fix` → `ws2-rag-phase-a` (contains merged WS4); resolve (expected: pyproject beam line trivial; pipeline.py validation_runs block — keep ws2's RAG/WS4 additions + ws1's write_result/gate change); full suite green on ws2.
- [ ] Merge `ws2-rag-phase-a` → `ws3-eval-framework`; apply the Task-2 pattern to the eval branch: `write_history = eval_rows | "WriteValidationDataHistory" >> validation_data_history_sink` + `_MemorizationGateDoFn.process(self, row, wait_on_write=None)` with the same `AsIter` guard; update its docstring (removes the WS3 final-review caveat — now field-motivated by the b2 run); test mirroring Task 2's; full suite green on ws3.
- [ ] Push all three branches (PRs #3/#4/#6 update in place).

**Out of scope:** b1 embed-phase regression (image bisect, M4/CI); `COL_047` BYTES-as-STRING (source-owner escalation, report #5); probe `LIMIT 50` (report #6 — minor tooling, fold into WS5); GPU-image Beam bump verification beyond pin edits (CI build + M4 run).
