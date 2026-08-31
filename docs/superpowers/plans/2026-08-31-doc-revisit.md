# Documentation Revisit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `README.md` as the single project entry point from the corp experimentation doc, eliminate the committed `integration_test/` layer (evidence relocated to `docs/releases/<version>/evidence/`), rename local scratch `integration_tests/` → `runs/`, align all `docs/` with post-v0.1.0 reality, create the tracked Medium article layer (`docs/articles/` index + Article 1), and teach the visual-first skill the drawio convention.

**Architecture:** Documentation-only branch (`doc-revisit`, single PR) plus two surgical code changes: `discover_artifact_sets()` in `scripts/release/make_release_report.py` gains the `docs/releases/<version>/evidence/` layout (legacy `integration_test/` kept as fallback), and e2e script path constants move from `integration_test(s)` to `runs/`. Everything else is Markdown, `.claude/` recipes, `.github/` prompts, and drawio/PNG assets.

**Tech Stack:** Python 3 (pytest via `uv run --no-sync python3 -m pytest` — laptop quirk), mermaid, next-ai-drawio MCP (`.drawio` + exported PNG), gh CLI.

**Spec:** `docs/superpowers/specs/2026-08-31-doc-revisit-design.md`

## Global Constraints

- **Anonymized identifiers only**: never write real corp table/column names; aliases (`A_TABLE`, `B_TABLE`, `X_TABLE`, `COL_XXX`) everywhere including commit messages.
- **No managed-GCP proposals**: no Vertex AI, Dataplex, Looker, external LLM APIs anywhere in new prose.
- Laptop test invocation: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` (never bare `pytest`).
- Lint gates: `uv run ruff check .` and `uv run mypy packages/sdfb-core/src` must stay at 0 errors.
- Visual-first rules apply to every new doc: figures carry claims, mermaid house-style classDef block inline, no orphan numbers (measured claims cite their source).
- Commit messages follow repo style `docs(scope): …` / `feat(scope): …`, each ending with the Claude Co-Authored-By trailer.
- The owner's untracked `doc_revesit_prompt.md` is never committed or edited.
- Historical ADR/design bodies are never rewritten — only status banners and directory-path spellings change.

---

### Task 1: Spec + plan commit, visual-first skill drawio update (§E)

**Files:**
- Commit (already written): `docs/superpowers/specs/2026-08-31-doc-revisit-design.md`, `docs/superpowers/plans/2026-08-31-doc-revisit.md`
- Modify: `.claude/skills/visual-first-documentation/SKILL.md`

**Interfaces:**
- Produces: the drawio convention (`.drawio` source + exported `.png` side-by-side in the same `assets/` dir) that Tasks 4 and 6 follow; the evidence-chain top layer `runs/<JOB_ID>/` that Task 3's renames make true.

- [ ] **Step 1: Commit spec + plan**

```bash
git add docs/superpowers/specs/2026-08-31-doc-revisit-design.md docs/superpowers/plans/2026-08-31-doc-revisit.md
git commit -m "docs(revisit): approved doc-revisit spec + implementation plan"
```

- [ ] **Step 2: Update the source-of-truth chain table (SKILL.md line 29)**

Replace the raw-evidence row:

```markdown
| Raw evidence | `runs/<JOB_ID>/` (local, gitignored); release-promoted copies in `docs/releases/<version>/evidence/<JOB_ID>/` | immutable; never edited |
```

- [ ] **Step 3: Add a drawio row to "Choosing the form" (after the generated-PNG row, ~line 88)**

```markdown
| Cloud architecture with GCP services, audience-facing/Medium figures, any diagram mermaid renders cramped | **drawio → exported PNG** | official GCP icons, full layout control; Medium cannot render mermaid |
```

- [ ] **Step 4: Add a `## Draw.io diagrams — when mermaid is not enough` section (after the mermaid house-style section)**

