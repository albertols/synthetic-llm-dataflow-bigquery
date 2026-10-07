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


def _schema_vocabulary(table: str, *path: str) -> list[str]:
  """A field's closed vocabulary, from its description in the evaluator's
  BigQuery schema (`a | b | c`): the evaluator's own second statement of it."""
  fields = json.loads(
      (_REPO / "packages" / "sdfb-evaluation" / "src" / "sdfb_evaluation" /
       "schemas" / f"{table}.schema.json").read_text(encoding="utf-8"))
  for name in path:
    field = next(f for f in fields if f["name"] == name)
    fields = field.get("fields", [])
  return [part.split()[0] for part in field["description"].split(" | ")]


def test_evaluation_channel_is_the_evaluator_cli(knobs_module, knobs):
  evaluation = [k for k in knobs["knobs"] if k["channel"] == "evaluation"]
  ids = {k["id"] for k in evaluation}
  assert {
      "eval_mode", "eval_sample_rows", "eval_privacy_sample_rows",
      "eval_detection_sample_rows", "eval_pair_max_columns",
      "eval_row_flags_top_k", "eval_row_flags_source_keys",
      "eval_max_bytes_billed", "eval_max_shuffle_gb", "eval_scope",
      "eval_allow_contaminated"
  } == ids
  cli = knobs_module.EVAL_CLI.relative_to(_REPO).as_posix()
  for knob in evaluation:
    assert knob["source"].rsplit(":", 1)[0] == cli, knob["id"]
    assert knob["help"], knob["id"]
    assert knob["flex_param"] == knob["cli_flag"][2:], knob["id"]
  by_id = _by_id(knobs)
  assert by_id["eval_max_bytes_billed"]["value"] == 1_099_511_627_776
  # No argparse default: the runner decides (the help text says how).
  assert by_id["eval_mode"]["value"] is None
  assert by_id["eval_mode"]["choices"] == _schema_vocabulary(
      "evaluation_data_history", "mode")
  # `choices=("auto", *SCOPE_MODES)`: a name imported from another module.
  assert by_id["eval_scope"]["choices"] == [
      "auto",
      *_schema_vocabulary("evaluation_data_history", "tables", "scope_mode")
  ]
  assert by_id["eval_scope"]["help"].endswith("(default auto)")
  # Raw source keys are never written: one choice, and no flag to change it.
  assert by_id["eval_row_flags_source_keys"]["choices"] == ["hashed"]


def test_evaluation_channel_reads_every_way_the_cli_declares_a_flag(
    knobs_module, tmp_path):
  declared = {
      "mode", "scope", "sample_rows", "pair_max_columns", "allow_contaminated"
  }
  rest = [
      name for name, _, _ in knobs_module._EVAL_FLAGS if name not in declared
  ]
  cli = tmp_path / "main.py"
  cli.write_text(
      "import argparse\n"
      "_MODES = ('table', 'manual')\n"
      "_COUNT_FLAGS = (\n"
      "    ('--sample_rows', 123456, 'rows per side'),\n"
      "    ('--pair_max_columns', 7, 'columns'),\n"
      ")\n"
      "def _boolean(parser, name, help_text):\n"
      "  parser.add_argument(name, nargs='?', const=True, default=False)\n"
      "def build():\n"
      "  p = argparse.ArgumentParser()\n"
      "  p.add_argument(\n"
      "      '--mode', choices=('exact', 'sampled'), help='every row, or '\n"
      "      'a sample')\n"
      "  p.add_argument('--scope', choices=('auto', *_MODES), default='auto',\n"
      "                 help='rows in scope (default %(default)s)')\n"
      "  _boolean(p, '--allow_contaminated', 'evaluate it anyway')\n"
      "  for name, default, help_text in _COUNT_FLAGS:\n"
      "    p.add_argument(name, type=int, default=default, help=help_text)\n" +
      "".join(f"  p.add_argument('--{name}', default=1)\n" for name in rest) +
      "  return p\n",
      encoding="utf-8")
  entries = {k["id"]: k for k in knobs_module.eval_knobs(cli)}
  assert set(entries) == {
      f"eval_{name}" for name, _, _ in knobs_module._EVAL_FLAGS
  }
  mode = entries["eval_mode"]
  assert (mode["value"], mode["choices"]) == (None, ["exact", "sampled"])
  assert mode["help"] == "every row, or a sample"
  # The line that holds the flag, not the line the call starts on.
  assert mode["source"].endswith("main.py:12")
  scope = entries["eval_scope"]
  assert scope["choices"] == ["auto", "table", "manual"]
  assert scope["help"] == "rows in scope (default auto)"
  rows = entries["eval_sample_rows"]
  assert (rows["value"], rows["help"]) == (123456, "rows per side")
  assert rows["source"].endswith("main.py:4")
  contaminated = entries["eval_allow_contaminated"]
  assert contaminated["value"] is False
  assert contaminated["help"] == "evaluate it anyway"
  assert contaminated["source"].endswith("main.py:16")


