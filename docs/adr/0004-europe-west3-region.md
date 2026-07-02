# ADR 0004 — europe-west3 (Frankfurt) as the M1 Dataflow region

- **Status**: accepted (2026-05-19)

## Context

L4 GPUs and `g2-standard-*` machine types are not available in every region. The user's BigQuery data sits in EU; the corporate networking egress proxy (`the-proxy:8080`) is tuned for EU egress to googleapis.com. Frankfurt is the closest GCP region with broad L4 availability and matches db.com's data-residency posture.

## Decision

M1 Dataflow jobs run in **`europe-west3`** with worker zone **`europe-west3-b`** by default. Both encoded in `.envrc` as `GCP_REGION` / `GCP_ZONE`; consumed by `scripts/probe_gpu_dataflow.sh` and (eventually) by the production pipeline launcher.

## Consequences

- **Enables**: same-region image pulls (once an AR fallback exists), same-region BQ reads, EU-data-residency alignment.
- **Costs**: ties the M1 deliverable to a single region. M3 will add at least `us-central1` parity (see `docs/ROADMAP.md`).
- **Forbids**: hardcoding the region into Python sources or Dockerfiles. All region-dependent flags come from env vars or argparse.

## Related

- `docs/GPU_CONTAINER.md` — "Locked decisions" section.
- L4 quota check: `gcloud compute project-info describe --flatten='quotas[]' | grep NVIDIA_L4_GPUS`.
