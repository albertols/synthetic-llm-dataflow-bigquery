# Deployment prerequisites — GCS & BigQuery

What must exist **before** a pipeline run, and what to provision per environment. Aimed at both the current db.com deployment and a future generic/OSS adaptation.

The pipeline is **bring-your-own-infra**: every table and bucket is passed in by URI/FQN, and all BigQuery sinks are `CREATE_NEVER` (`sdfb_beam/cli/run_pipeline.py`). Nothing is auto-created at run time — a missing table or bucket fails the job.

Companion docs (don't restate — link):
- **Model weights** — GCS layout, download, file checklist → [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md)
- **Build / deploy / run** — image, Flex Template, DAG import, secrets → [`CICD.md`](CICD.md)
- **Dev env setup** → [`M4_SETUP.md`](M4_SETUP.md)

Two layers below: **① the portable core** (needed anywhere) and **② the enterprise wrapper** (db.com-specific — strip for OSS).

---

## ① Portable core

### GCS — buckets & objects

| What | Path | Read by | Notes |
|---|---|---|---|
| **LLM weights** | `gs://{bucket}/synthetic/models/{family}/{model}/{version}/` | worker `setup()` warm-pull (`sdfb_beam/gcs.py`) | Self-contained HF-layout dir. Immutable `vN/`. File checklist → [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md). |
| **Embedder weights** (B.1 RAG) | `gs://{bucket}/synthetic/models/embedders/{model}/{version}/` | B.1 DoFn `setup()` | Optional — empty `--embedder_uri` → dependency-free `HashingEmbedder`. |
| **DDL JSON** | `gs://…/…_ddl.json` (`--ddl_uri`) | driver `load_ddl()` | Produced by `scripts/extract_ddl.py`; stage to GCS for the launcher. |
| **Dataflow staging/temp** | `gs://…-dataflow-staging/{staging,temp}/` | Dataflow service | Can be one bucket with prefixes. |
| **Flex Template spec** | `gs://…-dataflow-templates/synthetic/sdfb-<ver>-template.json` | Flex launch | Built by CI (`CICD.md` §4). |

Minimum footprint: a **models** bucket + a **staging/temp** bucket (+ a **templates** bucket, or a prefix on staging for OSS).

### BigQuery — datasets & tables

All sinks are `FILE_LOADS` + `WRITE_APPEND` + `CREATE_NEVER`, so the three destination tables **must be pre-created with exact schemas**.

| Table | FQN pattern | Schema source | Provision |
|---|---|---|---|
| **Reference / source** | `project.dataset.table` (`--reference_table`) | *pre-existing* | The table you clone. Read `SELECT * … LIMIT N` (`sdfb_beam/io/bq_sources.py`). Read access only. |
| **Landing** | `project.synthetic_data.landing` | **derived from the target DDL** | `sdfb_core/codegen/derive_bq_ddl.py` turns the `_ddl.json` into a BQ `TableSchema` → `bq mk`. **No committed schema file** — it's per-target. |
| **DLQ** | `project.synthetic_data_quality.dlq` | `config/bq_schemas/dead_letter.schema.json` | DAY-partition on `dlq_inserted_at`. |
| **validation_runs** | `project.synthetic_data_quality.validation_runs` | `config/bq_schemas/validation_runs.schema.json` | DAY-partition on `created_at`. Optional (empty FQN skips the write) but recommended. |

Datasets to create: **`synthetic_data`** (landing) and **`synthetic_data_quality`** (dlq + validation_runs), in the reference data's region (`europe-west3` here). The two DQ tables map 1:1 to committed JSON schemas:

```bash
bq mk --schema config/bq_schemas/dead_letter.schema.json \
      --time_partitioning_field dlq_inserted_at \
      project:synthetic_data_quality.dlq
bq mk --schema config/bq_schemas/validation_runs.schema.json \
      --time_partitioning_field created_at \
      project:synthetic_data_quality.validation_runs
```

The **landing** table is the one gap: its schema is whatever the target DDL derives to. Intended flow: `extract_ddl.py` → `_ddl.json` → `derive_bq_ddl.py` → `TableSchema` → `bq mk`. A generic OSS build should script this into a one-shot "provision from DDL" step.

### Config artifacts (required at run time)

- `config/thresholds.yml` — `--thresholds_uri` (gs:// or local); missing → permissive gate.
- `config/models.yml` — registry/reference only; the run takes a full `--model_uri`, not a key.

### IAM — Dataflow worker SA

- `bigquery.dataEditor` + `bigquery.jobUser` — landing/dlq/validation writes + reference `SELECT`.
- `storage.objectViewer` on the models bucket; `storage.objectAdmin` on staging/temp.

---

## ② Enterprise wrapper (strip for OSS)

db.com-specific; the bulk of what an OSS adaptation removes or genericizes:

- **JFrog** base images + pip mirror (ADR 0003, `pyproject.toml [tool.uv.index]`) → Docker Hub / gcr.io + pypi.org.
- **WIF + GSM secrets** for CI auth and worker image pulls (`CICD.md` §6) → SA key or ADC. Add `secretmanager.secretAccessor` to the worker SA only here.
- **Composer Variables** `PROJECT_ID`, `REGION`, `DATAFLOW_SUBNET`, `SA_DATAFLOW`, `SDFB_MODEL_URI` (`composer/synthetic_beam_bigquery.py`) → pass as DAG params / env.
- **Network tags, `WORKER_IP_PRIVATE` + Private Google Access, KMS keys, secure-boot** → removable for a public-IP OSS default.
- **L4-in-`europe-west3` capacity / reservation logic** in the DAG (ADR 0004) → make region + accelerator plain params.

---

## Bootstrap order (once per environment)

Steps 1–6 are the portable core; step 7 is the wrapper.

1. **Buckets** — models, dataflow-staging (+ templates).
2. **Stage weights** — LLM → `…/synthetic/models/{family}/{model}/{version}/`; (optional) embedder → `…/embedders/…`. Procedure in [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md).
3. **Datasets** — `synthetic_data`, `synthetic_data_quality` (+ ensure the reference dataset exists).
4. **DDL** — `scripts/extract_ddl.py` → upload `_ddl.json` to GCS.
5. **Tables** — landing (from derived DDL), dlq + validation_runs (from the committed JSON schemas).
6. **IAM** — grant the Dataflow SA the roles above.
7. *(enterprise)* build image, deploy Flex Template, set Composer Variables, import DAG → [`CICD.md`](CICD.md).
