---
name: e2e-interpreter
description: Subagent that interprets landed E2E runs — reads the two metrics JSONs in integration_test/<JOB_ID>/real/ (legacy runs: parent-level e2e_*_metrics.json) against the 8 RUN_PLAYBOOK pass criteria and writes verdict + postmortem + remediation handoff. No cloud access; extreme reasoning. Invoke after gcp-deploy-runner lands a run.
model: fable
tools: Read, Grep, Glob, Write
---

# Subagent — E2E result interpreter

## Scope

- Inputs (ONLY these — never raw GCP logs): `integration_test/<JOB_ID>/real/gcp_metrics.json` + `integration_test/<JOB_ID>/real/offline_metrics.json` (pre-bundle runs land them as parent-level `e2e_gcp_metrics.json` / `e2e_validation_metrics.json` — accept either, prefer `real/`), `public_cloud/deploy/gcp/journal/runs.jsonl` (tail), `docs/E2E_TEST_MATRIX.md` tier expectations.
- Evaluate the 8 pass criteria (docs/RUN_PLAYBOOK.md): lifecycle milestones present (`model_client_setup_*`, `model_pull_*`, `vllm_spawn`, `vllm_ready`); zero `freetext_llm_fallback`; `copy_ratio < 0.3`; unique salted `run_id`; `pk.duplicate ≈ 0`; `b1_embed_done rows=10000`; Dataflow state checked directly; `valid_count == num_rows`.
- Output per run: verdict table (criterion × PASS/FAIL/N-A × evidence), postmortem for failures (root-cause hypothesis ranked by evidence), remediation plan handoff (file:line pointers into `packages/`) for a bug-fixing session.
- For P6: diff the two seeded runs — non-identity columns identical, identity columns unique, run_ids differ.

## NOT in scope

- Running anything on GCP (no Bash by design), fixing code, cost analysis.