def test_a_flag_the_evaluator_dropped_fails_the_export(knobs_module, tmp_path):
  cli = tmp_path / "main.py"
  cli.write_text(
      "import argparse\n"
      "p = argparse.ArgumentParser()\n" +
      "".join(f"p.add_argument('--{name}', default=1)\n"
              for name, _, _ in knobs_module._EVAL_FLAGS[1:]),
      encoding="utf-8")
  with pytest.raises(LookupError, match="--mode is not a flag"):
    knobs_module.eval_knobs(cli)


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


# The committed files are checked by .github/workflows/gui.yml (`--check`,
# tolerant of line shifts), not here: an unrelated Python PR that moves a
# line must not fail this suite.


def test_check_ignores_line_shifts_but_not_values(knobs_module, tmp_path):
  assert knobs_module.main(["--out-dir", str(tmp_path)]) == 0
  assert knobs_module.main(["--check", "--out-dir", str(tmp_path)]) == 0
  path = tmp_path / "knobs.json"
  doc = json.loads(path.read_text(encoding="utf-8"))
  knob = doc["knobs"][0]
  file, line = knob["source"].rsplit(":", 1)
  knob["source"] = f"{file}:{int(line) + 7}"
  doc["exported_from"] = {"commit": "0" * 40, "dirty": []}
  path.write_text(json.dumps(doc), encoding="utf-8")
  assert knobs_module.main(["--check", "--out-dir", str(tmp_path)]) == 0
  assert knobs_module.main(["--check-strict", "--out-dir", str(tmp_path)]) == 1
  knob["source_token"] = "moved elsewhere"
  path.write_text(json.dumps(doc), encoding="utf-8")
  assert knobs_module.main(["--check", "--out-dir", str(tmp_path)]) == 1


