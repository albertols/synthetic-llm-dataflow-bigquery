#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""`scripts/gui/export_{knobs,golden_fixtures}.py` — the GUI's Python truth.

The Synthetic Platform GUI (ADR 0042) never retypes a value from the Python
side: every knob is IMPORTED from code with its `path:line`, and the TS
ports of the hashing embedder, GReaT serialization and retrieval are pinned
against golden files these scripts write. These tests prove the exporters
read the code (not a copy of it), that every `source` points at the line it
claims, that the known docs-vs-code discrepancies are still true, and that
`--check` fails on drift.

Skipped when `gui/` or `scripts/gui/` is absent (the DSG copy ships
neither).
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=import-outside-toplevel,protected-access,redefined-outer-name

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parents[5]
_SCRIPTS = _REPO / "scripts" / "gui"

pytestmark = pytest.mark.skipif(
    not (_REPO / "gui").is_dir() or not _SCRIPTS.is_dir(),
    reason="the GUI workspace is not part of this tree",
)

_CHANNELS = {
    "sampling", "generation", "free_text", "rag", "guardrails", "relational",
    "serving", "evaluation"
}


def _load(name: str):
  spec = importlib.util.spec_from_file_location(f"gui_{name}",
                                                _SCRIPTS / f"{name}.py")
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def knobs_module():
  return _load("export_knobs")


@pytest.fixture(scope="module")
def knobs(knobs_module):
  return knobs_module.build()


@pytest.fixture(scope="module")
def golden_module():
  return _load("export_golden_fixtures")


def _by_id(doc: dict) -> dict[str, dict]:
  return {knob["id"]: knob for knob in doc["knobs"]}


def _line_of(source: str) -> str:
  path, line = source.rsplit(":", 1)
  return (_REPO / path).read_text(encoding="utf-8").splitlines()[int(line) - 1]


# ------------------------------------------------------------------ knobs --


def test_every_knob_is_complete_and_in_a_known_channel(knobs):
  assert {c["id"] for c in knobs["channels"]} == _CHANNELS
  seen = set()
  for knob in knobs["knobs"]:
    assert knob["id"] not in seen, knob["id"]
    seen.add(knob["id"])
    for key in ("id", "channel", "group", "label", "value", "unit",
                "settable_via", "source", "related_adrs", "docs"):
      assert key in knob, (knob["id"], key)
    assert knob["channel"] in _CHANNELS
    assert set(knob["settable_via"]) <= {
        "cli", "composer", "flex", "constant", "derived"
    }
  channels_used = {knob["channel"] for knob in knobs["knobs"]}
  assert channels_used == _CHANNELS


def test_values_are_imported_from_code_not_retyped(knobs):
  from sdfb_beam.cli import run_pipeline
  from sdfb_core.engines import generation_plan
  from sdfb_core.engines.b1_rag import engine as b1_engine
  from sdfb_core.rag import chunking
  from sdfb_core.rag.embedding import BgeEmbedder, HashingEmbedder
  from sdfb_core.stats import source_stats

  by_id = _by_id(knobs)
  assert by_id["max_row_doc_rows"]["value"] == chunking.MAX_ROW_DOC_ROWS
  assert (by_id["max_free_text_values_per_column"]["value"] ==
          chunking.MAX_FREE_TEXT_VALUES_PER_COLUMN)
  assert by_id["free_text_pool_max"][
      "value"] == generation_plan.FREE_TEXT_POOL_MAX
  assert by_id["rag_top_k"]["value"] == b1_engine._DEFAULT_TOP_K
  assert by_id["hashing_embedder_dim"]["value"] == HashingEmbedder().dim
  assert by_id["bge_embedder_dim"]["value"] == BgeEmbedder("/nowhere").dim
  assert by_id["profiler_version"]["value"] == source_stats.PROFILER_VERSION
  args, _ = run_pipeline.parse_args([
      "--reference_table", "p.d.s", "--landing_table", "p.d.l", "--dlq_table",
      "p.d.q", "--num_rows", "1", "--run_id", "r", "--model_uri", "gs://b/m/"
  ])
  assert by_id["reference_rows_limit"]["value"] == args.reference_rows_limit
  assert by_id["similarity"]["value"] == args.similarity
  assert by_id["uniqueness_mode"]["value"] == args.uniqueness_mode
  assert by_id["fk_candidate_cap"]["value"] == args.fk_candidate_cap
  assert by_id["blocker_failure_ratio_prd"]["value"] == 0.01
  assert by_id["temperature_ladder"]["value"] == [0.7, 1.0, 1.3]


