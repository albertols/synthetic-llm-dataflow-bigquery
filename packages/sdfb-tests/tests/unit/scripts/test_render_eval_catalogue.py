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
"""Unit tests for `scripts/doc/render_eval_catalogue.py`.

The evaluation design document carries the metric catalogue as a table. That
table is generated from the evaluator's catalogue YAML, between two markers,
so the document cannot say something the code does not: the script rewrites
the block, and `--check` (a CI step) fails when the two differ.

The catalogue belongs to `packages/sdfb-evaluation`, a standalone project
that is not part of every copy of this repository. Without it there is
nothing to render, and the whole module skips.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).parents[5]
_CATALOGUE = (
    _ROOT / "packages" / "sdfb-evaluation" / "src" / "sdfb_evaluation" /
    "catalogue" / "metrics.yaml")
if not _CATALOGUE.is_file():
  pytest.skip(
      "the evaluator's metric catalogue is not in this tree",
      allow_module_level=True)

_SCRIPT = _ROOT / "scripts" / "doc" / "render_eval_catalogue.py"
_spec = importlib.util.spec_from_file_location("doc_eval_catalogue", _SCRIPT)
render = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = render
_spec.loader.exec_module(render)

_START, _END = "<!-- eval-catalogue:start -->", "<!-- eval-catalogue:end -->"


def _metric(metric_id: str, **over) -> dict:
  level = metric_id.split(".", maxsplit=1)[0]
  entry = {
      "id": metric_id,
      "version": "1",
      "title": f"Title of {metric_id}",
      "level": level,
      "family": "fidelity",
      "kinds": ["numeric", "temporal"],
      "value_kind": "distance",
      "estimator": "binned",
      "direction": "lower_better",
      "target": None,
      "range": [0, 1],
      "thresholds": {
          "warn": 0.1,
          "fail": 0.2,
          "source": "heuristic"
      },
      "score": "linear",
      "noise_floor": "ks_two_sample",
      "n_dependent": False,
      "baseline": True,
      "uses_ci_bound": False,
      "formula": r"D = \max_i \lvert F(e_i) - G(e_i) \rvert",
      "purpose": "p",
      "interpretation": {
          "good": "g",
          "bad": "b"
      },
      "pitfalls": "x",
      "references": [],
  }
  entry.update(over)
  return entry


def _catalogue(*metrics: dict) -> dict:
  return {
      "catalogue_version": "9.9.9",
      "levels": ["field", "column", "row"],
      "families": ["fidelity", "privacy", "overall"],
      "metrics": list(metrics),
  }


_SMALL = _catalogue(
    _metric("column.ks"),
    _metric(
        "row.memorization_lift",
        family="privacy",
        kinds=[],
        direction="lower_better",
        target=1.0,
        thresholds={
            "warn": 2,
            "fail": 5,
            "source": "heuristic"
        },
        noise_floor="rate_ratio",
        n_dependent=True,
        baseline=False,
        uses_ci_bound=True),
    _metric(
        "field.adherence",
        direction="higher_better",
        thresholds={
            "warn": 0.99,
            "fail": 0.95,
            "source": "heuristic"
        }),
)


def _write(tmp_path: Path, catalogue: dict, body: str) -> tuple[Path, Path]:
  source = tmp_path / "metrics.yaml"
  source.write_text(yaml.safe_dump(catalogue), encoding="utf-8")
  doc = tmp_path / "design.md"
  doc.write_text(body, encoding="utf-8")
  return source, doc


def _run(source: Path, doc: Path, *flags: str) -> int:
  return render.main([*flags, "--catalogue", str(source), "--doc", str(doc)])


def test_the_block_names_the_version_and_counts_every_metric():
  block = render.render_block(_SMALL)
  assert "catalogue `9.9.9`" in block
  assert "3 metrics" in block
  for metric_id in ("column.ks", "row.memorization_lift", "field.adherence"):
    assert block.count(f"`{metric_id}`") == 1


def test_levels_follow_the_catalogues_own_order():
  block = render.render_block(_SMALL)
  order = [
      block.index(f"#### {level} ") for level in ("field", "column", "row")
  ]
  assert order == sorted(order)
  # The level's table follows its heading: a metric sits under its own level.
  assert block.index("#### column ") < block.index("`column.ks`") < block.index(
      "#### row ")


def test_a_row_carries_the_gate_exactly_as_the_catalogue_states_it():
  row = next(
      line for line in render.render_block(_SMALL).split("\n")
      if "`row.memorization_lift`" in line)
  cells = [cell.strip() for cell in row.strip("|").split("|")]
  assert cells[0] == ("`row.memorization_lift`<br/>"
                      "Title of row.memorization_lift")
  assert cells[1] == "privacy"
  assert cells[2] == "—"  # no column kinds above the column level
  assert cells[4:] == [
      "binned", "lower_better", "1", "2", "5", "linear", "rate_ratio",
      "matched n, CI bound"
  ]


def test_the_formula_is_inline_maths_that_markdown_cannot_reformat():
  row = next(
      line for line in render.render_block(_SMALL).split("\n")
      if "`column.ks`" in line)
  assert r"$`D = \max_i \lvert F(e_i) - G(e_i) \rvert`$" in row


def test_a_null_gate_and_a_data_dependent_target_are_spelled_out():
  catalogue = _catalogue(
      _metric(
          "column.info_only",
          thresholds={
              "warn": None,
              "fail": None,
              "source": "none"
          },
          noise_floor="none",
          baseline=False),
      _metric("column.novelty", direction="target", target=None))
  lines = render.render_block(catalogue).split("\n")
  info = next(line for line in lines if "`column.info_only`" in line)
  novelty = next(line for line in lines if "`column.novelty`" in line)
  assert "| — | — | — | linear | — | — |" in info
  assert "| target | source value | 0.1 | 0.2 |" in novelty


def test_small_thresholds_keep_their_magnitude():
  catalogue = _catalogue(
      _metric(
          "column.rare",
          thresholds={
              "warn": 1.0e-5,
              "fail": 1.0e-4,
              "source": "heuristic"
          }))
  assert "| 1e-5 | 0.0001 |" in render.render_block(catalogue)


def test_a_pipe_or_a_newline_cannot_break_a_table_row():
  catalogue = _catalogue(_metric("column.odd", title="a | b\nc"))
  row = next(
      line for line in render.render_block(catalogue).split("\n")
      if "`column.odd`" in line)
  assert r"a \| b c" in row


def test_the_count_table_adds_up_per_level_and_family():
  block = render.render_block(_SMALL)
  assert "| level | fidelity | privacy | overall | total |" in block
  assert "| field | 1 | — | — | 1 |" in block
  assert "| row | — | 1 | — | 1 |" in block
  assert "| **total** | 2 | 1 | — | 3 |" in block


def test_a_metric_outside_the_declared_levels_is_refused():
  with pytest.raises(ValueError, match=r"table\.orphaned"):
    render.render_block(_catalogue(_metric("table.orphaned")))


def test_rewrite_touches_only_what_lies_between_the_markers(tmp_path):
  body = f"# Title\n\nbefore\n\n{_START}\nstale\n{_END}\n\nafter\n"
  source, doc = _write(tmp_path, _SMALL, body)
  assert _run(source, doc) == 0
  text = doc.read_text(encoding="utf-8")
  assert text.startswith(f"# Title\n\nbefore\n\n{_START}\n")
  assert text.endswith(f"\n{_END}\n\nafter\n")
  assert "stale" not in text
  assert "`column.ks`" in text


def test_rewrite_is_idempotent(tmp_path):
  source, doc = _write(tmp_path, _SMALL, f"x\n{_START}\n{_END}\ny\n")
  assert _run(source, doc) == 0
  once = doc.read_text(encoding="utf-8")
  assert _run(source, doc) == 0
  assert doc.read_text(encoding="utf-8") == once


def test_check_is_0_in_sync_and_1_on_drift_and_writes_nothing(tmp_path, capsys):
  source, doc = _write(tmp_path, _SMALL, f"x\n{_START}\nstale\n{_END}\ny\n")
  before = doc.read_text(encoding="utf-8")
  assert _run(source, doc, "--check") == 1
  assert doc.read_text(encoding="utf-8") == before
  assert "render_eval_catalogue.py" in capsys.readouterr().err
  assert _run(source, doc) == 0
  assert _run(source, doc, "--check") == 0


def test_check_sees_a_catalogue_edit(tmp_path):
  source, doc = _write(tmp_path, _SMALL, f"{_START}\n{_END}\n")
  assert _run(source, doc) == 0
  edited = _catalogue(*_SMALL["metrics"], _metric("column.added"))
  source.write_text(yaml.safe_dump(edited), encoding="utf-8")
  assert _run(source, doc, "--check") == 1


@pytest.mark.parametrize("body", [
    "no markers at all\n",
    f"{_START}\nonly a start\n",
    f"{_END}\nreversed\n{_START}\n",
    f"{_START}\n{_END}\n{_START}\n{_END}\n",
])
def test_a_document_without_exactly_one_block_is_an_error(
    tmp_path, body, capsys):
  source, doc = _write(tmp_path, _SMALL, body)
  assert _run(source, doc, "--check") == 2
  assert _run(source, doc) == 2
  assert doc.read_text(encoding="utf-8") == body
  assert "eval-catalogue" in capsys.readouterr().err


def test_a_missing_catalogue_is_an_error_not_a_pass(tmp_path, capsys):
  _, doc = _write(tmp_path, _SMALL, f"{_START}\n{_END}\n")
  assert _run(tmp_path / "absent.yaml", doc, "--check") == 2
  assert "absent.yaml" in capsys.readouterr().err


def test_the_renderer_never_imports_the_evaluator():
  # The root environment stays independent of the evaluator's project: the
  # YAML is read directly.
  tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
  imported = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      imported.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
      imported.add((node.module or "").split(".")[0])
  assert "yaml" in imported
  assert not imported & {"sdfb_evaluation", "sdfb_core", "sdfb_beam"}


def test_the_real_catalogue_renders_every_metric_once():
  catalogue = yaml.safe_load(_CATALOGUE.read_text(encoding="utf-8"))
  block = render.render_block(catalogue)
  ids = [metric["id"] for metric in catalogue["metrics"]]
  for metric_id in ids:
    assert block.count(f"| `{metric_id}`<br/>") == 1, metric_id
  assert f"{len(ids)} metrics" in block


def test_the_design_document_is_in_sync_with_the_catalogue():
  assert render.main(["--check"]) == 0
