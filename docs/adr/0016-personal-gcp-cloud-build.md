# 0016 — Personal-project images build on Cloud Build (ADR 0008 carve-out)

Date: 2026-07-14
Status: Accepted

## Context

ADR 0008 makes GitHub CI the only sanctioned image builder — its rationale is
corporate (provenance/ITSK, JFrog cache, ARC runners). A personal GCP project
(`public_cloud/deploy/gcp/`, spec `docs/superpowers/specs/2026-07-14-personal-gcp-e2e-design.md`)
now runs the T4 E2E matrix outside the corporate LZ. The dev laptop has no
Docker (Intel Mac, macOS 12), the image is ~15–20 GB, and corporate CI cannot
push to a personal Artifact Registry.

## Decision

For the **personal path only**, `gcloud builds submit` (Cloud Build) is the
sanctioned builder (`public_cloud/deploy/gcp/06_build_image.sh` +
`cloudbuild/build_image.yaml`). The mainline `docker/Dockerfile` is reused
byte-identical; the single JFrog dependency (the pip-install-uv `--index-url`
bootstrap) is retargeted to pypi.org by a `sed` in the **ephemeral Cloud Build
workspace** with a fail-loud grep — never committed. `uv.lock` already pins
public `files.pythonhosted.org` URLs, so dependency resolution is unchanged.
Local `docker build`/`push` remains forbidden on both paths.

## Consequences

- Corporate rule unchanged; zero mainline edits (drift-guard test
  `test_tiers_matrix.py` protects the template contract instead).
- If the Dockerfile bootstrap line changes upstream, the personal build fails
  loudly at the grep, pointing at the sed to update.
- Personal images live only in the personal GAR repo (keep-newest-1 policy).
- Two accepted deviations from the corporate spec, scoped to the personal
  path only: `run.googleapis.com` + `eventarc.googleapis.com` are enabled
  (`01_bootstrap_project.sh`) because the gen2 `billing-killswitch` Cloud
  Function requires them; and `BUILD_SA` holds project-level
  `roles/storage.objectAdmin` (`02_iam.sh`) rather than a bucket-scoped grant,
  because Cloud Build's auto-created source-staging bucket doesn't exist yet
  when IAM is provisioned.
