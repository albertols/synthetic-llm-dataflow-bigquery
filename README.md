# synthetic-dataflow-bigquery

Synthetic data generation for BigQuery via Apache Beam on Google Cloud Dataflow, powered by self-hosted open-weight LLMs (Gemma 4 family) running on Dataflow L4 GPU workers.

## What this does

Given a BigQuery DDL (`_ddl.json` produced by the included extraction job) and a live reference sample (`SELECT … LIMIT N` per Dataflow run), produce fictitious-but-realistic synthetic rows that conform to the original schema, constraints, and column-value distributions; write them back to BigQuery with in-pipeline validation (Mode A).

Two engines coexist behind one `GenerationEngine` interface:
- **B.1 — RAG**: embeds the reference sample, retrieves top-k exemplars per generation request, prompts Gemma 4 with schema + exemplars under JSON-schema-guided decoding.
- **B.2 — library-wrapper**: wraps a battle-tested tabular synthesis library (`sdgx` or DataDreamer — bake-off scheduled for M1 §6) and patches free-text columns via the LLM `ModelClient`.

## Status

Milestone 1 — in progress. See `CLAUDE.md` for the locked scope and `.claude/skills/` / `.claude/agents/` for the work breakdown.

## Workspace layout

```
packages/sdfb-core/    pure-Python contracts, codegen, engine ABC (laptop-installable)
packages/sdfb-beam/    Apache Beam pipeline + DoFns (Beam + GCP, optional GPU extras)
packages/sdfb-tests/   unit + DirectRunner integration tests
docker/                custom container for L4 GPU Dataflow workers (M1 §10)
scripts/               one-time M4 ops (model warm-pull, image build, job submit)
config/                models.yml, thresholds.yml — env-scoped knobs
```

## Local dev (laptop, no GPU, no GCP)

```bash
uv sync --group dev
uv run pytest -m "not gpu and not gcp"
uv run ruff check .
uv run mypy packages/sdfb-core/src
```

## Production run (on the M4 against GCP)

See `scripts/run_dataflow.sh` (deferred to M1 §10–§11 — needs the GPU container locked first).

## Hard constraints

- LLM serving lives **inside Beam DoFns** via `apache_beam.ml.inference.RunInference` only — no Vertex AI in the serving path.
- Model weights are pulled once into `gs://{bucket}/synthetic/models/` on the M4 — no HuggingFace Hub at runtime (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`).
- Validation Mode A (Pydantic + Pandera + whylogs) is in-pipeline; Mode B (GX / Soda / SDMetrics / Evidently) is M2.
- No Dataplex, no Looker Studio dashboards — validation outputs live in BigQuery `synthetic_data_quality.*` tables and GCS HTML/JSON only.

## License

Apache-2.0.