def test_every_source_points_at_the_line_it_claims(knobs):
  for knob in knobs["knobs"]:
    source = knob["source"]
    if source == "planned":
      assert knob["channel"] == "evaluation", knob["id"]
      continue
    line = _line_of(source)
    needle = knob.get("source_token")
    assert needle and needle in line, (knob["id"], source, line)


def test_cli_knobs_carry_their_launch_surfaces(knobs):
  by_id = _by_id(knobs)
  similarity = by_id["similarity"]
  assert similarity["cli_flag"] == "--similarity"
  assert {"cli", "composer", "flex"} <= set(similarity["settable_via"])
  assert similarity["composer_param"] == "similarity"
  # A constant is never turnable.
  assert by_id["free_text_pool_max"]["settable_via"] == ["constant"]
  # Composer and CLI defaults can differ; both are reported.
  assert by_id["vllm_dtype"]["value"] == "auto"
  assert by_id["vllm_dtype"]["composer_default"] == "float16"


def test_evaluation_channel_is_planned_until_the_cli_exists(
    knobs_module, knobs):
  evaluation = [k for k in knobs["knobs"] if k["channel"] == "evaluation"]
  ids = {k["id"] for k in evaluation}
  assert {
      "eval_mode", "eval_sample_rows", "eval_privacy_sample_rows",
      "eval_detection_sample_rows", "eval_pair_max_columns",
      "eval_topk_profile", "eval_row_flags_top_k", "eval_row_flags_source_keys",
      "eval_max_bytes_billed", "eval_max_shuffle_gb", "eval_scope"
  } == ids
  by_id = _by_id(knobs)
  assert by_id["eval_max_bytes_billed"]["value"] == 1_099_511_627_776
  if not knobs_module.EVAL_CLI.exists():
    assert all(k["source"] == "planned" for k in evaluation)


def test_evaluation_channel_reads_the_cli_by_ast_when_present(
    knobs_module, tmp_path):
  cli = tmp_path / "main.py"
  cli.write_text(
      "import argparse\n"
      "def build():\n"
      "  p = argparse.ArgumentParser()\n"
      "  p.add_argument('--sample_rows', type=int, default=123456)\n"
      "  p.add_argument('--mode', default='sampled', choices=['exact', "
      "'sampled'])\n"
      "  return p\n",
      encoding="utf-8")
  entries = {k["id"]: k for k in knobs_module.eval_knobs(cli)}
  assert entries["eval_sample_rows"]["value"] == 123456
  assert entries["eval_sample_rows"]["source"].endswith("main.py:4")
  assert entries["eval_mode"]["choices"] == ["exact", "sampled"]
  assert entries["eval_pair_max_columns"]["source"] == "planned"


def test_docs_differ_annotations_are_verified_in_code(knobs):
  annotations = {a["id"]: a for a in knobs["annotations"]}
  assert {
      "similarity-semantics", "inverse-cdf-resolution", "bge-pooling",
      "fk-orphan-not-blocker"
  } <= set(annotations)
  known = {k["id"] for k in knobs["knobs"]}
  for annotation in annotations.values():
    assert set(annotation["knobs"]) <= known, annotation["id"]
    assert annotation["evidence"], annotation["id"]
    for evidence in annotation["evidence"]:
      assert evidence["excerpt"] in _line_of(evidence["source"]), evidence


def test_fk_orphan_annotation_tracks_the_code(knobs):
  from sdfb_core.validation.summary import BLOCKER_RULE_IDS

  ids = {a["id"] for a in knobs["annotations"]}
  assert ("fk-orphan-not-blocker" in ids) == ("fk.orphan"
                                              not in BLOCKER_RULE_IDS)


