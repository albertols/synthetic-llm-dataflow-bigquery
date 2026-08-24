# scripts/ — scoped script layout

Every script lives under `scripts/<scope>/`. New scripts NEVER land at the
root. Current scopes:

| Scope | Owns |
|---|---|
| `doc/` | figure/report generators for documentation (`make_<topic>_figures.py`; see `.claude/skills/visual-first-documentation/SKILL.md`) |
| `e2e/` | integration-test analysis, GCP probes, sample fetch, crosschecks, redaction |
| `release/` | post-merge versioning + release reports (see `.github/workflows/release_tag_report.yaml`) |
| `relationships/` | reading/rendering `config/relationships/` models offline (`card.py` prints exactly what a launch would plan — ADR 0032) |

Future scopes (`ddl/`, `gcp/`, `automation/`, `config/`, …) are created on
first need. Root-level scripts (`extract_ddl.py`, `deployment_prerequisites.py`,
`derive_landing_schema.py`, `probe_gpu_dataflow.sh`, `hello_synthetic_mlx.py`,
`run_tableset.py`) are grandfathered and move to a scope when next touched.

Docs, prompts, agents and skills reference scripts by their scoped path.
Tests load scripts via importlib from `packages/sdfb-tests/tests/unit/scripts/`.
