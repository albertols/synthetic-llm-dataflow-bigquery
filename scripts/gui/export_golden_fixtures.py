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
"""Export golden fixtures that pin the GUI's TypeScript ports to Python.

    UV_PROJECT_ENVIRONMENT=.venv-gui uv run python scripts/gui/export_golden_fixtures.py
    UV_PROJECT_ENVIRONMENT=.venv-gui uv run python scripts/gui/export_golden_fixtures.py --check

The Synthetic Platform GUI (ADR 0042) re-implements three small pieces of
`sdfb_core.rag` in TypeScript so the RAG tab can run them in the browser.
This script runs the Python originals and writes what they return into
`gui/packages/contracts/generated/golden/`:

- `hashing_embedder.json`: `HashingEmbedder` over 40 strings chosen to hit
  the whitespace set `str.split()` uses (`\\x1c`-`\\x1f`, `\\x85`, NBSP,
  U+3000 …, but not U+FEFF or U+200B), tabs, emoji, the empty string and a
  vector whose signed buckets cancel to zero. Each case carries its tokens,
  `(token, bucket, sign)` triples (the uint64 modulo), the integer counts
  and the normalized non-zero values.
- `great_serialize.json`: `serialize_row` over 10 invented rows whose
  values are type-tagged, so a float `1.0`, an int `1`, a `datetime` and a
  `Decimal` survive JSON and render exactly as Python's `str()`.
- `retrieval.json`: centroid top-k (the pure-Python exact index), greedy
  k-center and k-center-rotate picks on a mulberry32-seeded 64x384 matrix
  (with duplicate rows, for tie-breaks) and on a collapsed 12x8 matrix
  (few distinct vectors, for the "never re-pick" rule).

Floats are rounded to 12 decimals: the TS tests compare vectors to 1e-6 and
the integer parts (buckets, counts, picks) exactly. `--check` exits 1 when a
committed file differs from a fresh export.

Design: docs/DESIGN.md §12 Platform GUI
(ADR 0042).
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import platform
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from sdfb_core.rag import embedding, retrieval, serialize
from sdfb_core.rag.index import _PyExactIPIndex  # pylint: disable=protected-access  # the goldens pin the pure-Python index, never FAISS float32

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "gui" / "packages" / "contracts" / "generated" / "golden"
_DECIMALS = 12
_U32 = 0xFFFFFFFF
_MAIN_SEED = 20260928
_ROWS, _COLS = 64, 384
_DUPLICATES = ((10, 3), (11, 3), (40, 17))  # (row overwritten, row copied)

# 40 strings: ASCII, GReaT-style rows, Unicode, the separators `str.split()`
# treats as whitespace, the ones it does not, tabs and emoji.
TEXTS = (
    "",
    "   ",
    "hello",
    "hello world",
    "Hello World",
    "hello  world",
    "the quick brown fox jumps over the lazy dog",
    "repeat repeat repeat repeat",
    "status is Complete, sale_price is 42.5, user_id is 17",
    "first_name is Ana, last_name is Ruiz, country is Spain",
    "tab\tseparated\tvalues",
    "new\nline\r\nwindows",
    "vertical\x0btab\x0cform feed",
    "file\x1cgroup\x1drecord\x1eunit\x1fseparators",
    "next\x85line",
    "no\u00a0break\u00a0space",
    "zero\u200bwidth\u200bspace",
    "\ufeffbom at start",
    "bom\ufeffinside",
    "ideographic\u3000space",
    "line\u2028separator\u2029paragraph",
    "en\u2002em\u2003thin\u2009spaces",
    "ogham\u1680mark",
    "narrow\u202fnbsp and\u205fmath",
    "São Paulo café naïve",
    "Zürich Straße",
    "東京 大阪 京都",
    "مرحبا بالعالم",
    "Привет мир",
    "emoji 🎁 gift",
    "family 👨\u200d👩\u200d👧 zwj",
    "flag 🇪🇸 and 🇯🇵",
    "skin tone 👍🏽 ok",
    "mixed\t🎉\u3000tabs\x1fand\x85more",
    "a,b,c;d|e",
    "UPPER lower MiXeD 12345",
    "email ana.ruiz@example.com",
    "0.1 1e-05 -0.0 1234567.891",
    "(unused: the cancel case is appended below)",
    "trailing space ",
)
_CANCEL_PLACEHOLDER = 38

_COLUMNS = ("order_id", "user_id", "status", "sale_price", "is_gift",
            "created_at", "shipped_on", "discount", "notes", "country")


def _i(v: int | str) -> dict[str, Any]:
  """An int; one above 2^53 is written as text so JSON keeps every digit."""
  return {"t": "int", "v": v}


def _f(v: float) -> dict[str, Any]:
  return {"t": "float", "v": v}


def _s(v: str) -> dict[str, Any]:
  return {"t": "str", "v": v}


def _b(v: bool) -> dict[str, Any]:
  return {"t": "bool", "v": v}


_NULL = {"t": "null"}

# Ten invented rows. Absent keys render as `null` (serialize_row uses get()).
ROWS = (
    {
        "order_id": _i(1001),
        "user_id": _i(17),
        "status": _s("Complete"),
        "sale_price": _f(42.5),
        "is_gift": _b(False),
        "created_at": {
            "t": "datetime",
            "v": "2026-03-01T10:15:00+00:00"
        },
        "shipped_on": {
            "t": "date",
            "v": "2026-03-02"
        },
        "discount": {
            "t": "decimal",
            "v": "12.50"
        },
        "notes": _s("leave at the door"),
        "country": _s("Spain"),
    },
    {
        "order_id": _i(1002),
        "user_id": _i(18),
        "status": _s("Shipped"),
        "sale_price": _f(1.0),
        "is_gift": _b(True),
        "created_at": {
            "t": "datetime",
            "v": "2026-03-01T23:59:59.123456+00:00"
        },
        "shipped_on": _NULL,
        "discount": _f(0.0),
        "notes": _s(""),
        "country": _s("Brasil"),
    },
    {
        "order_id": _i(1003),
        "user_id": _i(-1),
        "status": _s("Processing"),
        "sale_price": _f(1e16),
        "is_gift": _NULL,
        "created_at": {
            "t": "datetime",
            "v": "2026-03-02T08:00:00"
        },
        "notes": _s("gift, wrapped, with a card"),
        "country": _s("São Paulo"),
    },
    {
        "order_id": _i(1004),
        "user_id": _i(0),
        "status": _s("Cancelled"),
        "sale_price": _f(1e-5),
        "is_gift": _b(False),
        "discount": _f(-0.0),
        "notes": _s("status is Complete"),
        "country": _s("Zürich"),
    },
    {
        "order_id": _i(1005),
        "user_id": _i(19),
        "status": _s("Returned"),
        "sale_price": _f(123.0),
        "is_gift": _b(True),
        "created_at": {
            "t": "datetime",
            "v": "2026-03-03T12:00:00-05:00"
        },
        "discount": _f(2.5e-7),
        "notes": _s("🎁 birthday"),
        "country": _s("日本"),
    },
    {
        "order_id": _i(1006),
        "user_id": _i(20),
        "status": _s("Complete"),
        "sale_price": _f(1234567.891),
        "is_gift": _b(False),
        "shipped_on": {
            "t": "date",
            "v": "2026-12-31"
        },
        "discount": _f(0.1),
        "notes": _s("  padded  "),
        "country": _s("United States"),
    },
    {
        "order_id": _i(1007),
        "user_id": _i(21),
        "status": _NULL,
        "sale_price": _f(1e22),
        "is_gift": _b(True),
        "discount": _f(0.0001),
        "notes": _s("tab\tinside"),
        "country": _NULL,
    },
    {
        "order_id": _i(1008),
        "user_id": _i(22),
        "status": _s("Shipped"),
        "sale_price": _f(3.14159),
        "is_gift": _b(False),
        "created_at": {
            "t": "datetime",
            "v": "2026-03-04T00:00:00+00:00"
        },
        "discount": {
            "t": "decimal",
            "v": "0.00"
        },
        "notes": _s("line\nbreak"),
        "country": _s("España"),
    },
    {
        "order_id": _i("123456789012345678901"),
        "user_id": _i(23),
        "status": _s("Complete"),
        "sale_price": _f(99.99),
        "is_gift": _b(True),
        "discount": _f(1e-4),
        "notes": _s("an int above 2^53, kept as text"),
        "country": _s("France"),
    },
    {
        "order_id": _i(1010),
        "user_id": _i(24),
        "status": _s("Processing"),
        "sale_price": _f(0.30000000000000004),
        "is_gift": _b(False),
        "created_at": {
            "t": "datetime",
            "v": "2026-03-05T06:07:08.000001+00:00"
        },
        "discount": _f(100.0),
        "notes": _s("is is is"),
        "country": _s("Deutschland"),
    },
)


def decode_row(tagged: dict[str, dict[str, Any]]) -> dict[str, Any]:
  """A golden row back to the Python values `serialize_row` receives."""
  out: dict[str, Any] = {}
  for column, cell in tagged.items():
    kind = cell["t"]
    if kind == "null":
      out[column] = None
    elif kind == "int":
      out[column] = int(cell["v"])
    elif kind == "float":
      out[column] = float(cell["v"])
    elif kind == "bool":
      out[column] = bool(cell["v"])
    elif kind == "datetime":
      out[column] = dt.datetime.fromisoformat(cell["v"])
    elif kind == "date":
      out[column] = dt.date.fromisoformat(cell["v"])
    elif kind == "decimal":
      out[column] = decimal.Decimal(cell["v"])
    else:
      out[column] = str(cell["v"])
  return out


def mulberry32(seed: int) -> Iterator[int]:
  """The mulberry32 PRNG as uint32 draws; bit-identical to the TS port."""
  state = seed & _U32
  while True:
    state = (state + 0x6D2B79F5) & _U32
    t = ((state ^ (state >> 15)) * (state | 1)) & _U32
    t = ((t + (((t ^ (t >> 7)) * (t | 61)) & _U32)) & _U32) ^ t
    yield (t ^ (t >> 14)) & _U32


def matrix(spec: dict[str, Any]) -> list[list[float]]:
  """The matrix a spec describes: u32 / 2^32 - 0.5, exact in binary64."""
  draws = mulberry32(spec["seed"])
  rows = [[next(draws) / 4294967296 - 0.5
           for _ in range(spec["cols"])]
          for _ in range(spec["base_rows"])]
  if spec.get("repeat_base"):
    rows = [list(rows[i % spec["base_rows"]]) for i in range(spec["rows"])]
  for dst, src in spec.get("duplicates", []):
    rows[dst] = list(rows[src])
  return rows


def centroid_top_k(vectors: list[list[float]], k: int) -> list[int]:
  """`retrieve_centroid_top_k` over the pure-Python exact index."""
  index = _PyExactIPIndex(vectors, len(vectors[0]))
  return retrieval.retrieve_centroid_top_k(index, vectors,
                                           list(range(len(vectors))), k)


def _round(value: float) -> float:
  return round(value, _DECIMALS) + 0.0


def _whitespace() -> list[int]:
  return [c for c in range(0x110000) if chr(c).isspace()]


def _cancelling_text(dim: int, seed: int) -> str:
  """Two tokens in one bucket with opposite signs: a zero vector."""
  embedder = embedding.HashingEmbedder(dim=dim, seed=seed)
  seen: dict[int, tuple[str, float]] = {}
  for i in range(10_000):
    token = f"w{i}"
    bucket, sign = embedder._bucket_and_sign(token)  # pylint: disable=protected-access  # the case needs a known bucket collision
    if bucket in seen and seen[bucket][1] == -sign:
      return f"{seen[bucket][0]} {token}"
    seen.setdefault(bucket, (token, sign))
  raise RuntimeError("no cancelling pair found")


def _hashing_case(embedder: embedding.HashingEmbedder,
                  text: str) -> dict[str, Any]:
  tokens = text.split() or [text]
  triples = []
  counts: dict[int, float] = {}
  for token in tokens:
    bucket, sign = embedder._bucket_and_sign(token)  # pylint: disable=protected-access  # the uint64 modulo
    triples.append([token, bucket, int(sign)])
    counts[bucket] = counts.get(bucket, 0.0) + sign
  vector = embedder.embed([text])[0]
  return {
      "text": text,
      "tokens": tokens,
      "buckets": triples,
      "counts": [[b, int(c)] for b, c in sorted(counts.items()) if c],
      "norm2": int(sum(c * c for c in counts.values())),
      "nonzero": [[i, _round(v)] for i, v in enumerate(vector) if v != 0.0],
  }


def _hashing() -> dict[str, Any]:
  dim, seed = 384, 0
  texts = list(TEXTS)
  texts[_CANCEL_PLACEHOLDER] = _cancelling_text(dim, seed)
  embedder = embedding.HashingEmbedder(dim=dim, seed=seed)
  small = embedding.HashingEmbedder(dim=16, seed=7)
  return {
      "generated_by": "scripts/gui/export_golden_fixtures.py",
      "python": platform.python_version(),
      "source": "packages/sdfb-core/src/sdfb_core/rag/embedding.py "
                "(HashingEmbedder)",
      "dim": dim,
      "seed": seed,
      "whitespace": _whitespace(),
      "cases": [_hashing_case(embedder, text) for text in texts],
      "small": {
          "dim": 16,
          "seed": 7,
          "cases": [_hashing_case(small, text) for text in texts[:8]],
      },
  }


def _great() -> dict[str, Any]:
  return {
      "generated_by":
          "scripts/gui/export_golden_fixtures.py",
      "python":
          platform.python_version(),
      "source":
          "packages/sdfb-core/src/sdfb_core/rag/serialize.py "
          "(serialize_row)",
      "column_order":
          list(_COLUMNS),
      "tags": {
          "int": "Python int (JSON number; may exceed 2^53, compare as text)",
          "float": "Python float; rendered with repr(), e.g. 1.0, 1e-05",
          "str": "str",
          "bool": "True/False rendered true/false",
          "null": "None",
          "datetime": "datetime.fromisoformat(v); str() uses a space",
          "date": "date.fromisoformat(v)",
          "decimal": "decimal.Decimal(v); str() keeps trailing zeros",
      },
      "rows": [{
          "row": row,
          "text": serialize.serialize_row(decode_row(row), _COLUMNS),
      } for row in ROWS],
  }


def _retrieval_cases(name: str, vectors: list[list[float]],
                     ks: Sequence[int]) -> list[dict[str, Any]]:
  n = len(vectors)
  items = list(range(n))
  cases = []
  for k in ks:
    cases.append({
        "matrix": name,
        "strategy": "centroid",
        "k": k,
        "attempt": 0,
        "picks": centroid_top_k(vectors, k),
    })
    cases.append({
        "matrix": name,
        "strategy": "kcenter",
        "k": k,
        "attempt": 0,
        "picks": retrieval.retrieve_kcenter_k(vectors, items, k),
    })
    for attempt in range(4):
      start = (attempt * k) % n if k > 0 else 0
      cases.append({
          "matrix": name,
          "strategy": "kcenter_rotate",
          "k": k,
          "attempt": attempt,
          "picks": retrieval.retrieve_kcenter_k(vectors, items, k, start=start),
      })
  return cases


def _retrieval() -> dict[str, Any]:
  specs = {
      "main": {
          "generator": "mulberry32",
          "seed": _MAIN_SEED,
          "rows": _ROWS,
          "cols": _COLS,
          "base_rows": _ROWS,
          "transform": "u32 / 2^32 - 0.5, row-major",
          "duplicates": [list(pair) for pair in _DUPLICATES],
      },
      "collapsed": {
          "generator": "mulberry32",
          "seed": 7,
          "rows": 12,
          "cols": 8,
          "base_rows": 3,
          "repeat_base": True,
          "transform": "3 base rows repeated: row i = base[i mod 3]",
          "duplicates": [],
      },
  }
  cases = []
  checks = {}
  for name, ks in (("main", (0, 1, 2, 4, 8, 16, 64, 70)), ("collapsed",
                                                           (1, 3, 5, 12))):
    vectors = matrix(specs[name])
    checks[name] = {
        "sum": _round(sum(sum(row) for row in vectors)),
        "first": [_round(v) for v in vectors[0][:4]],
        "last": [_round(v) for v in vectors[-1][-4:]],
        "centroid_head": [_round(v) for v in retrieval.centroid(vectors)[:4]],
    }
    cases.extend(_retrieval_cases(name, vectors, ks))
  return {
      "generated_by": "scripts/gui/export_golden_fixtures.py",
      "python": platform.python_version(),
      "source": "packages/sdfb-core/src/sdfb_core/rag/retrieval.py "
                "(centroid over _PyExactIPIndex, retrieve_kcenter_k)",
      "note": "kcenter_rotate starts at (attempt * k) mod n, as "
              "select_seed_examples passes it; k >= n returns every item.",
      "matrices": specs,
      "checks": checks,
      "cases": cases,
  }


def build() -> dict[str, dict[str, Any]]:
  return {
      "hashing_embedder": _hashing(),
      "great_serialize": _great(),
      "retrieval": _retrieval(),
  }


def render(doc: dict[str, Any]) -> str:
  return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  parser.add_argument(
      "--check",
      action="store_true",
      help="exit 1 when a committed file differs from a fresh export")
  parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
  args = parser.parse_args(argv)
  drift = []
  for name, doc in build().items():
    path = args.out_dir / f"{name}.json"
    text = render(doc)
    if args.check:
      current = path.read_text(encoding="utf-8") if path.is_file() else ""
      if current != text:
        drift.append(path)
      continue
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
  for path in drift:
    print(
        f"{path}: drift — re-run scripts/gui/export_golden_fixtures.py",
        file=sys.stderr)
  return 1 if drift else 0


if __name__ == "__main__":
  sys.exit(main())