def test_measured_values_come_from_the_figure_scripts(knobs):
  measured = {m["id"]: m for m in knobs["measured"]}
  wall = measured["make_throughput_figures.WALL_MIN_COLD"]
  assert wall["value"] == 93.8
  assert "MEASURED" in wall["block"]
  assert "WALL_MIN_COLD" in _line_of(wall["source"])
  assert measured["make_ws5_figures.POOL_BUILD_LLM"]["value"] == 68_805.0
  assert measured["make_throughput_figures.FLEET_ROWS_PER_S_MEASURED"][
      "value"] == 10_500


def test_the_committed_knobs_file_is_current(knobs_module):
  assert knobs_module.main(["--check"]) == 0


def test_check_fails_on_drift(knobs_module, tmp_path):
  out = tmp_path / "knobs.json"
  assert knobs_module.main(["--out", str(out)]) == 0
  doc = json.loads(out.read_text(encoding="utf-8"))
  doc["knobs"][0]["value"] = "tampered"
  out.write_text(json.dumps(doc), encoding="utf-8")
  assert knobs_module.main(["--check", "--out", str(out)]) == 1


def test_no_float_in_the_file_can_trip_the_card_number_gate(knobs_module):
  text = knobs_module.render(knobs_module.build())
  assert not knobs_module.CARD_LIKE.search(text)


# ----------------------------------------------------------------- golden --


def test_hashing_golden_matches_the_python_embedder(golden_module):
  from sdfb_core.rag.embedding import HashingEmbedder

  doc = golden_module.build()["hashing_embedder"]
  texts = [case["text"] for case in doc["cases"]]
  assert len(texts) == 40
  joined = "".join(texts)
  for char in ("\x1c", "\x1d", "\x1e", "\x1f", "\x85", "\ufeff", "\t"):
    assert char in joined, repr(char)
  embedder = HashingEmbedder(dim=doc["dim"], seed=doc["seed"])
  for case, vector in zip(doc["cases"], embedder.embed(texts), strict=True):
    dense = [0.0] * doc["dim"]
    for index, value in case["nonzero"]:
      dense[index] = value
    assert max(abs(a - b) for a, b in zip(dense, vector, strict=True)) < 1e-9
    assert case["tokens"] == (case["text"].split() or [case["text"]])


def test_great_golden_matches_serialize(golden_module):
  from sdfb_core.rag.serialize import serialize_row

  doc = golden_module.build()["great_serialize"]
  assert len(doc["rows"]) == 10
  for case in doc["rows"]:
    row = golden_module.decode_row(case["row"])
    assert serialize_row(row, doc["column_order"]) == case["text"]


def test_retrieval_golden_matches_python_picks(golden_module):
  from sdfb_core.rag import retrieval

  doc = golden_module.build()["retrieval"]
  matrices = {
      name: golden_module.matrix(spec) for name, spec in doc["matrices"].items()
  }
  assert (len(matrices["main"]), len(matrices["main"][0])) == (64, 384)
  assert len({tuple(row) for row in matrices["collapsed"]}) == 3
  for case in doc["cases"]:
    vectors = matrices[case["matrix"]]
    items = list(range(len(vectors)))
    if case["strategy"] == "centroid":
      expected = golden_module.centroid_top_k(vectors, case["k"])
    else:
      start = ((case["attempt"] * case["k"]) %
               len(vectors) if case["strategy"] == "kcenter_rotate" else None)
      expected = retrieval.retrieve_kcenter_k(
          vectors, items, case["k"], start=start)
    assert case["picks"] == expected, case


def test_golden_check_detects_drift(golden_module, tmp_path):
  assert golden_module.main(["--out-dir", str(tmp_path)]) == 0
  assert golden_module.main(["--check", "--out-dir", str(tmp_path)]) == 0
  path = tmp_path / "retrieval.json"
  doc = json.loads(path.read_text(encoding="utf-8"))
  doc["cases"][0]["picks"] = [0]
  path.write_text(json.dumps(doc), encoding="utf-8")
  assert golden_module.main(["--check", "--out-dir", str(tmp_path)]) == 1


def test_the_committed_golden_files_are_current(golden_module):
  assert golden_module.main(["--check"]) == 0