def test_check_fails_on_value_drift_in_every_file(knobs_module, tmp_path):
  for name, tamper in (
      ("knobs.json", lambda d: d["knobs"][0].update(value="tampered")),
      ("relationships.json",
       lambda d: d["models"][0]["tables"][0].update(pk=["tampered"])),
      ("dlq_rules.json", lambda d: d["rules"][0].update(severity="INFO")),
  ):
    assert knobs_module.main(["--out-dir", str(tmp_path)]) == 0
    path = tmp_path / name
    doc = json.loads(path.read_text(encoding="utf-8"))
    tamper(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert knobs_module.main(["--check", "--out-dir", str(tmp_path)]) == 1, name


def test_exports_record_the_commit_their_links_resolve_at(knobs_module):
  docs = knobs_module.build_all()
  stamps = {json.dumps(doc["exported_from"]) for doc in docs.values()}
  assert len(stamps) == 1
  stamp = docs["knobs.json"]["exported_from"]
  assert stamp["commit"] is None or len(stamp["commit"]) == 40
  assert isinstance(stamp["dirty"], list)


def test_relationship_export_is_the_registry_view(knobs_module):
  from sdfb_core.contracts.relationships import RelationshipRegistry

  doc = knobs_module.build_relationships()
  by_model = {m["model"]: m for m in doc["models"]}
  thelook = by_model["gcp_public_thelook"]
  assert thelook["source"] == "config/relationships/gcp_public_fk_example.yaml"
  assert thelook["generation_order"] == ["users", "orders", "order_items"]
  path = _REPO / thelook["source"]
  registry = RelationshipRegistry.from_sources([
      (thelook["source"], path.read_text(encoding="utf-8"))
  ])
  assert thelook["sha12"] == registry.sha12()
  items = {t["name"]: t for t in thelook["tables"]}["order_items"]
  roles = {(tuple(e["cols"]), e["ref"]): e["role"] for e in items["fk"]}
  assert roles == {
      (("order_id", "user_id"), "orders"): "driving",
      (("user_id",), "users"): "implied",
      (("product_id",), "synthetic_data.products"): "external",
  }
  # Only committed sample models: real ones are gitignored.
  assert all("example" in Path(m["source"]).stem for m in doc["models"])


def test_dlq_rule_export_matches_the_code(knobs_module):
  from sdfb_core.validation import dlq
  from sdfb_core.validation.summary import BLOCKER_RULE_IDS

  rules = {r["rule_id"]: r for r in knobs_module.build_dlq_rules()["rules"]}
  expected = {
      "schema.types": ("pydantic", "ValidateRecordDoFn"),
      "schema.batch": ("pandera", "PanderaValidateBatchDoFn"),
      "schema.non_finite": ("load_safety", None),
      "row.duplicate": ("uniqueness", "EnforceUniqueness"),
      "pk.duplicate": ("uniqueness", "EnforceUniqueness"),
      "identity.unique": ("uniqueness", "EnforceUniqueness"),
      "fk.orphan": ("referential_integrity", "EnforceFkIntegrityDoFn"),
      "fk.unmatched": ("referential_integrity", "GenerateRecordsDoFn"),
      "engine_failure": ("engine", "GenerateRecordsDoFn"),
  }
  emitted = {
      k: (r["error_type"], r["pipeline_step"])
      for k, r in rules.items()
      if r["emitted"]
  }
  assert emitted == expected
  for rule_id, (error_type, step) in expected.items():
    normalized = dlq.normalize_dlq_record(
        {
            "error_type": error_type,
            "rule_id": rule_id
        }, run_id="r")
    assert (normalized["pipeline_step"] or None) == step, rule_id
    assert _line_of(rules[rule_id]["emitted_by"]).strip(), rule_id
  assert rules["fk.unmatched"]["stage"] == "pre_generate"
  # Declared as a BLOCKER, counted by the gate, never emitted by a DoFn.
  assert rules["null.required"]["declared"]
  assert not rules["null.required"]["emitted"]
  assert {r for r, v in rules.items() if v["counted_in_blocker_gate"]
         } == set(BLOCKER_RULE_IDS)


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


def test_compare_golden_holds_the_evaluators_own_verdicts(golden_module):
  import math

  doc = golden_module.build()["compare"]
  render = sys.modules["sdfb_evaluation.report.render"]
  catalogue = render.load_catalogue()
  known = set(catalogue.ids())
  decode = {"+inf": math.inf, "-inf": -math.inf, "nan": math.nan}

  def row(side: dict) -> dict:
    return {
        key: value if key == "status" else decode.get(value, value)
        for key, value in side.items()
    }

  # Each pair alone through the function `compare` judges a pair with.
  for case in doc["cases"]:
    metric_id = case["metric_id"]
    entry = render._delta(
        ("users", metric_id, case["id"], None, None), row(case["a"]),
        row(case["b"]),
        catalogue.get(metric_id) if metric_id in known else None)
    assert (entry["verdict"], entry["noise"]) == (case["python"]["verdict"],
                                                  case["python"]["noise"]), case
  assert {c["python"]["verdict"] for c in doc["cases"]} == set(doc["verdicts"])
  # Judged against both floors, the intervals, nothing, or not judged at all.
  assert {(c["python"]["noise"] or "none").split()[0] for c in doc["cases"]
         } == {"floor", "CI", "no", "none"}


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
