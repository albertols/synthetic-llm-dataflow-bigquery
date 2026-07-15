---
name: gcp-e2e-run
description: Recipe for running personal-GCP E2E tiers (submit → poll → collect → report). Load before executing any run-matrix campaign or a single tier on the personal project.
---

# Skill — personal GCP E2E runs

## Single tier

```bash
cd public_cloud/deploy/gcp
./run_e2e.sh <tier> <table>     # tiers: S0 R1p R2p R3p N4 P6 P7; tables: citibike hacker_news
```

- run_e2e.sh polls to terminal state, journals to `journal/runs.jsonl`, enforces
  EXPECT semantics (N4 must FAIL — a green N4 job is a broken guard).
- T4 preset is baked in tiers.yaml: qwen3-4b + `vllm_dtype=float16` +
  `vllm_max_model_len=8192` + n1-standard-8 + `install-nvidia-driver:5xx`.

## Campaign (spec §Run matrix)

S0 → R1p → R2p → R3p → N4 → P6 ×2 → P7 on citibike, then R1p on hacker_news.
Stop the campaign at the first unexpected FAIL; hand the landed artifacts to
`e2e-interpreter` before continuing.

## Collect + report (after every landed job; RUN_PLAYBOOK §5)

```bash
JOB=<job_id>; RUN=<run_id>; T=citibike_trips_50k
uv run --no-sync python3 scripts/e2e_gcp_probe.py --project $PROJECT_ID \
  --source-fqn $PROJECT_ID.synthetic_source.$T --landing-fqn $PROJECT_ID.synthetic_data.$T \
  --quality-dataset synthetic_data_quality --region us-central1 \
  --job-id $JOB --run-id $RUN --pk trip_id --out integration_test/$JOB/e2e_gcp_metrics.json
uv run --no-sync python3 scripts/e2e_validation_analysis.py --csv eng=integration_test/$JOB/<export>.csv \
  --schema output/${T}_landing_schema.json --pk trip_id --batch-size 16 \
  --out integration_test/$JOB/e2e_validation_metrics.json
uv run --no-sync python3 scripts/e2e_bundle_export.py --metrics gcp=integration_test/$JOB/e2e_gcp_metrics.json \
  --metrics validation=integration_test/$JOB/e2e_validation_metrics.json --out-root integration_test/$JOB --job-id $JOB
```

(hacker_news: `--pk id`, table `hacker_news_50k`.)

## Token-efficiency rules

- Poll: `gcloud dataflow jobs describe <id> --region us-central1 --format 'value(currentState)'` — nothing broader.
- Never read raw worker logs; the probe script mines them into JSON.
