# Deployment prerequisites — GCS & BigQuery

What must exist **before** a pipeline run, and what to provision per environment. Aimed at both the current db.com deployment and a future generic/OSS adaptation.

The pipeline is **bring-your-own-infra**: every table and bucket is passed in by URI/FQN, and all BigQuery sinks are `CREATE_NEVER` (`sdfb_beam/cli/run_pipeline.py`). Nothing is auto-created at run time — a missing table or bucket fails the job.

Companion docs (don't restate — link):
- **Model weights** — GCS layout, download, file checklist → [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md)
- **Build / deploy / run** — image, Flex Template, DAG import, secrets → [`CICD.md`](CICD.md)
- **Dev env setup** → [`M4_SETUP.md`](M4_SETUP.md)
- **Run playbook** — GPU verdict, run matrix, Dataflow options, L4 capacity strategy, report recipe → [`RUN_PLAYBOOK.md`](RUN_PLAYBOOK.md)

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

All sinks are `FILE_LOADS` + `WRITE_APPEND` + `CREATE_NEVER`, so the three destination tables **must be pre-created with exact schemas** — Beam does **not** auto-create them; a missing table fails the load job rather than being created (deliberate: keeps schema authorship out of the pipeline and fails fast on a typo'd FQN).

| Table | FQN pattern | Schema source | Provision |
|---|---|---|---|
| **Reference / source** | `project.dataset.table` (`--reference_table`) | *pre-existing* | The table you clone. Read `SELECT * … LIMIT N` (`sdfb_beam/io/bq_sources.py`). Read access only. |
| **Landing** | `project.synthetic_data.<source table>` (defaults to the DDL table name; override with `landing_table`) | **derived from the target DDL** | `sdfb_core/codegen/derive_bq_ddl.py` turns the `_ddl.json` into a BQ `TableSchema` → `bq mk`. **No committed schema file** — it's per-target (the preflight writes `config/bq_schema/synthetic_data/<table>.schema.json`). |
| **DLQ** | `project.synthetic_data_quality.dlq` | `config/bq_schema/synthetic_data_quality/dlq.schema.json` | DAY-partition on `dlq_inserted_at`. |
| **validation_runs** | `project.synthetic_data_quality.validation_runs` | `config/bq_schema/synthetic_data_quality/validation_runs.schema.json` | DAY-partition on `created_at`. Optional (empty FQN skips the write) but recommended. |
| **rag_chunks** (WS2) | `project.synthetic_rag.rag_chunks` | `config/bq_schema/synthetic_rag/rag_chunks.schema.json` | DAY-partition on `created_at`. **One shared store for the whole project**: chunks from *every* source `dataset.table` coexist, scoped by `source_fqn` and pinned to a vector space by (`embedder_id`, `embedder_version`) — adding a new source table needs **no** new RAG table. Optional (only needed for `--build_rag_layer` / b1 chunk reuse). |

Datasets to create: **`synthetic_data`** (landing), **`synthetic_data_quality`** (dlq + validation_runs), and — for the WS2 RAG layer — **`synthetic_rag`** (rag_chunks), in the reference data's region (`europe-west3` here). Schema files are laid out by dataset under `config/bq_schema/<dataset>/<table>.schema.json`. The committed-schema tables map 1:1 to JSON files:

```bash
bq mk --schema config/bq_schema/synthetic_data_quality/dlq.schema.json \
      --time_partitioning_field dlq_inserted_at \
      project:synthetic_data_quality.dlq
bq mk --schema config/bq_schema/synthetic_data_quality/validation_runs.schema.json \
      --time_partitioning_field created_at \
      project:synthetic_data_quality.validation_runs
bq mk --schema config/bq_schema/synthetic_rag/rag_chunks.schema.json \
      --time_partitioning_field created_at \
      project:synthetic_rag.rag_chunks
```

After the **first** `--build_rag_layer` population run (BigQuery requires ≥5 000 rows before an index can be created), add the vector index — retrieval falls back to brute-force COSINE until then:

```sql
CREATE VECTOR INDEX rag_chunks_embedding_idx
ON `project.synthetic_rag.rag_chunks`(embedding)
OPTIONS(index_type = 'IVF', distance_type = 'COSINE');
```

Planned retrieval-side optimizations (reranking, metadata filtering) build on this same contract — `source_fqn` scoping, the embedder pin, and the vector index — so treat the `rag_chunks` schema as an API: additive changes only.

#### Provisioning the landing table (step by step)

The **landing** table has no committed schema — it mirrors the target you're cloning, so you derive it from that table's `_ddl.json`. The goal is a reusable **`landing_ddl.json`** (a BQ JSON schema array) that feeds either `bq mk` or Terraform.

**1. Extract the target DDL** (skip if you already have it from step 4 of the bootstrap):

```bash
uv run python scripts/extract_ddl.py \
    --project "$(gcloud config get-value project)" \
    --dataset <source_dataset> --table <source_table> \
    --runner DirectRunner
# → ./output/<source_dataset>/ddl_metadata_<source_dataset>_<source_table>.json
```

**2. Derive `landing_ddl.json`** with `scripts/derive_landing_schema.py`. It unwraps `derive_bq_schema()`'s `{"fields": [...]}` to the **bare array** that `bq` and Terraform want, and prints partitioning/clustering (which live in the DDL, not the schema JSON). Pass `--print-bq` to also emit a ready-to-run `bq mk`:

```bash
DDL_JSON=./output/<source_dataset>/ddl_metadata_<source_dataset>_<source_table>.json

uv run python scripts/derive_landing_schema.py "$DDL_JSON" \
    -o landing_ddl.json \
    --print-bq project:synthetic_data.<source_table>   # landing mirrors the source table name
```

For the `customers` fixture this prints `partitioning: DAY signup_at`, `clustering: country,tier`, a matching `bq mk` command, and writes a `landing_ddl.json` like:

```json
[
  {"name": "customer_id", "type": "INT64", "mode": "REQUIRED", "description": "Surrogate customer key."},
  {"name": "email", "type": "STRING", "mode": "REQUIRED", "description": "Customer email; unique.", "maxLength": 255},
  {"name": "signup_at", "type": "TIMESTAMP", "mode": "REQUIRED", "description": "Account creation timestamp (UTC)."},
  {"name": "country", "type": "STRING", "mode": "NULLABLE", "description": "ISO-3166-1 alpha-2.", "maxLength": 2},
  {"name": "tier", "type": "STRING", "mode": "REQUIRED", "description": "Subscription tier.", "maxLength": 16},
  {"name": "lifetime_value", "type": "NUMERIC", "mode": "NULLABLE", "precision": 18, "scale": 2}
]
```

**3a. Create with `bq mk`** — either run the command `--print-bq` emitted in step 2, or by hand (omit the partition/cluster flags if the DDL had none):

```bash
bq mk --table \
    --schema landing_ddl.json \
    --time_partitioning_type DAY --time_partitioning_field signup_at \
    --clustering_fields country,tier \
    project:synthetic_data.customers   # landing table = source table name (customers)
```

**3b. Or with Terraform** — `landing_ddl.json` drops straight into the `schema` argument:

```hcl
resource "google_bigquery_table" "landing" {
  project    = var.project
  dataset_id = "synthetic_data"
  table_id   = "customers"   # landing table = source table name
  schema     = file("${path.module}/landing_ddl.json")

  time_partitioning {           # from step 2's partitioning printout
    type  = "DAY"
    field = "signup_at"
  }
  clustering = ["country", "tier"]   # from step 2's clustering printout
}
```

The same `landing_ddl.json` also documents exactly what the pipeline will write — it's derived from the identical `_ddl.json` the engines and validators use.

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

## Preflight checklist — `scripts/deployment_prerequisites.py`

One local, **read-only** command audits everything above and prints an **OK / KO** report with clickable paths. It generates the schema files (checks 1–2) and *verifies* the rest — it never runs DataflowRunner, `bq mk`, or bucket creation. Anything missing is reported as an **ACTION** (do it via ① / ②); anything it can't reach offline/without creds is a **SKIP**.

```bash
uv run python scripts/deployment_prerequisites.py \
    --project my-proj --source-table my-proj.raw.customers \
    --model-uri gs://my-proj-models/synthetic/models/gemma4/e4b-it/v1/ \
    --staging-bucket my-proj-dataflow-staging \
    --templates-bucket my-proj-dataflow-templates \
    --ddl-uri gs://my-proj-dataflow/ddl/customers_ddl.json \
    --rag-chunks-table my-proj.synthetic_rag.rag_chunks
# → output/deployment_prerequisites_YYYY_MM_DD_HH_MM.md · exit 0 = OK, 1 = KO
# --rag-chunks-table defaults to {project}.synthetic_rag.rag_chunks; pass '' for a RAG-less deployment
```

**This ordered checklist is the single source of truth — it is mirrored 1:1 in the script's docstring. Keep them in sync.**

| # | Check | Tool does | Missing → action |
|---|-------|-----------|------------------|
| 1 | **Source DDL** — extract `_ddl.json`, write source BQ schema to `config/bq_schema/<source_dataset>/<table>.schema.json` | runs `extract_ddl.py` (unless `--no-extract`) | check BQ creds/connectivity |
| 2 | **Landing schema** — `config/bq_schema/synthetic_data/<table>.schema.json` (mirrors source) | runs `derive_landing_schema.py` | resolve #1 first |
| 3 | **Local weights** — required LLM (+ embedder) files under `./models/…` | verifies file checklist | download weights ([`MODEL_LAYOUT.md`](MODEL_LAYOUT.md)) |
| 4 | **BigQuery tables** — source, landing, dlq, validation_runs exist | verifies (read-only) | create them (① → tables) |
| 5 | **Staging bucket** — `…-dataflow-staging` exists | verifies | create bucket |
| 6 | **Templates bucket** — `…-dataflow-templates` exists | verifies | create bucket / deploy template ([`CICD.md`](CICD.md)) |
| 7 | **BigQuery datasets** — `synthetic_data`, `synthetic_data_quality` exist | verifies | create datasets (① → datasets) |
| 8 | **Others** — weights staged in GCS · `_ddl.json` staged in GCS (*optional* at launch since WS4 §6b — empty `--ddl_uri` live-extracts from INFORMATION_SCHEMA; an explicit URI pins/air-gaps) · local config artifacts present | verifies | upload weights / `_ddl.json`; restore config |
| 9 | **RAG chunk store** (WS2) — `synthetic_rag` dataset + `rag_chunks` table exist, multi-table contract columns + `created_at` DAY partition live, vector-index state | verifies (read-only; `--rag-chunks-table ''` skips) | create per ① → rag_chunks; index after first population |
| 10 | **Free-text pool store** (WS5/ADR 0020) — `freetext_pools` table contract | verifies (missing table = SKIP by design — pools rebuild per worker, slower not broken; `--freetext-pools-table ''` omits) | `bq mk` with `config/bq_schema/synthetic_rag/freetext_pools.schema.json`; drifted table = ACTION |
| 11 | **Source stats store** (WS8, 2026-08-05 spec WS-B + [ADR 0022](adr/0022-stats-driven-generation.md)) — `source_table_stats` table contract incl. the provenance columns `sample_rows`/`stats_tier`/`profiler_version` (versioned skip key; `distinct` means sample-bound at `stats_tier=sample`, full-table HLL at `exact`) | verifies (missing table = SKIP by design — stats still land as milestone + JSON artifact; `--source-stats-table ''` omits) | `bq mk` with `config/bq_schema/synthetic_rag/source_table_stats.schema.json`; drifted table = ACTION (write_rows load job would fail mid-launch); pre-ADR-0022 tables need the three columns added (`bq update` with the schema file — additive NULLABLE, no data rewrite) |
| 12 | **Relationship models** ([ADR 0032](adr/0032-relationships-as-config.md)) — `config/relationships/*.yaml` load, and every table they declare exists in the landing dataset | verifies (a broken model file = ACTION, since a launch reads the same loader and stops on it; a table that is absent but `enabled: false` is fine — it is detached on purpose; `--relationships-uri ''` omits) | fix the model file, or create the missing landing tables (`scripts/derive_landing_schema.py` + `bq mk`), or set `enabled: false` to detach them from the launch |

Create-order note: because #4/#7 are read-only checks, provision the create-side in dependency order — datasets → tables, buckets, weights (local → GCS), then IAM — using the `bq mk`/Terraform recipes in ①. Enterprise deploy (image, Flex Template, Composer Variables, DAG) is ② → [`CICD.md`](CICD.md).
