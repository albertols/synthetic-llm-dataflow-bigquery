#!/usr/bin/env python
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
"""Generate the two-sided golden parity fixture for `sdfb_evaluation`'s
relationship-model and reference-sample mirrors (D2).

`packages/sdfb-evaluation/` cannot import `sdfb_core`/`sdfb_beam` (the
package must stand alone), so it carries small hand-written mirrors of
`sdfb_core.contracts.relationships`, the reference-sample helpers in
`sdfb_beam.io` and the row-doc prefix size in `sdfb_core.rag.chunking`.
This script runs in the ROOT environment (it imports the originals) and
writes one fixture both sides pin against:
`packages/sdfb-evaluation/tests/fixtures/parity/goldens.json`. A root test
(`packages/sdfb-tests/tests/unit/evaluation_parity/test_goldens.py`)
recomputes the same values from the originals and an eval test
(`packages/sdfb-evaluation/tests/unit/test_parity_goldens.py`) recomputes
them from the mirrors — both assert equality with this file, so neither
side can drift from the other without a test failure.

    cd synthetic-llm-dataflow-bigquery
    UV_PROJECT_ENVIRONMENT=.venv-master uv run python \\
        scripts/evaluation/make_parity_goldens.py            # write
    UV_PROJECT_ENVIRONMENT=.venv-master uv run python \\
        scripts/evaluation/make_parity_goldens.py --check     # verify, no write

The output is deterministic: `json.dumps(..., sort_keys=True, indent=2)`
plus a trailing newline.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import date, datetime, UTC
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from sdfb_beam.io.bq_sources import load_reference_rows
from sdfb_beam.io.digest import compute_reference_digest
from sdfb_beam.io.relationships import is_sample_model
from sdfb_core.contracts.relationships import (
    RelationshipError,
    RelationshipRegistry,
)
from sdfb_core.rag.chunking import MAX_ROW_DOC_ROWS

_ROOT = Path(__file__).resolve().parents[2]
_MODEL_FILES = (
    "example_retail",
    "example_star_diamond",
    "gcp_public_fk_example",
)
_OUTPUT = (
    _ROOT / "packages" / "sdfb-evaluation" / "tests" / "fixtures" / "parity" /
    "goldens.json")

# A synthetic model (not a real file) built specifically to trigger
# `RelationshipRegistry`'s column-widening path (ADR 0036 rev 2): P's own
# edge to G only covers G1, but child C references the SAME column (CG)
# on both P (via P's other column PG2) and G (via G2) — the model asserts
# CG == P.PG2 == G.G2, so P's edge to G must be widened to carry
# (PG2 -> G2) too. None of the three committed sample models happens to
# exercise this path, so without this model the widening would go unpinned.
_INVENTED_WIDENING_SOURCE = "<invented>/widening.yaml"
_INVENTED_WIDENING_YAML = ("model: invented_widening\n"
                           "tables:\n"
                           "  G:\n"
                           "    pk: [G1, G2]\n"
                           "  P:\n"
                           "    pk: [PG1]\n"
                           "    fk:\n"
                           "      - cols: [PG1]\n"
                           "        ref: G\n"
                           "        ref_cols: [G1]\n"
                           "  C:\n"
                           "    pk: [CG]\n"
                           "    fk:\n"
                           "      - cols: [CG]\n"
                           "        ref: P\n"
                           "        ref_cols: [PG2]\n"
                           "        drives: true\n"
                           "      - cols: [CG]\n"
                           "        ref: G\n"
                           "        ref_cols: [G2]\n")

# 25 invented YAML texts (or source SETS, for the cross-file checks)
# pinning `parse_model`/`load_models` strictness against the originals'
# pydantic models and `RelationshipRegistry.from_sources` (R47).
_PARSE_CASES: tuple[tuple[str, list[tuple[str, str]]], ...] = (
    ("minimal_valid", [("a.yaml", "model: m\ntables:\n  A:\n    pk: [A1]\n")]),
    ("model_empty_string_id", [("a.yaml", "model: ''\ntables: {}\n")]),
    ("model_int_id_invalid", [("a.yaml", "model: 7\ntables: {}\n")]),
    ("model_missing_invalid", [("a.yaml", "tables: {}\n")]),
    ("enabled_quoted_false",
     [("a.yaml", "model: m\ntables:\n  A:\n    enabled: 'false'\n")]),
    ("enabled_int_zero", [("a.yaml",
                           "model: m\ntables:\n  A:\n    enabled: 0\n")]),
    ("enabled_int_one", [("a.yaml", "model: m\ntables:\n  A:\n    enabled: 1\n")
                        ]),
    ("enabled_int_two_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    enabled: 2\n")]),
    ("enabled_yes", [("a.yaml", "model: m\ntables:\n  A:\n    enabled: 'yes'\n")
                    ]),
    ("enabled_on", [("a.yaml", "model: m\ntables:\n  A:\n    enabled: 'on'\n")
                   ]),
    ("pk_scalar_string_invalid", [("a.yaml",
                                   "model: m\ntables:\n  A:\n    pk: A1\n")]),
    ("pk_list_of_ints_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    pk: [1, 2]\n")]),
    ("pk_empty_list", [("a.yaml", "model: m\ntables:\n  A:\n    pk: []\n")]),
    ("pk_null_invalid", [("a.yaml", "model: m\ntables:\n  A:\n    pk: null\n")
                        ]),
    ("unknown_table_key_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    unknown_key: 1\n")]),
    ("unknown_top_level_key_invalid",
     [("a.yaml", "model: m\nextra_top: 1\ntables: {}\n")]),
    ("unknown_fk_key_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    fk:\n      - cols: [A1]\n"
       "        ref: B\n        ref_cols: [B1]\n        unknown: 1\n"
       "  B:\n    pk: [B1]\n")]),
    ("fk_cols_empty_list_invalid",
     [("a.yaml", "model: m\ntables:\n  B:\n    fk:\n      - cols: []\n"
       "        ref: A\n        ref_cols: []\n  A: {}\n")]),
    ("fk_as_dict_invalid", [("a.yaml", "model: m\ntables:\n  B:\n    fk: {}\n")
                           ]),
    ("table_value_null_invalid", [("a.yaml", "model: m\ntables:\n  A: null\n")
                                 ]),
    ("tables_omitted", [("a.yaml", "model: m\n")]),
    ("self_reference_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    fk:\n      - cols: [A1]\n"
       "        ref: A\n        ref_cols: [A1]\n")]),
    ("fk_missing_ref_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    fk:\n      - cols: [A1]\n"
       "        ref_cols: [A1]\n")]),
    ("duplicate_table_across_models_invalid", [
        ("a.yaml", "model: m1\ntables:\n  A:\n    pk: [A1]\n"),
        ("b.yaml", "model: m2\ntables:\n  A:\n    pk: [A1]\n"),
    ]),
    ("enforced_edge_cycle_invalid",
     [("a.yaml", "model: m\ntables:\n  A:\n    fk:\n      - cols: [X]\n"
       "        ref: B\n        ref_cols: [X]\n"
       "  B:\n    fk:\n      - cols: [X]\n"
       "        ref: A\n        ref_cols: [X]\n")]),
)

# One path per `is_sample_model` rule: `example_*`, `*.example.*`,
# `*_example.*`, a real (non-sample) model, the one committed public
# sample, and a gs:// URI — the loader must agree on the same 6 whatever
# the scheme.
_SAMPLE_PATHS = (
    "config/relationships/example_retail.yaml",
    "config/relationships/foo.example.yaml",
    "config/relationships/bar_example.yml",
    "config/relationships/retail_private.yaml",
    "config/relationships/gcp_public_fk_example.yaml",
    "gs://bucket/models/retail.yaml",
)

_REFERENCE_TABLE = "p.d.t"
_REFERENCE_LIMIT = 10_000


def _file_sources(stem: str) -> list[tuple[str, str]]:
  """`[(source, text)]` for one committed sample model file."""
  path = _ROOT / "config" / "relationships" / f"{stem}.yaml"
  return [(str(path.relative_to(_ROOT)), path.read_text(encoding="utf-8"))]


def _model_entry_goldens(sources: list[tuple[str, str]]) -> dict[str, Any]:
  """`sha12`, `component`, `generation_order`, `enforced_edges` (each
  edge's `enforced`/`drives` flags included, so the mirror's `Edge`
  fields are pinned too, not just its `(cols, ref, ref_cols)` shape) and
  `derived_widenings`, for every table `sources` resolves to — read
  through the original `RelationshipRegistry.from_sources` (the
  mirror's target). `sources` travels with the golden so BOTH parity
  tests parse the identical bytes through their own implementation,
  rather than one side re-reading `config/relationships/` by a relative
  path assumption; it may hold more than one file (a single registry
  built from several models at once, e.g. two committed samples loaded
  together).
  """
  registry = RelationshipRegistry.from_sources(sources)
  tables = [t for m in registry.models for t in m.tables]
  return {
      "sources": [list(pair) for pair in sources],
      "sha12":
          registry.sha12(),
      "component": {
          t: list(registry.component(t)) for t in tables
      },
      "generation_order": {
          t: list(registry.generation_order(registry.component(t)))
          for t in tables
      },
      "enforced_edges": {
          t: [[list(e.cols), e.ref,
               list(e.ref_cols), e.enforced, e.drives]
              for e in registry.enforced_edges(t)] for t in tables
      },
      "derived_widenings": [[
          rec["table"], rec["ref"], rec["via"],
          [list(pair) for pair in rec["added"]]
      ] for rec in registry.derived_widenings()],
  }


def _model_goldens_by_name() -> dict[str, Any]:
  entries = {
      stem: _model_entry_goldens(_file_sources(stem)) for stem in _MODEL_FILES
  }
  entries["invented_widening"] = _model_entry_goldens([
      (_INVENTED_WIDENING_SOURCE, _INVENTED_WIDENING_YAML)
  ])
  entries["retail_and_star_diamond"] = _model_entry_goldens(
      _file_sources("example_retail") + _file_sources("example_star_diamond"))
  return entries


def _parse_verdict_goldens() -> list[dict[str, Any]]:
  """One entry per `_PARSE_CASES` case: the original's accept/reject
  verdict, the error class name when rejected, and the `sha12` when
  accepted."""
  results: list[dict[str, Any]] = []
  for case, sources in _PARSE_CASES:
    entry: dict[str, Any] = {
        "case": case,
        "sources": [list(pair) for pair in sources],
    }
    try:
      registry = RelationshipRegistry.from_sources(sources)
    except RelationshipError as exc:
      entry.update(accept=False, error_class=type(exc).__name__, sha12=None)
    else:
      entry.update(accept=True, error_class=None, sha12=registry.sha12())
    results.append(entry)
  return results


def _encode_value(  # noqa: PLR0911 — type dispatch, clearer flat than nested
    value: Any,) -> Any:
  """One reference-fixture cell as a JSON-safe, type-tagged value.

  Only the goldens FILE needs this: `compute_reference_digest` and its
  mirror both take native Python values (`Decimal`, `datetime`, `date`,
  `bytes`) straight from a BigQuery client row, and JSON cannot carry
  those natively. Both parity tests decode the tags back before calling
  the digest function, so this encoding is never itself under test.
  """
  if isinstance(value, Decimal):
    return {"$decimal": str(value)}
  if isinstance(value, datetime):
    return {"$datetime": value.isoformat()}
  if isinstance(value, date):
    return {"$date": value.isoformat()}
  if isinstance(value, (bytes, bytearray)):
    return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
  if isinstance(value, dict):
    return {k: _encode_value(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_encode_value(v) for v in value]
  return value


def _reference_fixture_rows() -> list[dict[str, Any]]:
  """Invented, public-looking rows (thelook-like users, `@example.com`
  emails) exercising every type `compute_reference_digest` must be
  sensitive to: a `Decimal` pinned to its exact string form (`1.50` vs
  `1.5` hash DIFFERENTLY here — unlike `canonical.py`'s normalizing
  `canonical_value`, this digest's `json.dumps(..., default=str)` never
  folds them together), a tz-aware and a naive `datetime`, a `date`,
  `bytes`, `None`, a nested dict/list, and unicode text. No 15-16 digit
  integers (the precheck Luhn rule).
  """
  return [
      {
          "id": 1001,
          "email": "ava.brown@example.com",
          "first_name": "Ava",
          "last_name": "Brown",
          "created_at": datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC),
          "birth_date": date(1990, 5, 20),
          "balance": Decimal("1.50"),
          "avatar": b"\x89PNG\r\n\x1a\n",
          "referred_by": None,
          "profile": {
              "newsletter": True,
              "channels": ["email", "sms"],
          },
          "city": "São Paulo",
      },
      {
          "id": 1002,
          "email": "wei.liu@example.com",
          "first_name": "Wei",
          "last_name": "Liu",
          "created_at": datetime(2026, 2, 1, 8, 0, 0),
          "birth_date": date(1985, 11, 2),
          "balance": Decimal("1.5"),
          "avatar": b"\xff\xd8\xff\xe0",
          "referred_by": 1001,
          "profile": {
              "newsletter": False,
              "channels": ["push"],
          },
          "city": "Zürich",
      },
      {
          "id": 1003,
          "email": "noor.haidari@example.com",
          "first_name": "Noor",
          "last_name": "Haidari",
          "created_at": datetime(2026, 3, 10, 14, 45, 0, tzinfo=UTC),
          "birth_date": date(1998, 7, 4),
          "balance": Decimal("0"),
          "avatar": None,
          "referred_by": None,
          "profile": {
              "newsletter": True,
              "channels": [],
          },
          "city": "Montréal",
      },
  ]


def _reference_digest_goldens() -> dict[str, Any]:
  rows = _reference_fixture_rows()
  return {
      "rows": [{
          k: _encode_value(v) for k, v in row.items()
      } for row in rows],
      "digest": compute_reference_digest(rows),
  }


def _reference_sql_goldens() -> dict[str, Any]:
  """The exact query `load_reference_rows` sends for `(table, limit)`
  with no `extra_filters`, captured from a mock BigQuery client — the
  same technique `packages/sdfb-tests/tests/unit/io/test_bq_sources.py`
  uses."""
  client = MagicMock()
  client.query.return_value.result.return_value = []
  load_reference_rows(
      table=_REFERENCE_TABLE, limit=_REFERENCE_LIMIT, client=client)
  sql = client.query.call_args[0][0]
  return {"table": _REFERENCE_TABLE, "limit": _REFERENCE_LIMIT, "sql": sql}


def build_goldens() -> dict[str, Any]:
  return {
      "models": _model_goldens_by_name(),
      "is_sample_model": {
          p: is_sample_model(p) for p in _SAMPLE_PATHS
      },
      "parse_verdicts": _parse_verdict_goldens(),
      "reference_digest": _reference_digest_goldens(),
      "reference_sql": _reference_sql_goldens(),
      # D3: E = the first MAX_ROW_DOC_ROWS rows of the reference order —
      # the prompt-exposed row docs the B.1 engine reads back.
      "exposure_rows": MAX_ROW_DOC_ROWS,
  }


def render(goldens: dict[str, Any]) -> str:
  return json.dumps(goldens, sort_keys=True, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--check",
      action="store_true",
      help="exit 1 if the fixture would change, without writing it")
  args = parser.parse_args(argv)
  rendered = render(build_goldens())
  current = _OUTPUT.read_text(encoding="utf-8") if _OUTPUT.exists() else None
  if args.check:
    if current != rendered:
      print(
          f"{_OUTPUT} is stale — run "
          "scripts/evaluation/make_parity_goldens.py to refresh it",
          file=sys.stderr)
      return 1
    print(f"{_OUTPUT} is up to date")
    return 0
  _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
  _OUTPUT.write_text(rendered, encoding="utf-8")
  print(f"wrote {_OUTPUT}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
