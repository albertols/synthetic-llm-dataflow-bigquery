#!/usr/bin/env python
"""Predeployment preflight — verify every prerequisite in `docs/DEPLOYMENT_PREREQUISITES.md`.

Read-only + local-artifact only: **no DataflowRunner, no `bq mk`, no bucket
creation**. It produces the deployable schema files, checks that everything the
pipeline reads already exists, and writes a compact OK / ACTION report with
clickable paths. Nothing in GCP is mutated.

The ordered checklist below is the SINGLE source of truth — it is mirrored 1:1
in `docs/DEPLOYMENT_PREREQUISITES.md` → "Preflight checklist". Keep them in sync.

  1. Source DDL      — run extract_ddl.py → `_ddl.json`; write source BQ schema to
                       config/bq_schema/{source_dataset}/{table}.schema.json
  2. Landing schema  — run derive_landing_schema.py →
                       config/bq_schema/synthetic_data/{table}.schema.json (mirrors source)
  3. Local weights   — required model (+ embedder) files present under ./models/
  4. BigQuery tables — source, landing, dlq, validation_runs exist
  5. Staging bucket  — …-dataflow-staging exists
  6. Templates bucket— …-dataflow-templates exists
  7. BigQuery datasets — synthetic_data, synthetic_data_quality exist
  8. Others          — weights staged in GCS · _ddl.json staged in GCS · local config artifacts

Exit code: 0 when no ACTION items (KO=0), 1 when any ACTION (KO). SKIP (could not
verify — offline / no creds / missing lib) never fails the run but is surfaced.

Usage:
    python scripts/deployment_prerequisites.py \\
        --project my-proj --source-table my-proj.raw.customers \\
        --model-uri gs://my-proj-models/synthetic/models/gemma4/e4b-it/v1/ \\
        --staging-bucket my-proj-dataflow-staging \\
        --templates-bucket my-proj-dataflow-templates \\
        --ddl-uri gs://my-proj-dataflow/ddl/customers_ddl.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sdfb_core.codegen.derive_bq_ddl import derive_bq_schema
from sdfb_core.contracts.schema import TableSchema

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent

OK, ACTION, SKIP = "OK", "ACTION", "SKIP"
_BADGE = {OK: "✅ OK", ACTION: "⚠️ ACTION", SKIP: "⏭️ SKIP"}

# Minimum on-disk files per docs/MODEL_LAYOUT.md (file-level checklist).
# NOTE: special_tokens_map.json is NOT universally shipped — gemma/bge include
# it, Qwen (HF + ModelScope) does not (special tokens live in
# tokenizer_config.json) — so it is only required for embedders. LLMs instead
# need SOME loadable tokenizer asset (see _TOKENIZER_ANY / _TOKENIZER_BPE_PAIR).
LLM_REQUIRED = ["config.json", "tokenizer_config.json"]
_TOKENIZER_ANY = ("tokenizer.json", "tokenizer.model")
_TOKENIZER_BPE_PAIR = ("vocab.json", "merges.txt")
EMBEDDER_REQUIRED = [
    "config.json", "model.safetensors", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json",
]


@dataclass
class Result:
    step: str            # "1", "4a", … — the checklist position
    name: str
    status: str
    resource: str = ""   # markdown (clickable link(s) / short detail)
    action: str = ""     # remediation, shown in the Actions section


@dataclass
class Ctx:
    args: argparse.Namespace
    results: list[Result] = field(default_factory=list)
    ddl_json_path: Path | None = None
    table_schema: TableSchema | None = None

    def add(self, *a, **k) -> None:
        self.results.append(Result(*a, **k))


# --------------------------------------------------------------------------- #
# link helpers — every resource is a clickable path
# --------------------------------------------------------------------------- #
def flink(p: Path) -> str:
    ap = p.resolve()
    return f"[`{ap}`](file://{ap})"


def short(reason: str, limit: int = 90) -> str:
    """First line of an exception string, capped — SKIP reasons stay compact."""
    line = reason.splitlines()[0]
    return line if len(line) <= limit else line[: limit - 1] + "…"


def bucket_of(uri_or_name: str) -> str:
    return urlsplit(uri_or_name).netloc if uri_or_name.startswith("gs://") else uri_or_name


def prefix_of(uri: str) -> str:
    return urlsplit(uri).path.lstrip("/") if uri.startswith("gs://") else ""


def gcs_link(uri_or_name: str, project: str) -> str:
    bucket = bucket_of(uri_or_name)
    prefix = prefix_of(uri_or_name)
    tgt = f"{bucket}/{prefix}" if prefix else bucket
    url = f"https://console.cloud.google.com/storage/browser/{tgt}?project={project}"
    label = uri_or_name if uri_or_name.startswith("gs://") else f"gs://{bucket}"
    return f"[`{label}`]({url})"


def bq_table_link(fqn: str) -> str:
    proj, dataset, table = fqn.split(".", 2)
    url = (f"https://console.cloud.google.com/bigquery?project={proj}"
           f"&p={proj}&d={dataset}&t={table}&page=table")
    return f"[`{fqn}`]({url})"


def bq_dataset_link(project: str, dataset: str) -> str:
    url = (f"https://console.cloud.google.com/bigquery?project={project}"
           f"&p={project}&d={dataset}&page=dataset")
    return f"[`{project}.{dataset}`]({url})"


# --------------------------------------------------------------------------- #
# lazy GCP clients — return (client, None) or (None, reason) so callers SKIP
# --------------------------------------------------------------------------- #
def bq_client(project: str):
    try:
        from google.cloud import bigquery
        return bigquery.Client(project=project), None
    except Exception as e:
        return None, short(f"{type(e).__name__}: {e}")


def gcs_client():
    try:
        from google.cloud import storage
        return storage.Client(), None
    except Exception as e:
        return None, short(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def step1_source_ddl(ctx: Ctx) -> None:
    a = ctx.args
    proj, dataset, table = a.source_table.split(".", 2)
    out_base = REPO_ROOT / "output"
    expected = out_base / dataset / f"ddl_metadata_{dataset}_{table}.json"

    ddl_path = Path(a.ddl_json) if a.ddl_json else expected
    if a.no_extract or ddl_path.exists():
        if not ddl_path.exists():
            ctx.add("1", "Source DDL (_ddl.json)", ACTION,
                    f"missing {flink(ddl_path)}",
                    "run without --no-extract, or pass --ddl-json to an existing file")
            return
    else:
        cmd = [sys.executable, str(SCRIPTS_DIR / "extract_ddl.py"),
               "--project", proj, "--dataset", dataset, "--table", table,
               "--runner", "DirectRunner", "--output_base", str(out_base),
               "--timeout", str(a.timeout)]
        rc = subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode
        if rc != 0 or not expected.exists():
            ctx.add("1", "Source DDL (_ddl.json)", ACTION,
                    f"extract_ddl.py exited {rc}; expected {flink(expected)}",
                    "check BigQuery creds / connectivity, then re-run")
            return
        ddl_path = expected

    ctx.ddl_json_path = ddl_path
    ctx.table_schema = TableSchema.model_validate(json.loads(ddl_path.read_text()))

    schema_file = Path(a.schemas_dir) / dataset / f"{table}.schema.json"
    schema_file.parent.mkdir(parents=True, exist_ok=True)
    schema_file.write_text(json.dumps(derive_bq_schema(ctx.table_schema)["fields"], indent=2) + "\n")
    ctx.add("1", "Source DDL + BQ schema", OK,
            f"{flink(ddl_path)} · {flink(schema_file)}")


def step2_landing_schema(ctx: Ctx) -> None:
    a = ctx.args
    if ctx.ddl_json_path is None:
        ctx.add("2", "Landing schema", ACTION, "no _ddl.json (step 1 failed)",
                "resolve step 1 first")
        return
    _, ld_dataset, ld_table = a.landing_table.split(".", 2)
    landing_file = Path(a.schemas_dir) / ld_dataset / f"{ld_table}.schema.json"
    landing_file.parent.mkdir(parents=True, exist_ok=True)
    mod = _load_sibling("derive_landing_schema")
    rc = mod.main([str(ctx.ddl_json_path), "-o", str(landing_file)])
    if rc != 0 or not landing_file.exists():
        ctx.add("2", "Landing schema", ACTION, f"derive exited {rc}",
                "run scripts/derive_landing_schema.py manually")
        return
    ctx.add("2", "Landing schema (mirrors source)", OK, flink(landing_file))


def step3_local_weights(ctx: Ctx) -> None:
    a = ctx.args
    _local_model(ctx, "3a", "LLM weights", a.model_uri, LLM_REQUIRED, need_safetensors=True)
    _local_model(ctx, "3b", "Embedder weights", a.embedder_uri, EMBEDDER_REQUIRED,
                 need_safetensors=False, optional=True)


def step4_bq_tables(ctx: Ctx) -> None:
    a = ctx.args
    client, reason = bq_client(a.project)
    tables = [("4a", "source", a.source_table), ("4b", "landing", a.landing_table),
              ("4c", "dlq", a.dlq_table), ("4d", "validation_runs", a.validation_runs_table)]
    if client is None:
        for step, label, fqn in tables:
            ctx.add(step, f"BQ table · {label}", SKIP, f"{bq_table_link(fqn)} — {reason}")
        return
    from google.api_core.exceptions import NotFound
    for step, label, fqn in tables:
        try:
            client.get_table(fqn)
            ctx.add(step, f"BQ table · {label}", OK, bq_table_link(fqn))
        except NotFound:
            ctx.add(step, f"BQ table · {label}", ACTION, f"{bq_table_link(fqn)} — not found",
                    _table_action(label, fqn))
        except Exception as e:
            ctx.add(step, f"BQ table · {label}", SKIP,
                    f"{bq_table_link(fqn)} — {short(f'{type(e).__name__}: {e}')}")


def step5_staging_bucket(ctx: Ctx) -> None:
    _gcs_bucket(ctx, "5", "Staging bucket", ctx.args.staging_bucket, ctx.args.project)


def step6_templates_bucket(ctx: Ctx) -> None:
    _gcs_bucket(ctx, "6", "Templates bucket", ctx.args.templates_bucket, ctx.args.project)


def step7_bq_datasets(ctx: Ctx) -> None:
    a = ctx.args
    # The datasets WE own (landing/dlq/validation) — source dataset is covered by 4a.
    datasets = []
    for fqn in (a.landing_table, a.dlq_table, a.validation_runs_table):
        proj, ds, _ = fqn.split(".", 2)
        if (proj, ds) not in datasets:
            datasets.append((proj, ds))
    client, reason = bq_client(a.project)
    from_notfound = None
    if client is not None:
        from google.api_core.exceptions import NotFound
        from_notfound = NotFound
    for i, (proj, ds) in enumerate(datasets):
        step = f"7{chr(ord('a') + i)}"
        link = bq_dataset_link(proj, ds)
        if client is None:
            ctx.add(step, f"BQ dataset · {ds}", SKIP, f"{link} — {reason}")
            continue
        try:
            client.get_dataset(f"{proj}.{ds}")
            ctx.add(step, f"BQ dataset · {ds}", OK, link)
        except from_notfound:
            ctx.add(step, f"BQ dataset · {ds}", ACTION, f"{link} — not found",
                    f"create dataset `{ds}` in the pipeline region before the tables")
        except Exception as e:
            ctx.add(step, f"BQ dataset · {ds}", SKIP, f"{link} — {short(f'{type(e).__name__}: {e}')}")


def step8_others(ctx: Ctx) -> None:
    a = ctx.args
    # 8a — weights actually staged in GCS.
    _gcs_prefix(ctx, "8a", "Weights staged in GCS", a.model_uri, a.project,
                "upload ./models/… to the model URI (see docs/MODEL_LAYOUT.md)")
    # 8b — _ddl.json staged in GCS (the launcher's --ddl_uri).
    if a.ddl_uri:
        _gcs_object(ctx, "8b", "_ddl.json staged in GCS", a.ddl_uri, a.project,
                    "upload the extracted _ddl.json to --ddl-uri")
    else:
        ctx.add("8b", "_ddl.json staged in GCS", SKIP, "no --ddl-uri given")
    # 8c — committed local config artifacts.
    missing = [f for f in (
        Path(a.schemas_dir) / "synthetic_data_quality" / "dlq.schema.json",
        Path(a.schemas_dir) / "synthetic_data_quality" / "validation_runs.schema.json",
        REPO_ROOT / "config" / "thresholds.yml",
    ) if not f.exists()]
    if missing:
        ctx.add("8c", "Local config artifacts", ACTION,
                " · ".join(flink(m) for m in missing) + " missing",
                "restore the committed config files")
    else:
        ctx.add("8c", "Local config artifacts", OK,
                "thresholds.yml · synthetic_data_quality/dlq.schema.json · "
                "synthetic_data_quality/validation_runs.schema.json")


# --------------------------------------------------------------------------- #
# shared check primitives
# --------------------------------------------------------------------------- #
def _local_model(ctx, step, label, uri, required, *, need_safetensors, optional=False):
    if not uri:
        ctx.add(step, label, SKIP, f"no {'--embedder-uri' if optional else '--model-uri'} given")
        return
    subpath = prefix_of(uri).split("synthetic/models/", 1)[-1].rstrip("/")
    mdir = Path(ctx.args.models_dir) / subpath
    if not mdir.is_dir():
        ctx.add(step, label, ACTION, f"missing dir {flink(mdir)}",
                f"stage weights locally under {mdir} (docs/MODEL_LAYOUT.md)")
        return
    missing = [f for f in required if not (mdir / f).exists()]
    safet = list(mdir.glob("*.safetensors"))
    if need_safetensors and not safet:
        missing.append("*.safetensors")
    if need_safetensors:  # LLM: any loadable tokenizer asset (family-agnostic)
        has_tokenizer = any((mdir / f).exists() for f in _TOKENIZER_ANY) or all(
            (mdir / f).exists() for f in _TOKENIZER_BPE_PAIR
        )
        if not has_tokenizer:
            missing.append("tokenizer.json|tokenizer.model|vocab.json+merges.txt")
    if len(safet) > 1 and not (mdir / "model.safetensors.index.json").exists():
        missing.append("model.safetensors.index.json")
    if missing:
        ctx.add(step, label, ACTION, f"{flink(mdir)} — missing: {', '.join(missing)}",
                "download the full file set (docs/MODEL_LAYOUT.md checklist)")
    else:
        ctx.add(step, label, OK, f"{flink(mdir)} — {len(safet)} safetensors")


def _gcs_bucket(ctx, step, label, name, project):
    if not name:
        ctx.add(step, label, SKIP, f"no --{label.split()[0].lower()}-bucket given")
        return
    client, reason = gcs_client()
    link = gcs_link(name, project)
    if client is None:
        ctx.add(step, label, SKIP, f"{link} — {reason}")
        return
    try:
        exists = client.lookup_bucket(bucket_of(name)) is not None
        ctx.add(step, label, OK if exists else ACTION,
                link if exists else f"{link} — not found",
                "" if exists else f"create the {label.lower()} in the pipeline region")
    except Exception as e:
        ctx.add(step, label, SKIP, f"{link} — {short(f'{type(e).__name__}: {e}')}")


def _gcs_prefix(ctx, step, label, uri, project, action):
    if not uri:
        ctx.add(step, label, SKIP, "no --model-uri given")
        return
    client, reason = gcs_client()
    link = gcs_link(uri, project)
    if client is None:
        ctx.add(step, label, SKIP, f"{link} — {reason}")
        return
    try:
        blob = next(iter(client.list_blobs(bucket_of(uri), prefix=prefix_of(uri), max_results=1)), None)
        ctx.add(step, label, OK if blob else ACTION,
                link if blob else f"{link} — no objects", "" if blob else action)
    except Exception as e:
        ctx.add(step, label, SKIP, f"{link} — {short(f'{type(e).__name__}: {e}')}")


def _gcs_object(ctx, step, label, uri, project, action):
    client, reason = gcs_client()
    link = gcs_link(uri, project)
    if client is None:
        ctx.add(step, label, SKIP, f"{link} — {reason}")
        return
    try:
        exists = client.bucket(bucket_of(uri)).blob(prefix_of(uri)).exists()
        ctx.add(step, label, OK if exists else ACTION,
                link if exists else f"{link} — not found", "" if exists else action)
    except Exception as e:
        ctx.add(step, label, SKIP, f"{link} — {short(f'{type(e).__name__}: {e}')}")


def _table_action(label: str, fqn: str) -> str:
    if label == "source":
        return "point --source-table at an existing table"
    _, dataset, table = fqn.split(".", 2)
    path = f"config/bq_schema/{dataset}/{table}.schema.json"
    if label == "landing":
        return f"create it from {path} (mirrors source; DEPLOYMENT_PREREQUISITES.md)"
    return f"create it from {path}"


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def build_report(ctx: Ctx, stamp: datetime) -> str:
    a = ctx.args
    n_ok = sum(r.status == OK for r in ctx.results)
    n_act = sum(r.status == ACTION for r in ctx.results)
    n_skip = sum(r.status == SKIP for r in ctx.results)
    verdict = "KO — actions required" if n_act else "OK — ready to deploy"

    lines = [
        f"# Deployment prerequisites — `{a.project}`",
        "",
        f"_Generated {stamp:%Y-%m-%d %H:%M} UTC · source `{a.source_table}`_",
        "",
        f"**{n_ok} OK · {n_act} ACTION · {n_skip} SKIP → {verdict}**",
        "",
        "| # | Check | Status | Resource |",
        "|---|-------|--------|----------|",
    ]
    lines += [f"| {r.step} | {r.name} | {_BADGE[r.status]} | {r.resource} |" for r in ctx.results]

    actions = [r for r in ctx.results if r.status == ACTION]
    if actions:
        lines += ["", "## Actions needed", ""]
        lines += [f"- **[{r.step}] {r.name}** — {r.action or r.resource}" for r in actions]

    skips = [r for r in ctx.results if r.status == SKIP]
    if skips:
        lines += ["", "## Not verified (offline / no creds / not provided)", ""]
        lines += [f"- **[{r.step}] {r.name}** — {r.resource}" for r in skips]

    lines += ["", "---", "_Checklist mirrors `docs/DEPLOYMENT_PREREQUISITES.md` → Preflight checklist._", ""]
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="GCP project (billing + console links).")
    p.add_argument("--source-table", required=True, help="project.dataset.table of the reference table.")
    p.add_argument("--landing-table", default=None,
                   help="Default {project}.synthetic_data.{source table name}")
    p.add_argument("--dlq-table", default=None, help="Default {project}.synthetic_data_quality.dlq")
    p.add_argument("--validation-runs-table", default=None,
                   help="Default {project}.synthetic_data_quality.validation_runs")
    p.add_argument("--model-uri", default="", help="gs://…/synthetic/models/{family}/{model}/{version}/")
    p.add_argument("--embedder-uri", default="", help="gs://…/synthetic/models/embedders/{model}/{version}/")
    p.add_argument("--staging-bucket", default="", help="name or gs:// of the dataflow-staging bucket")
    p.add_argument("--templates-bucket", default="", help="name or gs:// of the dataflow-templates bucket")
    p.add_argument("--ddl-uri", default="", help="gs:// path where the _ddl.json must be staged (launcher --ddl_uri)")
    p.add_argument("--models-dir", default=str(REPO_ROOT / "models"), help="Local weights root (default ./models).")
    p.add_argument("--schemas-dir", default=str(REPO_ROOT / "config" / "bq_schema"),
                   help="Root under which step 1/2 drop {dataset}/{table}.schema.json "
                        "(default config/bq_schema).")
    p.add_argument("--report-dir", default=str(REPO_ROOT / "output"), help="Report output dir (default ./output).")
    p.add_argument("--ddl-json", default="", help="Reuse an existing _ddl.json instead of extracting.")
    p.add_argument("--no-extract", action="store_true", help="Skip running extract_ddl.py (reuse existing).")
    p.add_argument("--timeout", type=float, default=50.0, help="BigQuery timeout (s).")
    args = p.parse_args(argv)
    # Landing defaults to the source table's *name* in synthetic_data (mirrors
    # the DDL table) — override with --landing-table (launcher's landing_table).
    source_table_name = args.source_table.split(".")[-1]
    args.landing_table = args.landing_table or f"{args.project}.synthetic_data.{source_table_name}"
    args.dlq_table = args.dlq_table or f"{args.project}.synthetic_data_quality.dlq"
    args.validation_runs_table = (args.validation_runs_table
                                  or f"{args.project}.synthetic_data_quality.validation_runs")
    return args


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore", message=".*quota project.*")
    args = parse_args(argv)
    ctx = Ctx(args=args)
    for step in (step1_source_ddl, step2_landing_schema, step3_local_weights, step4_bq_tables,
                 step5_staging_bucket, step6_templates_bucket, step7_bq_datasets, step8_others):
        step(ctx)

    stamp = datetime.now(UTC)
    report = build_report(ctx, stamp)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"deployment_prerequisites_{stamp:%Y_%m_%d_%H_%M}.md"
    report_path.write_text(report)

    n_act = sum(r.status == ACTION for r in ctx.results)
    n_skip = sum(r.status == SKIP for r in ctx.results)
    n_ok = len(ctx.results) - n_act - n_skip
    print(f"{n_ok} OK · {n_act} ACTION · {n_skip} SKIP → {'KO' if n_act else 'OK'}")
    print(f"report: file://{report_path}")
    return 1 if n_act else 0


if __name__ == "__main__":
    sys.exit(main())
