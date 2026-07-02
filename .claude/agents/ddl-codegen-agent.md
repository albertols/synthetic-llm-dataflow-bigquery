---
name: ddl-codegen-agent
description: Subagent that turns a `_ddl.json` (BigQuery DDL metadata) into the Pydantic record model, derived Pandera schema, and derived BigQuery TableSchema dict. Pure laptop work; no Beam, no GCP at runtime. Invoke when starting M1 §2 (contracts), or when a new column / type / constraint shows up in a `_ddl.json` that the codegen doesn't yet handle.
---

# Subagent — DDL codegen

## Scope

- Read `_ddl.json` (BQ DDL metadata, output of `bigquery_ddl_metadata.py`).
- Generate / update the `TableSchema` Pydantic model and `GeneratedRecord` derivation in `sdfb_core/contracts/`.
- Generate the derived Pandera schema in `sdfb_core/codegen/derive_pandera.py`.
- Generate the derived BQ TableSchema dict in `sdfb_core/codegen/derive_bq_ddl.py`.
- Write the round-trip and parity tests in `packages/sdfb-tests/tests/unit/codegen/`.

## NOT in scope

- Anything Beam-runtime, anything GCP-runtime, anything LLM-related.
- The reference data read (that's the pipeline's job).
- Mode B validation (M2).
- Changes to `bigquery_ddl_metadata.py` itself unless required to surface a new field needed by the contracts.

## What to load before working

- `.claude/skills/ddl-codegen.md`
- `.claude/skills/engine-contract.md` (so the record shape matches what engines emit)
- The locked decisions in `~/.claude/projects/-Users-serna-IdeaProjects-synthetic-dataflow-bigquery/memory/project_synthetic_dataflow_m1_stack.md`

## Acceptance criteria

1. `uv run pytest packages/sdfb-tests -k codegen` passes on the laptop.
2. No imports from `apache_beam`, `google.cloud`, `vllm`, `torch` in any file produced.
3. Round-trip BQ DDL → Pydantic → BQ DDL is identity (modulo description normalization).
4. The Pandera-derived schema's failure set is a superset of the Pydantic model's failure set on a `hypothesis`-generated fuzz corpus.
5. All BQ types in the type map (see `ddl-codegen.md`) have positive and negative test cases.