Content it must carry (write as prose+rules in the skill's voice):
- Prefer drawio over mermaid when: (a) the diagram shows GCP services and benefits from official GCP icons (GCS, Dataflow, BigQuery, Composer, Artifact Registry); (b) the figure is audience-facing (Medium article, deck, README flagship); (c) mermaid renders it too cramped (>~12 nodes or crossing edges).
- Convention: the `.drawio` source and its exported `.png` are committed **side-by-side in the same `assets/` dir** with the same basename (`assets/<topic>-architecture.drawio` + `.png`). The PNG is what docs embed; the `.drawio` is the regenerable source.
- The provenance table row for a drawio figure names the `.drawio` file as source (export via the next-ai-drawio MCP plugin or the drawio desktop app — record which).
- The mermaid/drawio boundary: **GitHub-facing flow/DAG diagrams stay inline mermaid** (diffable, renders on GitHub); **article-bound and architecture diagrams are drawio+PNG**. Never maintain the same diagram in both forms — pick by destination.
- Node color vocabulary carries over from the mermaid house style (Beam=orange `#eb6834`, stores=blue `#2a78d6`, GPU=purple `#7a3fd1`, CPU=green `#1baf7a`, data=gray `#6b7280`) so drawio and mermaid diagrams read as one system.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/visual-first-documentation/SKILL.md
git commit -m "docs(skills): visual-first — drawio convention + runs/ evidence layer"
```

---

### Task 2: Evidence relocation + release-tooling re-point (§C, code half)

**Files:**
- Create: `docs/releases/v0.1.0/evidence/2026-08-26_05_01_16-3186876581127148459/` (9 files restored from HEAD)
- Modify: `scripts/release/make_release_report.py` (docstring L21, comments L115–131, `_run_git` docstring L141, `discover_artifact_sets` L154–192)
- Modify: `.github/workflows/release_tag_report.yaml:6` (comment)
- Modify: `.claude/skills/release-report/SKILL.md:13,34,37`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_make_release_report.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `discover_artifact_sets(ref)` returning job dicts from BOTH `docs/releases/<version>/evidence/<job>/real/*.json` (new) and `integration_test/...` (legacy fallback, unchanged semantics). Task 5's playbook and Task 4's README cite `docs/releases/v0.1.0/evidence/` as the canonical evidence home.

- [ ] **Step 1: Restore the R6 10M bundle from HEAD into the new evidence home**

```bash
mkdir -p docs/releases/v0.1.0/evidence
git restore --source=HEAD --worktree -- integration_test/
mv integration_test/2026-08-26_05_01_16-3186876581127148459 docs/releases/v0.1.0/evidence/
rmdir integration_test
git add -A integration_test docs/releases/v0.1.0/evidence
git status --short   # expect: 9 renames (R) integration_test/... -> docs/releases/v0.1.0/evidence/...
```

- [ ] **Step 2: Write the failing test** (mirror `test_discover_artifact_sets_supports_real_bundle_layout` at line 179, same `_make_tree`/`json.dumps` helpers):

```python
def test_discover_artifact_sets_supports_release_evidence_layout(tmp_repo):
    """New canonical layout: bundles under docs/releases/<version>/evidence/;
    oss/ twins and non-evidence release files are skipped."""
    job = "2026-08-26_05_01_16-3186876581127148459"
    tree = _make_tree(
        tmp_repo,
        {
            f"docs/releases/v0.1.0/evidence/{job}/real/gcp_metrics.json": json.dumps(
                {"execution_seconds": 60}
            ),
            f"docs/releases/v0.1.0/evidence/{job}/real/offline_metrics.json": json.dumps(
                {"row_count": 10}
            ),
            f"docs/releases/v0.1.0/evidence/{job}/oss/gcp_metrics.json": json.dumps(
                {"execution_seconds": 999}
            ),
            "docs/releases/v0.1.0/report.md": "# not an artifact",
        },
    )
    sets = make_release_report.discover_artifact_sets(tree)
    assert sets == {
        job: {"gcp": {"execution_seconds": 60}, "offline": {"row_count": 10}}
    }
```

(Adapt helper signatures to whatever `_make_blob`/`_make_tree` at lines 66/70 actually take — the existing bundle-layout test is the template.)

- [ ] **Step 3: Run it — expect FAIL** (`sets == {}` because discovery only lists `integration_test`):

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_make_release_report.py -q
```

- [ ] **Step 4: Implement the dual-layout discovery** in `make_release_report.py`. Replace the listing call (L163) and the parsing block (L172–183):

```python
    listing = _run_git(
        "ls-tree", "-r", "--name-only", ref, "--", "docs/releases", "integration_test"
    )
```

```python
        parts = path.split("/")
        job_id: str | None = None
        key: str | None = None
        if parts[0] == "integration_test" and len(parts) >= _ARTIFACT_PATH_PARTS:
            job_id, basename = parts[1], parts[-1]
            if len(parts) == _ARTIFACT_PATH_PARTS:
                key = _ARTIFACT_BASENAMES.get(basename)
            elif len(parts) == _ARTIFACT_PATH_PARTS + 1 and parts[2] == "real":
                key = _BUNDLE_ARTIFACT_BASENAMES.get(basename)
        elif (
            len(parts) == _EVIDENCE_PATH_PARTS
            and parts[:2] == ["docs", "releases"]
            and parts[3] == "evidence"
            and parts[5] == "real"
        ):
            job_id = parts[4]
            key = _BUNDLE_ARTIFACT_BASENAMES.get(parts[-1])
        if job_id is None or key is None:
            continue
```

Add beside `_ARTIFACT_PATH_PARTS`:

```python
# "docs/releases/<version>/evidence/<job_id>/real/<basename>" — the canonical
# evidence layout from 2026-08-31 on (doc-revisit); integration_test/ remains
# discoverable so pre-relocation tags stay regenerable.
_EVIDENCE_PATH_PARTS = 7
```

Update the module docstring (L21), the layout comments (L115, L120, L128), `_run_git`'s docstring (L141: "a ref with no artifact tree"), and `discover_artifact_sets`'s docstring (L155–162) to describe the canonical evidence layout + legacy fallback.

- [ ] **Step 5: Run the release-report tests — expect ALL PASS** (same command as Step 3; legacy-layout tests must stay green).

- [ ] **Step 6: Update the two release-tooling consumers**
  - `release_tag_report.yaml:6` comment → "…from committed `docs/releases/<version>/evidence/` bundles (legacy `integration_test/` for old tags)".
  - `.claude/skills/release-report/SKILL.md`: L13 discovery description; L34/L37 leak-scan snippet →
    ```python
    job_dir = Path("docs/releases") / version / "evidence" / job_id
    ```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(release): evidence lives in docs/releases/<version>/evidence/ — R6 10M bundle relocated, dual-layout discovery"
```

---

### Task 3: `integration_tests/` → `runs/` rename + runtime-path sweep (§C, path half)

**Files (from the reference sweep — every non-playbook, non-plans runtime reference):**
- Modify constants: `scripts/e2e/history_mappings.py:34` (+docstrings 5, 9), `scripts/run_tableset.py:45`, `scripts/e2e/e2e_bundle_export.py:170,190` (+docstring 34–40), `scripts/e2e/e2e_fetch_samples.py:195` (+6, 26)
- Modify docstrings: `scripts/e2e/e2e_validation_analysis.py:26–27`, `scripts/e2e/source_synthetic_stats_diff.py:27–28`, `scripts/e2e/redact_doc.py:19–21`, `scripts/e2e/build_full_report.py:27–28`, `public_cloud/deploy/gcp/run_e2e.sh:97–98`
- Modify: `.gitignore:61` (and drop the uncommitted `docs/articles` line)
- Modify `.claude/`: `agents/e2e-interpreter.md:3,12`, `agents/gcp-deploy-runner.md:14`, `skills/gcp-e2e-run.md:38–62`
- Modify `.github/`: `PULL_REQUEST_TEMPLATE.md:11`, `prompts/end_to_end_validation_report_generation.prompt.md` (31 refs), `prompts/llm_prompt_constraint_recommender.prompt.md` (7), `prompts/freetext_crosscheck_report_generation.prompt.md` (3), `prompts/visual_fk_pk_ddl_contract_guide.prompt.md` (7)
- Modify provenance comments: `scripts/doc/make_{wave4_verification,wave4,r6_scale,marginal_fidelity,ws5,ws6,constraint_router}_figures.py`
- Modify path spellings only: ADRs `0020:5, 0023:4-5, 0027:119, 0029:78,81, 0030:102, 0033:15`; designs `2026-07-26-ws5:8, 2026-07-27-ws6:11,462, 2026-08-10-prompt-constraints:13 (markdown link → plain code path), 2026-08-20-measurement:262, 2026-08-22-constraint-router:309, 2026-08-22-fk-model:120,153,154,165, 2026-08-29-r6:6,7`; `docs/DDL_CONTRACT_GUIDE.md:205`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_e2e_bundle_export.py:80,107,141,183,213,245` (tmp names → `runs`), `test_history_mappings.py:3`, `test_make_release_report.py:23,26` (docstrings), `packages/sdfb-tests/tests/unit/engines/test_constraint_sampler.py:4`

**Interfaces:**
- Consumes: Task 2's evidence home (`docs/releases/<version>/evidence/`) for the "promotion" wording.
- Produces: the one path rule every later task writes by — **runtime/scratch = `runs/<JOB_ID>/` (gitignored); release-promoted evidence = `docs/releases/<version>/evidence/<JOB_ID>/`**. `e2e_bundle_export.py --out-root` defaults to `Path("runs")`; `e2e_fetch_samples.py --out-dir` defaults to `"runs"`; `history_mappings.DEFAULT_PATH = Path("runs/history_mappings_replacement.json")`; `run_tableset._FK_MODELS_DIR = "runs/fk_models"`.

- [ ] **Step 1: Apply the two rename rules across every file listed above.** Rule S (old singular, was the committed dir): runtime usages → `runs/`; the only `docs/releases/<version>/evidence/` mentions are where promotion/committed evidence is meant (PR template, prompts' "committed bundle" phrasing). Rule P (old plural): → `runs/` everywhere. `.gitignore:61` becomes `runs/`; delete the uncommitted `docs/articles` ignore line (articles are tracked per spec §D).

- [ ] **Step 2: Add the promotion step where bundles used to land in the committed dir.** In `.claude/skills/gcp-e2e-run.md` (after the bundle-export command) and `.github/PULL_REQUEST_TEMPLATE.md:11`:

```bash
# promote the bundle into release evidence (committed):
cp -R runs/<JOB_ID> docs/releases/<version>/evidence/<JOB_ID>
```

- [ ] **Step 3: Rename the local scratch dir so existing tooling keeps working:**

```bash
mv integration_tests runs
```

- [ ] **Step 4: Verify no stale references remain outside allowed files:**

```bash
grep -rn "integration_test" --include="*.py" --include="*.sh" --include="*.md" --include="*.yaml" --include=".gitignore" . \
  | grep -v ".git/" | grep -v "docs/superpowers/" | grep -v "doc_revesit_prompt" \
  | grep -v "make_release_report" | grep -v "test_make_release_report" \
  | grep -v "RUN_PLAYBOOK"
```

Expected: empty (playbooks are Task 5; release tooling keeps legacy-fallback mentions; superpowers history untouched until Task 7).

- [ ] **Step 5: Run the touched test files + lints — expect PASS:**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/ -q
uv run ruff check .
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs(e2e): local scratch integration_tests/ -> runs/; runtime paths + prompts + recipes swept"
```

---

### Task 4: README rewrite + corp-doc deletion (§A)

**Files:**
- Create: `docs/assets/architecture-overview.drawio` + `docs/assets/architecture-overview.png` (drawio MCP, GCP icons)
- Rewrite: `README.md`
- Delete: `docs/corp_experimentation_request.md` (orphan — only `doc_revesit_prompt.md` references it)

**Interfaces:**
- Consumes: Task 1's drawio convention; Task 2/3's path rule for §13 and the evidence `<details>` table.
- Produces: README section anchors that Task 5's guides and Task 6's articles link to; the architecture PNG that Article 1 reuses.

- [ ] **Step 1: Measure the live claims** (numbers go into the README once, here):

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q --collect-only 2>/dev/null | tail -1   # test count
ls docs/adr/*.md | wc -l   # ADR count (34 incl. index)
```

- [ ] **Step 2: Grep-verify every glossary term** before it survives: for each of the corp doc's 42 terms, confirm the backing identifier/concept exists on HEAD (e.g. `grep -rn "identical_match_rate\|column_copy_ratios\|entropy_norm\|top1_share\|source_table_stats" packages/`). Cut any term with no hit; note cuts in the commit message.

- [ ] **Step 3: Build the drawio architecture diagram** (next-ai-drawio MCP: `create_new_diagram` → `edit_diagram` → `export_diagram` PNG). Content = the corp doc's mermaid (L34–L50) updated to ADR 0030 single-job relational reality: BigQuery source tables (parents + child) → Dataflow single job (side-input parent keys, engine CPU bulk sampling, vLLM GPU subprocess, validation) → BigQuery landing/DLQ/audit; GCS model weights warm-pull; official GCP icons; color vocabulary from the skill (Beam orange, stores blue, GPU purple, CPU green). Claim: *"one Dataflow job generates parent and child tables with FK integrity by construction — nothing leaves the project boundary."* Save both files to `docs/assets/`.

- [ ] **Step 4: Write `README.md`** with exactly this section order (spec §A), donor text from `docs/corp_experimentation_request.md` with these rewrites:

1. `# synthetic-llm-dataflow-bigquery` + the one-liner + Beam Summit 2025 callout (`docs/assets/beam-summit-2025-acceptance.png`)
2. ToC (anchor links to every `##`)
3. *What this does / why* — Abstract (L20) + Problem (L24–26) condensed; drop "Accepted for presentation" duplicate sentence (callout owns it)
4. *Architecture at a glance* — embed `docs/assets/architecture-overview.png`; keep a small inline mermaid only if a flow detail needs diffable form
5. *Generation engines* — L52–57 verbatim minus registry framing; state sdgx is the locked B.2 backend (no bake-off language); fold in RAG retrieval/embedder highlights (L59–78) as a compact subsection linking `docs/designs/2026-07-25-rag-retrieval-geometry-roadmap.md` and ADR 0013/0014
6. *`generation_plan` — per-type synthesis* — table L121–130 verbatim (it is current), + one sentence on the constraint router (ADR 0028) and `route:llm` prompt constraints (ADR 0024) linking `docs/DDL_CONTRACT_GUIDE.md`
7. *Relational generation (PK/FK)* — **new**: relationships as versioned config (`config/relationships/*.yaml`, ADR 0032, table descriptions never read), joint FK draws with IPF-fitted weights (ADR 0031), orphan-rate BLOCKER gate, launch scenarios (ADR 0029), single-job pipeline (ADR 0030), pool ladder integrity at 10M (ADR 0033); measured claim: **0/10M orphans in the R6 FK-enforced pair** citing `docs/releases/v0.1.0/evidence/2026-08-26_05_01_16-3186876581127148459/`; reuse figure `docs/designs/assets/fk-orphan-rate.png`
8. *Stats-driven fidelity* — 4–6 sentences (deciles, inverse CDF, entropy/`top1_share`, null-pattern mix) linking ADR 0022 + `docs/designs/2026-08-05-source-table-stats.md`; reuse `stats-inverse-cdf.png`
9. *Validation & data quality* — L82–100 with eval framework reframed: Tier-1/2/3 code exists on HEAD (`sdfb_core/evaluation/`), full-run wiring still WIP on `ws3-eval-framework`; memorization gate wording verbatim
10. *CPU/GPU split + vLLM serving* — L102–115 condensed to two paragraphs
11. *Getting started* — laptop / M4 / personal-GCP triple, linking `docs/M4_SETUP.md`, `docs/DEPLOYMENT_PREREQUISITES.md`, `public_cloud/deploy/gcp/README.md`; the four verify commands from CLAUDE.md
12. *CI/CD* — 3–4 sentences + link `docs/CICD.md` and the release Action (`release_tag_report.yaml` → `docs/releases/`)
13. *Integration testing & validation reports* — runs land in local `runs/<JOB_ID>/`, promoted to `docs/releases/<version>/evidence/`; links `docs/RUN_PLAYBOOK.md`, `docs/E2E_TEST_MATRIX.md`, the three `.github/prompts/` report generators
14. *Documentation map* — table: ADRs (`docs/adr/`, 33 + index), design docs, guides, releases, articles (`docs/articles/`), specs
15. *Glossary* — the surviving corp terms (L150–191) + new relational block: **orphan rate, joint key draw, IPF weights, relationship config/card, constraint router, pool ladder, launch scenarios (landing-table / derived-FK / closure), parents-first / single-job propagation, identity column**
16. *License* — Apache-2.0
17. Collapsed `<details><summary>Evidence & provenance</summary>` — corp table L197–207 with the eval row's branch note updated and a new R6 row pointing at the relocated evidence bundle

Numbers to fix while porting: `~700 tests` → the Step-1 measured count; `1M-row scale` → 1M and 10M measured; "multi-table (M2)" roadmap line → shipped in v0.1.0 (roadmap now: extended fidelity, production hardening); "*internal repo link*" → relative links.

- [ ] **Step 5: Delete the corp doc, verify no inbound links:**

```bash
git rm docs/corp_experimentation_request.md
grep -rn "corp_experimentation" --include="*.md" . | grep -v doc_revesit_prompt   # expect empty
```

- [ ] **Step 6: Render-check the README** (view the PNG embed paths exist; run the Task-7 link checker early on README only) and commit:

```bash
git add -A
git commit -m "docs(readme): single entry point rebuilt from experimentation doc — relational flagship, verified glossary, corp doc removed"
```

---

### Task 5: docs/ alignment pass (§B)

**Files:**
- Rewrite: `docs/ROADMAP.md`
- Merge+delete: `docs/RUN_PLAYBOOK.md` ← evergreen parts of `docs/RUN_PLAYBOOK_WS8.md`; `git rm docs/RUN_PLAYBOOK_WS8.md`
- Modify guides: `docs/CICD.md`, `docs/DEPLOYMENT_PREREQUISITES.md`, `docs/M4_SETUP.md`, `docs/E2E_TEST_MATRIX.md`, `docs/MODEL_LAYOUT.md`, `docs/M4_LOCAL_SMOKE.md`
- Modify banners only: 7 design docs (list in Step 4)
- Create: `docs/superpowers/specs/README.md` (historical disclaimer)

**Interfaces:**
- Consumes: Task 3's path rule; Task 4's README anchors (guides de-dup by linking, never copying).
- Produces: the playbook §-structure Article 7 (Task 6 index stub) cites.

- [ ] **Step 1: ROADMAP.md** — rewrite to: M1 table all-✅ (row 11 → ✅ done, "R-series campaigns on T4/L4, 1M+10M measured — see `docs/releases/`"); drop the immutable-constraints "Single-table only" line (superseded by v0.1.0 relational); replace the 2026-07 cycle narrative with a 4-line "shipped history" pointer (releases index + ADR index own history); M2 section: mark multi-table/relational **shipped in v0.1.0** and keep only the still-open remainder (Tier S routing, B.2 relational parity, eval-framework merge, LMCache canary, doc site) as forward items with the "Remaining:" list promoted out of the dead bullet; link `docs/releases/README.md`.

- [ ] **Step 2: RUN_PLAYBOOK merge** — into `RUN_PLAYBOOK.md` bring from WS8: §0 from-scratch reset (L21–58) as new "§0 Reset"; the defaults block (L85–92) folded into §3 Dataflow options; §3 milestone-log dictionary (L115–159) as new "§ Milestone log dictionary"; §4 pass criteria (L160–230) replacing/extending the 8-criteria list; §6a/6b/6c recipes (L366–498, L521–541) as the current launch recipes. Do NOT port: campaign map, per-run R0–R7 rows, the §5 remediation chronicle, §6b′ run log (git history + ADR 0027/0033 own them). While merging: fix `(branch e2e-hardening, …)` at L79 and `not yet measured` at L443 (WS6 was measured — cite `docs/designs/2026-07-27-ws6-pipeline-shape.md`); rewrite the 3-command recipe (L258–352) to the `runs/<JOB_ID>` + promotion contract from Task 3; update the L3–6 banner (WS8 pointer gone). Then `git rm docs/RUN_PLAYBOOK_WS8.md`.

- [ ] **Step 3: Guides** — surgical fixes only (each guide keeps its depth; anything the README now owns becomes a link):
  - `CICD.md`: L23 registry row → Artifact Registry per ADR 0015 (JFrog framing at L11, 67–68, 84, 125, 131, 136–137 reworded to "corporate mirror (historical) / GAR (current)"); verify the L131 `apache-beam==2.71.0` pin against `pyproject.toml` and fix if drifted.
  - `DEPLOYMENT_PREREQUISITES.md`: add a `config/relationships/*.yaml` prerequisite row (ADR 0032 — required input for relational runs, gs:// override supported); same ADR 0015 registry rewording at L149.
  - `M4_SETUP.md`: L89 "68 laptop tests" → the Task-4 measured count; replace the shipped Step A–D task-order block (L158–186) with a pointer to ROADMAP + RUN_PLAYBOOK.
  - `E2E_TEST_MATRIX.md`: L3 + L79 dead `vllm-setup-worker` GitHub links → relative repo paths; add one Tier row for relational/FK runs (`--relationships` + scenario flags, R6 recipe pointer).
  - `MODEL_LAYOUT.md`: M1-§ future framing (L13, 42, 81, 173, 202, 204) → past tense/"shipped"; leave layout content untouched.
  - `M4_LOCAL_SMOKE.md`: L128 "(M1 §11)" → "Dataflow runs (see RUN_PLAYBOOK)".

- [ ] **Step 4: Design-doc banners** (banner lines only, bodies untouched):
  - `2026-07-07-evaluation-framework-design.md`: → `**Status: PARTIALLY IMPLEMENTED** — evaluation code on HEAD (sdfb_core/evaluation/), full-run wiring on ws3-eval-framework`
  - `2026-07-26-ws5-generation-throughput.md`: → IMPLEMENTED + E2E MEASURED (merged to master; branch deleted)
  - `2026-07-27-ws6-pipeline-shape.md`: drop the stale 748-test/branch parenthetical
  - `2026-08-05-freetext-expansion-modes.md`: branch ref → "merged via PR #13, v0.1.0"
  - `2026-08-10-prompt-constraints.md`: → "ACCEPTED — R-series acceptance measured (R1-c/R6)"
  - `2026-08-23-referential-integrity-joint-fk-draws.md`: → "ACCEPTED — R6 pair landed (1M+10M, 0 orphans)"
  - `2026-08-24-relationships-as-config.md`: → "ACCEPTED — first Dataflow launch verified (R6)"

- [ ] **Step 5: specs disclaimer** — create `docs/superpowers/specs/README.md`:

```markdown
# Historical design specs

Point-in-time planning artifacts, kept for provenance. Current truth lives in `docs/adr/` and `docs/designs/`; these are never updated retroactively.
```

- [ ] **Step 6: Commit** (one commit per step-group is fine: roadmap+playbooks, then guides+banners+disclaimer):

```bash
git add -A && git commit -m "docs(align): ROADMAP/playbook merge to post-v0.1.0 reality; WS8 playbook folded in"
```

---

### Task 6: Articles layer — index + Article 1 (§D)

**Files:**
- Create: `docs/articles/README.md` (series index)
- Create: `docs/articles/01-building-banking-synthetic-data-intro.md` (full draft)
- Create if needed: `docs/articles/assets/` drawio+PNG figures Article 1 needs beyond reuse

**Interfaces:**
- Consumes: Task 4's `docs/assets/architecture-overview.png` (reused, not redrawn); the README glossary anchors; design assets in `docs/designs/assets/`.
- Produces: the 10-slot series index Articles 2–10 will be drafted against.

- [ ] **Step 1: Series index** — `docs/articles/README.md`: purpose paragraph (VCS-tracked Medium sources, copy-pastable, provenance footers dropped on paste; two-way channel with readers), the owner's Medium profile link, a status legend (`Draft / Published / Needs-sync`), then a 10-row table — # · title · scope (3–5 lines) · sources (ADRs/designs/code) · figures (existing assets by name) · status · medium_url. Slots per spec §D: 1 intro/why · 2 type system & freetext resolution · 3 common runtime · 4 b1_rag deep dive · 5 b2_library · 6 relational by construction (0/10M orphans) · 7 integration testing with GH agents · 8 stats/stress/scale · 9 CI/CD & infra · 10 evaluation framework (deferred, branch-resident). Slots 2–10 are scope stubs (status `Stub`); slot 1 `Draft`.

- [ ] **Step 2: Article 1 full draft** — front-matter + body, Medium-pastable (PNG only, no mermaid):

```markdown
---
sources: [docs/adr/0001-two-engine-architecture.md (adjust to real ADR ids), docs/adr/0013, docs/adr/0022, docs/adr/0028-0033, README.md]
figures: [docs/assets/architecture-overview.png, docs/designs/assets/stats-inverse-cdf.png, docs/designs/assets/fk-orphan-rate.png]
medium_url: null
synced_at_commit: <sha at draft time>
---
```

Body arc (each figure preceded by its one-sentence claim):
1. Hook — why realistic fake banking data is hard (privacy/regulatory constraints; no external LLM APIs; Beam Summit 2025 acceptance as the frame)
2. The problem shape — masking degrades, fixtures don't scale, real rows can't leave the boundary
3. HLA — the architecture PNG; self-hosted vLLM inside Dataflow GPU workers; O(1) LLM calls per run, never per row
4. Engines at a glance — b1_rag vs b2_library in one table
5. The `generation_plan` tour — per-type table (constants, categoricals, numerics via inverse-CDF, temporal with sentinel preservation, identifiers, freetext pointer to Article 2 for the deep dive)
6. The flagship result — relational generation teaser, 0/10M orphans (full story = Article 6)
7. Glossary foundation — the ~12 terms a reader needs for the whole series: synthetic data, reference sample, fidelity, memorization, copy rate, pool, freetext column, guided JSON decoding, DLQ, blocker gate, decile vector, orphan rate — each 1–2 sentences, aligned with the README glossary
8. What's next — series map (from the index) + invitation for feedback/discussion
9. Provenance footer (dropped on paste): sources, figures, synced commit

- [ ] **Step 3:** If the generation_plan tour needs its own visual (types → strategy routing), build it as drawio+PNG in `docs/articles/assets/generation-plan-routing.{drawio,png}` per the Task-1 convention; otherwise reuse existing assets only.

- [ ] **Step 4: Commit**

```bash
git add docs/articles
git commit -m "docs(articles): Medium series index (10 slots) + Article 1 full draft"
```

---

### Task 7: Final sweep, verification, PR (§F)

**Files:**
- Delete: `docs/superpowers/plans/` (all 16 — 15 historical + this plan; git history keeps them; spec survives in `specs/`)

**Interfaces:** consumes everything; produces the PR.

- [ ] **Step 1:** `git rm -r docs/superpowers/plans/`

- [ ] **Step 2: Dead-link sweep** — check every relative markdown link resolves:

```bash
python3 - <<'EOF'
import re, pathlib
bad = []
for md in pathlib.Path(".").rglob("*.md"):
    if ".git" in md.parts or "runs" in md.parts or md.name == "doc_revesit_prompt.md":
        continue
    for m in re.finditer(r"\]\((?!http|#|mailto)([^)#]+)", md.read_text(errors="ignore")):
        target = (md.parent / m.group(1).strip()).resolve()
        if not target.exists():
            bad.append(f"{md}: {m.group(1)}")
print("\n".join(bad) or "all links resolve")
EOF
```

Fix every hit.

- [ ] **Step 3: Residual-reference grep** — `grep -rn "integration_test" --include="*.md" --include="*.py" --include="*.sh" --include="*.yaml" . | grep -v .git/` — remaining hits must ONLY be: `make_release_report.py` + its test (legacy fallback), `release_tag_report.yaml` legacy comment, the spec, and `doc_revesit_prompt.md`.

- [ ] **Step 4: Full verification — expect all green:**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run ruff check .
uv run mypy packages/sdfb-core/src
```

- [ ] **Step 5: Commit, push, PR:**

```bash
git add -A && git commit -m "docs(revisit): retire historical plans/; final link + reference sweep"
git push -u origin doc-revisit
gh pr create --title "docs: README single entry point, evidence relocation, docs alignment, Medium articles layer" --body "<summary per template: spec link, section A–F map, verification output>"
```
