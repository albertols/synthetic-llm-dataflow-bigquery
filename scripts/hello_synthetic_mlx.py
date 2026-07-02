#!/usr/bin/env python
"""Minimal M4 smoke test — real LLM, no Dataflow, no Docker, no Beam.

The point: validate that the full
    schema → reference rows → prompt → MLX → JSON → Pydantic → Pandera
chain works against a real LLM on M4 before burning Dataflow time.

What this script does:
  1. Loads the real `_ddl.json` you extracted via `scripts/extract_ddl.py`.
  2. Pulls a tiny reference sample (default 5 rows) via live BQ SELECT.
  3. Loads Gemma 4 E4B (or any other HF-layout model) via MLX.
  4. For each row of synthetic output requested, asks MLX to perturb a
     random reference anchor and emit one JSON record.
  5. Validates each candidate through the Pydantic-derived record model.
  6. Writes valid rows to `output/<table>/hello_synthetic_mlx.jsonl`.

What this script is NOT:
  - The production pipeline (that's `sdfb_beam.cli.run_pipeline` on Dataflow).
  - A benchmark (E4B on M4 is way slower than 26B-A4B MoE on L4).
  - A replacement for #11 — the only way to validate the full Beam DAG +
    GPU + vLLM is to ship the image to Dataflow.

REFs:
  - docs/M4_LOCAL_SMOKE.md      (runbook)
  - docs/MODEL_LAYOUT.md        (where MLX expects local weights)
  - docs/adr/0010-m4-local-smoke-mlx.md
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path

from sdfb_beam.handlers.mlx_client import MLXModelClient
from sdfb_beam.io.bq_sources import load_reference_rows
from sdfb_core.codegen import derive_record_model
from sdfb_core.contracts import TableSchema

logger = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="M4 MLX smoke test for synthetic-dataflow-bigquery")
    p.add_argument("--ddl_path", required=True,
                   help="Local path to _ddl.json (e.g. output/CDH_dataset/ddl_metadata_CDH_dataset_KW860T_RR.json)")
    p.add_argument("--reference_table", required=True,
                   help="BQ table FQN for reference SELECT, e.g. cdh_dataset.synthetic_data")
    p.add_argument("--reference_limit", type=int, default=5)
    p.add_argument("--num_rows", type=int, default=5,
                   help="How many synthetic rows to attempt generating")
    p.add_argument("--model_path", default="./models/gemma4/e4b-it/v1/",
                   help="Local path to MLX-loadable HF-layout model directory (use the -it variant)")
    p.add_argument("--output_dir", default="output",
                   help="Output JSONL goes to <output_dir>/<table>/hello_synthetic_mlx.jsonl")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max_tokens", type=int, default=4096,
                   help="Generation cap. 67-col records + any reasoning easily exceed 2048")
    p.add_argument("--temperature", type=float, default=0.2,
                   help="Low temp favors schema conformance for structured output")
    p.add_argument("--verbose", action="store_true",
                   help="DEBUG logging — surfaces the raw MLX text on parse failure")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        force=True,
    )
    rng = random.Random(args.seed)

    logger.info("Loading DDL from %s", args.ddl_path)
    schema = TableSchema.model_validate(json.loads(Path(args.ddl_path).read_text()))
    Record = derive_record_model(schema)
    record_schema = Record.model_json_schema()
    logger.info("Schema parsed: %s (%d columns)", schema.fqn, len(schema.columns))

    logger.info("Pulling %d reference rows from %s",
                args.reference_limit, args.reference_table)
    ref_rows = load_reference_rows(
        table=args.reference_table,
        limit=args.reference_limit,
    )
    if not ref_rows:
        logger.error("No reference rows returned — cannot prompt MLX without anchors.")
        return 1

    logger.info("Loading MLX model from %s", args.model_path)
    client = MLXModelClient(model_uri=args.model_path)
    client.setup()

    table_name = schema.fqn.split(".")[-1]
    out_path = Path(args.output_dir) / table_name / "hello_synthetic_mlx.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    valid_count = 0
    parse_fail_count = 0
    validation_fail_count = 0
    run_start = time.perf_counter()
    with out_path.open("w") as fout:
        for i in range(args.num_rows):
            row_start = time.perf_counter()
            anchor = ref_rows[rng.randrange(len(ref_rows))]
            prompt = _build_prompt(anchor, schema.fqn)
            raw = client.generate_json(
                prompt, record_schema, n=1,
                max_tokens=args.max_tokens, temperature=args.temperature,
            )
            row_secs = time.perf_counter() - row_start
            if not raw:
                parse_fail_count += 1
                logger.warning("Row %d: ✗ no JSON parsed (%.1fs)", i, row_secs)
                continue
            candidate = raw[0]
            try:
                validated = Record.model_validate(candidate)
                fout.write(json.dumps(validated.model_dump(mode="json"), default=str) + "\n")
                valid_count += 1
                logger.info("Row %d: ✓ valid (%.1fs)", i, row_secs)
            except Exception as e:
                validation_fail_count += 1
                logger.warning("Row %d: ✗ Pydantic rejected (%.1fs) — %s", i, row_secs, e)
                logger.debug("Rejected payload: %s", candidate)

    total_secs = time.perf_counter() - run_start
    attempted = args.num_rows
    logger.info("=" * 60)
    logger.info(
        "Done. %d/%d valid | %d parse-fail | %d schema-reject",
        valid_count, attempted, parse_fail_count, validation_fail_count,
    )
    logger.info(
        "Throughput: %.0fs total, %.1fs/row avg (%d cols/record)",
        total_secs, total_secs / attempted if attempted else 0.0, len(schema.columns),
    )
    logger.info("Output: %s", out_path)
    logger.info("=" * 60)
    return 0


def _build_prompt(anchor: dict, table_fqn: str) -> str:
    """Compose a perturb-this-anchor prompt for the LLM."""
    return (
        f"You are generating a single synthetic row for the BigQuery table "
        f"`{table_fqn}`. Use the example row below as a stylistic anchor, but "
        f"PRODUCE A NEW row with realistic but DIFFERENT values. Keep the same "
        f"types and rough distribution; vary names, IDs, dates within plausible "
        f"ranges.\n\n"
        f"Anchor row (for reference only — do not copy verbatim):\n"
        f"{json.dumps(anchor, default=str, indent=2)}"
    )


if __name__ == "__main__":
    sys.exit(main())
