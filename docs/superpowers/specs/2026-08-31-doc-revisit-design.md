# Documentation revisit — approved design (2026-08-31)

> **Status: APPROVED** (per-section user approval, 2026-08-31). Implements the
> doc-cleanup brief in `doc_revesit_prompt.md` (untracked scratch, owner's copy).
> Executed on branch `doc-revisit`, single PR.

## Context

Post-ws8/v0.1.0 the documentation has drifted: the README is a thin M1-era
stub, the corp experimentation-request doc holds the best project overview but
predates the relational merge, the committed `integration_test/` evidence dir
is being eliminated, and a Medium article series (Beam Summit 2025 momentum)
needs a VCS-tracked home aligned with the implementation. Audit findings that
shaped this design: `sdfb_core/evaluation/` is empty on master (eval framework
is branch-resident — "WIP" framing is accurate), ROADMAP's M1 table is mostly
current but its prose drifts, and the two run playbooks split roles (evergreen
vs WS8 campaign matrix) with little literal duplication but real navigational
bloat.

## A — New `README.md` (single entry point)

Donor body: `docs/corp_experimentation_request.md`, de-corporatized (registry
framing like "Experimentation Request" and the Title/Status headers dropped)
and **updated to post-ws8 reality** — the corp doc predates the relational
merge, so it is missing the flagship result. Structure:

1. Title + one-liner + Beam Summit callout (acceptance PNG)
2. **ToC** (anchor links, at the very top)
3. What this does / why (abstract + problem, condensed)
4. Architecture at a glance (updated diagram, house-style classes; reflects
   ADR 0030 single-job relational pipeline)
5. Generation engines — b1_rag / b2_library (current state: sdgx locked; no
   "bake-off scheduled" language)
6. `generation_plan` per-type table incl. freetext routing
7. **Relational generation (PK/FK) — new section**: relationships-as-config,
   joint FK draws, orphan BLOCKER gate (ADR 0028–0033), R6 result (0/10M
   orphans)
8. Stats-driven fidelity (deciles, inverse CDF, entropy — brief, links design
   docs, reuses committed figures)
9. Validation & data quality (three lines of defense, DLQ, thresholds +
   memorization gates)
10. CPU/GPU split + vLLM serving
11. Getting started — laptop dev / M4 / personal-GCP, linking `M4_SETUP`,
    `DEPLOYMENT_PREREQUISITES`, `public_cloud/deploy/gcp`
12. **CI/CD** — dedicated section → `docs/CICD.md`, the workflows, release
    report
13. **Integration testing & validation reports** — dedicated section →
    `RUN_PLAYBOOK.md`, `E2E_TEST_MATRIX.md`, the `.github/prompts/` report
    generators
14. Documentation map (ADRs, designs, guides, articles)
15. **Glossary** — every corp-doc term re-verified against the repo
    (grep-backed; stale terms cut) and extended with the relational
    vocabulary: orphan rate, joint key draws, IPF, constraint router, pool
    ladder, relationship card, etc.
16. License

The corp doc's internal "Evidence & provenance" table survives as a collapsed
`<details>` block at the README bottom (single source, no reader overload).
Claims re-measured at write time (test count is ~1327 now, not "~700"; eval
framework marked branch-WIP). Then **`docs/corp_experimentation_request.md`
is deleted**.

## B — `docs/` alignment pass

- **ROADMAP.md**: refresh statuses (ws8 relational work = much of M2
  delivered), prune drifted prose.
- **RUN_PLAYBOOK.md**: refresh stale references (branch `e2e-hardening` era),
  **merge the still-operational parts of `RUN_PLAYBOOK_WS8.md` in, delete the
  WS8 file** (git history keeps the campaign log).
- **Design docs**: no rewriting of history — add/fix status banners
  (`ACCEPTED / SUPERSEDED / AMENDED-BY`) per the visual-first contract, fix
  `integration_tests/` path references.
- **Guides** (`CICD`, `DEPLOYMENT_PREREQUISITES`, `M4_SETUP`,
  `DDL_CONTRACT_GUIDE`, `E2E_TEST_MATRIX`, `MODEL_LAYOUT`, `M4_LOCAL_SMOKE`):
  verify claims vs implementation, deduplicate anything the README now owns
  (guides keep the deep content; README links, never copies).
- **`docs/superpowers/plans/` deleted**; `specs/` kept + one-line historical
  disclaimer README.
- ADR index (`docs/adr/README.md`) completeness check.
- The owner's untracked `doc_revesit_prompt.md` is left alone.

## C — `integration_test` elimination

- Commit the deletion of tracked `integration_test/` (already staged in the
  worktree).
- **Move the R6 10M bundle to `docs/releases/v0.1.0/evidence/`** so the
  evidence the README/articles cite lives at tip, not only behind the tag.
- Re-point `make_release_report.py` discovery to
  `docs/releases/<version>/evidence/` (legacy `integration_test/` path kept
  as fallback so past tags stay regenerable); update its tests,
  `release_tag_report.yaml`, and the `release-report` skill.
- **Local plural `integration_tests/`** (gitignored scratch the e2e scripts
  write into): keep the mechanism but rename the output dir to `runs/` via
  the scripts' path constant; update `.gitignore` + docs + `.claude`
  agents/skills that name it.
- Visual-first skill's source-of-truth chain top layer re-pointed
  accordingly.

## D — Articles layer (`docs/articles/`)

- `docs/articles/README.md` = series index: per-article scope, source map
  (ADRs/designs/code), figure map, status (`Draft / Published / Needs-sync`),
  Medium URL.
- Each article: front-matter (`sources`, `figures`, `medium_url`,
  `synced_at_commit`) + a provenance footer dropped when pasting to Medium.
  Bodies are Medium-pastable: **PNG figures only** (Medium cannot render
  mermaid — this is where drawio exports earn their place), reusing
  `docs/designs/assets/` where a figure already exists.
- **Series (10 slots)** — the owner's 8 with two structural changes: the
  freetext deep-dive moves out of the overloaded Article 1 into Article 2
  (where freetext was already scoped), and **relational PK/FK gets its own
  article** (the Beam-Summit-promising flagship result, not an intro bullet):
  1. Intro & why — problem, constraints, HLA, engines at a glance,
     generation_plan tour, glossary foundation
  2. Type system & freetext resolution — router, pools, thresholds,
     failovers, RAG interplay (the requested diagrams)
  3. Common runtime — CPU/GPU split, vLLM serving, uniqueness, DLQ,
     autoscaling
  4. b1_rag deep dive — embedders, chunking, FAISS, retrieval geometry,
     byte-level decisions
  5. b2_library deep dive
  6. Relational generation by construction — PK/FK, joint draws, 0/10M
     orphans
  7. Integration testing with GH agents — prompts, metrics,
     `_full_report.md`
  8. Stats, stress & scale — fidelity math, 1M/10M performance
  9. CI/CD & infra — flex templates, GAR, uv, dtype map, L4/T4/G2
  10. Evaluation framework (deferred until the branch merges)
- **This effort drafts the index + Article 1 in full**; the rest get scope
  stubs in the index only.

## E — `visual-first-documentation` skill update

New **drawio section**: when to prefer drawio over mermaid (GCP architecture
wanting official GCP icons; audience-facing/Medium figures; anything mermaid
renders too cramped), the convention `.drawio` source + exported `.png`
committed side-by-side in the same `assets/` dir, provenance row naming the
`.drawio` as source, and the rule that GitHub-facing flow/DAG stays inline
mermaid (diffable) while article-bound architecture is drawio+PNG. Plus the
evidence-layer path fix from §C. The next-ai-drawio MCP plugin is live
(Node 22 installed 2026-08-31).

## F — Execution order & verification

Skill update → integration_test/release-tooling re-point (tests green) →
README + corp-doc deletion → docs alignment → articles index + Article 1 →
final sweep: dead-relative-link grep across all `.md`,
`uv run pytest -m "not gpu and not gcp"`, `uv run ruff check .`,
`uv run mypy packages/sdfb-core/src`. One branch (`doc-revisit`), one PR.
