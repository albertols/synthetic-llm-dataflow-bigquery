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
"""The `b1_rag` retrieval path, step by step, on a toy table.

    uv run --no-sync python3 scripts/doc/b1_rag_walkthrough.py

Prints every data example quoted in `docs/articles/04-b1-rag-deep-dive.md`.
Nothing here re-implements the engine: each step CALLS the function the
pipeline calls (`serialize_row`, `compute_row_digest`, `compute_chunk_id`,
`chunk_free_text_value`, `chunk_to_bq_row`, `build_index`,
`select_seed_examples`, `_build_pool_prompt`, `_pool_llm_yield`), so when
the implementation moves, this output moves with it and the article is
visibly stale.

Laptop honesty: without the `[embedding]` extra the embedder is the
dependency-free `HashingEmbedder` (lexical, 384-dim) and the index is the
pure-Python exact fallback — the same two seams production fills with
`BgeEmbedder` and FAISS `IndexFlatIP`. The LLM is a scripted stand-in that
returns a fixed candidate list, so the GATES are real and the candidates
are not a model's output.

The table is fictitious: `demo.card_transactions`, 24 rows, one free-text
column (`merchant_name`, till-receipt store names) with one dominant format
and three rare ones.
"""

from __future__ import annotations

import hashlib
import json

# The engine's own private helpers are imported on purpose: the article
# quotes what they do, so it must run them rather than paraphrase them.
# pylint: disable=protected-access
from sdfb_beam.io.digest import compute_reference_digest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag import engine as b1
from sdfb_core.engines.b1_rag.profile import ColumnKind, profile_columns
from sdfb_core.rag.chunking import (
    chunk_free_text_value,
    chunk_row,
    compute_chunk_id,
    compute_row_digest,
    distinct_free_text_values,
)
from sdfb_core.rag.embedding import HashingEmbedder, embedder_identity
from sdfb_core.rag.index import build_index
from sdfb_core.rag.retrieval import (
    retrieve_centroid_top_k,
    select_seed_examples,
)
from sdfb_core.rag.serialize import serialize_row

SOURCE_FQN = "demo.card_transactions"
COLUMNS = ["txn_id", "channel", "amount", "currency", "merchant_name"]

# One dominant format (NAME*CITY), three rare ones, and one dominant LITERAL
# — the realistic shape of a free-text column: a head value, most of the
# remaining mass in one mode, the interesting part in the tail. All values
# are invented.
_SHOPS = ("CAFE ARBOL", "PANADERIA LUZ", "FERRETERIA OSO", "LIBRERIA NUBE",
          "FARMACIA RIO", "MERCADO SOL", "CAFE PINO", "TALLER ROCA",
          "FLORISTERIA IRIS", "BODEGA TINTO", "OPTICA FARO", "PAPELERIA TIZA")
_CITIES = ("MADRID", "SEVILLA", "VALENCIA", "BILBAO", "MALAGA", "VIGO")
_RARE = (
    "METRO NORTE TRAVEL CH",
    "TRANVIA SUR TRAVEL CH",
    "BUS LITORAL TRAVEL CH",
    "PAYLINK *BLUE FERN YOGA",
    "PAYLINK *TINY ROBOT TOYS",
    "PAYLINK *OLD MILL BAKERY",
    "WWW.NUBEBOOKS.EXAMPLE",
    "WWW.OSOTOOLS.EXAMPLE",
)
_HEAD = "ATM WITHDRAWAL"  # 16 of 96 rows: a dominant literal, not an identity
_MERCHANTS = [f"{shop}*{city}" for shop in _SHOPS for city in _CITIES
             ] + list(_RARE) + [_HEAD] * 16
ROWS = [
    {
        # A multiplicative hash spreads the ids over all five digits, so the
        # column's shape template has a 100k value space, not the 96 observed.
        "txn_id": f"T{(i * 7919 + 104) % 100_000:05d}",
        "channel": ("pos", "ecom", "pos", "atm")[i % 4],
        "amount": round(4.5 + 7.25 * i, 2),
        "currency": "EUR",
        "merchant_name": name,
    } for i, name in enumerate(_MERCHANTS)
]

# The run's provenance key: the launcher's own digest over the sample.
REFERENCE_DIGEST = compute_reference_digest(ROWS)

# A scripted model reply — what array completions COULD look like. It holds
# two verbatim source values (one of them a shown seed), a column-name echo
# and an off-format sentence; the gates below must sort them out.
_SCRIPTED_CANDIDATES = [
    "CAFE ROBLE*MADRID",
    "CAFE ARBOL*MADRID",  # copy of a source value AND a shown seed
    "HELADERIA COPO*CADIZ",
    "FARMACIA RIO*MALAGA",  # copy of a source value the prompt never showed
    "PAYLINK *RED KITE CYCLES",
    "TRANVIA ESTE TRAVEL CH",
    "merchant_name: CAFE",  # echoes the column name
    "Here are some values!",  # prose where a receipt line belongs
    "BODEGA CLARETE*LOGRONO",
    "ZAPATERIA PASO*TOLEDO",
    "WWW.ROBLEHOME.EXAMPLE",
    "QUESERIA PRADO*OVIEDO",
]


class _ScriptedClient:
  """`ModelClient` stand-in: returns the scripted candidates once."""

  def __init__(self) -> None:
    self.calls: list[dict] = []

  def generate_json(self, **kwargs) -> list[dict]:
    self.calls.append(kwargs)
    return [{"values": list(_SCRIPTED_CANDIDATES)}]


def _h(title: str) -> None:
  print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def step_chunking() -> None:
  _h("1. CHUNKING — a row becomes bytes, bytes become identity")
  row = ROWS[0]
  text = serialize_row(row, COLUMNS)
  print("row            :", json.dumps(row))
  print("row_doc text   :", text)
  print("reference_digest (all 96 rows):", REFERENCE_DIGEST)
  print("row_digest     :", compute_row_digest(row))
  print("chunk_id       :",
        compute_chunk_id(SOURCE_FQN, compute_row_digest(row), 0))
  shuffled = {k: row[k] for k in reversed(COLUMNS)}
  print("\nsame row, keys in another order:")
  print("  naive str()  :",
        hashlib.sha256(str(row).encode()).hexdigest()[:16], "vs",
        hashlib.sha256(str(shuffled).encode()).hexdigest()[:16], "(differ)")
  print("  row_digest   :",
        compute_row_digest(row)[:16], "vs",
        compute_row_digest(shuffled)[:16], "(identical)")
  print("  row_doc text identical:", serialize_row(shuffled, COLUMNS) == text)

  chunks = chunk_row(
      row,
      column_order=COLUMNS,
      free_text_columns=["merchant_name"],
      source_fqn=SOURCE_FQN,
      reference_digest=REFERENCE_DIGEST,
      embedder_id="bge-small-en-v1.5",
      embedder_version="v1",
      pk_columns=["txn_id"],
  )
  print("\nchunk_row() ->", [(c.chunk_index, c.chunk_kind) for c in chunks])
  value_chunk = chunk_free_text_value(
      "merchant_name",
      row["merchant_name"],
      source_fqn=SOURCE_FQN,
      reference_digest=REFERENCE_DIGEST,
      embedder_id="bge-small-en-v1.5",
      embedder_version="v1",
  )
  print("deduped value chunk (what the population branch writes):")
  print("  chunk_text :", value_chunk.chunk_text)
  print("  row_digest :", value_chunk.row_digest[:16],
        "… (digest of {column, value} — no row, no PK)")
  print("  source_pk  :", value_chunk.source_pk, " metadata:",
        value_chunk.metadata)
  distinct = distinct_free_text_values(ROWS, ["merchant_name"])
  print(f"  {len(ROWS)} rows -> {len(distinct['merchant_name'])} distinct "
        "values to embed (the head literal repeats 16 times)")
  print(
      "embedder_identity('gs://b/synthetic/models/embedders/"
      "bge-small-en-v1.5/v1/') ->",
      embedder_identity(
          "gs://b/synthetic/models/embedders/bge-small-en-v1.5/v1/"))


def _fmt(value: str) -> str:
  """The toy column's four formats (+ the head literal), for the printout."""
  if value == _HEAD:
    return "head"
  if value.startswith("PAYLINK"):
    return "aggregator"
  if value.endswith("TRAVEL CH"):
    return "transport"
  if value.startswith("WWW."):
    return "web"
  return "name*city"


def step_embedding() -> tuple[list[list[float]], list[str]]:
  _h("2. EMBEDDING — text becomes a unit vector; what rag_chunks stores")
  embedder = HashingEmbedder(dim=384)
  values = list(dict.fromkeys(_MERCHANTS))
  vectors = embedder.embed(values)
  v0 = vectors[0]
  norm = sum(x * x for x in v0)**0.5
  nonzero = [(i, round(x, 3)) for i, x in enumerate(v0) if x]
  print(f"embedder       : HashingEmbedder dim={embedder.dim} "
        "(laptop stand-in for bge-small-en-v1.5)")
  print(f"'{values[0]}' -> {len(v0)} floats, L2 norm {norm:.3f}")
  print("  non-zero components (index, value):", nonzero)

  def cos(a: str, b: str) -> float:
    va, vb = vectors[values.index(a)], vectors[values.index(b)]
    return sum(x * y for x, y in zip(va, vb, strict=True))

  pairs = [
      ("CAFE ARBOL*MADRID", "CAFE ARBOL*SEVILLA"),
      ("CAFE ARBOL*MADRID", "PANADERIA LUZ*MADRID"),
      ("CAFE ARBOL*MADRID", "METRO NORTE TRAVEL CH"),
      ("METRO NORTE TRAVEL CH", "TRANVIA SUR TRAVEL CH"),
      ("PAYLINK *BLUE FERN YOGA", "PAYLINK *TINY ROBOT TOYS"),
  ]
  print("cosine similarity = inner product of unit vectors:")
  for a, b in pairs:
    print(f"  {cos(a, b):5.2f}  {a!r} · {b!r}")

  from sdfb_beam.rag.population import chunk_to_bq_row  # pylint: disable=import-outside-toplevel

  chunk = chunk_free_text_value(
      "merchant_name",
      values[0],
      source_fqn=SOURCE_FQN,
      reference_digest=REFERENCE_DIGEST,
      embedder_id="bge-small-en-v1.5",
      embedder_version="v1",
  )
  bq_row = chunk_to_bq_row(chunk, v0, "2026-09-21T00:00:00+00:00")
  bq_row["embedding"] = f"[… {len(bq_row['embedding'])} floats, L2 norm 1.0 …]"
  bq_row["reference_digest"] = bq_row["reference_digest"][:16] + "…"
  bq_row["chunk_id"] = bq_row["chunk_id"][:16] + "…"
  bq_row["row_digest"] = bq_row["row_digest"][:16] + "…"
  print("one rag_chunks row (chunk_to_bq_row):")
  print(json.dumps(bq_row, indent=2, ensure_ascii=False))
  return vectors, values


def step_index(vectors: list[list[float]], values: list[str]) -> None:
  _h("3. THE INDEX — exact inner product over every vector")
  index = build_index(vectors, 384)
  print(
      "index backend  :",
      type(index).__name__, "(FAISS IndexFlatIP when faiss is installed; "
      "the pure-Python fallback returns the same ids)")
  print(f"memory if FAISS: {len(vectors)} x 384 x 4 bytes = "
        f"{len(vectors) * 384 * 4:,} B here; 1,024 x 384 x 4 = "
        f"{1024 * 384 * 4:,} B at the production cap")
  query_text = "CAFE ARBOL*TOLEDO"
  query = HashingEmbedder(dim=384).embed([query_text])[0]
  ids = index.search(query, 3)
  print(f"classic query   '{query_text}' -> top-3:", [values[i] for i in ids])
  top = retrieve_centroid_top_k(index, vectors, values, 8)
  print("centroid query  (what b1_rag asks) -> top-8:")
  for v in top:
    print(f"    {_fmt(v):11s} {v}")
  index.release()


def step_strategies(vectors: list[list[float]], values: list[str]) -> list[str]:
  _h("4. SEED STRATEGIES — the same distinct values, three pickers")
  seeds_by_strategy = {}
  for strategy in ("centroid", "kcenter"):
    seeds = select_seed_examples(vectors, values, 8, strategy=strategy)
    seeds_by_strategy[strategy] = seeds
    print(f"\n--pool_seed_strategy={strategy}")
    for seed in seeds:
      print(f"    {_fmt(seed):11s} {seed}")
    print("    formats reached:", sorted({_fmt(x) for x in seeds}))
  print("\n--pool_seed_strategy=kcenter_rotate (start = attempt * k mod n)")
  for attempt in range(3):
    seeds = select_seed_examples(
        vectors, values, 8, strategy="kcenter_rotate", attempt=attempt)
    print(f"    attempt {attempt}: starts at {seeds[0]!r}; formats",
          sorted({_fmt(x) for x in seeds}))
  return seeds_by_strategy["centroid"]


# Left alone, the profiler sends this column to the shape-expansion exit
# (its masks are identifier-like) and the LLM is never asked — Part 2's
# "five exits before the LLM". ANY non-empty clause takes a column off that
# exit (`B1RagEngine._draws_from_expansion`), which puts it on the pool
# route, where retrieval lives. (`route: "llm"` is a different lever: it
# re-types a STRING the profiler would call categorical or identifier-shaped;
# this column is free text already, so the clause does not need it.)
_CLAUSE = {
    "llm_prompt_constraint": {
        "format": "short store name as printed on a till receipt",
        "charset": "upper-case letters, digits, spaces, dots and asterisks",
        "length": [8, 28],
    }
}


def _schema() -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": SOURCE_FQN
      },
      "schema": [{
          "name":
              name,
          "type":
              "FLOAT64" if name == "amount" else "STRING",
          "mode":
              "NULLABLE",
          "description":
              (json.dumps(_CLAUSE) if name == "merchant_name" else ""),
      } for name in COLUMNS],
  })


def step_prompt_and_gates(seeds: list[str]) -> None:
  _h("5. AUGMENTED GENERATION — the prompt, then the gates")
  prof = profile_columns(_schema(), ROWS)["merchant_name"]
  assert prof.kind is ColumnKind.FREE_TEXT
  print("profile        : kind =", prof.kind.value, "| distinct",
        len(set(prof.observed_values)), "| head_values",
        [(v, round(share, 3)) for v, share in prof.head_values])
  from sdfb_core.engines.text_shapes import length_hint  # pylint: disable=import-outside-toplevel

  # What `B1RagEngine._column_constraint` renders: the clause, plus the
  # measured length band unless the clause pins a length itself.
  constraint = " ".join(
      x for x in
      (prof.llm_prompt_constraint,
       "" if prof.constraint_sets_length else length_hint(prof.observed_values))
      if x)
  prompt = b1._build_pool_prompt(
      "merchant_name", 32, seeds, constraint=constraint)
  print("\nprompt (byte-identical on every round of this column):\n")
  print("  " + prompt.replace(". ", ".\n  "))
  print(f"\n  {len(prompt.encode())} bytes, sha256 "
        f"{hashlib.sha256(prompt.encode()).hexdigest()[:16]}…")

  client = _ScriptedClient()
  y = b1._pool_llm_yield(
      client,
      prompt,
      {"type": "object"},
      prof,
      seeds,
      target=8,
      source_values=frozenset(_MERCHANTS),
  )
  first = client.calls[0]
  print("\nrequest 1 sampling:", {
      k: first[k] for k in ("n", "temperature", "top_p", "top_k", "max_tokens")
  })
  print("scripted reply :", _SCRIPTED_CANDIDATES)
  print("gate outcome   : parsed", y.parsed, "| format_rejected",
        y.format_rejected, "| copies", y.copies, "| seed echoes",
        y.prompt_echoes, "| attempts", y.attempts)
  print("pool           :", y.pool)


def step_end_to_end() -> None:
  _h("6. END TO END — B1RagEngine.setup() + generate_batch() on the laptop")
  # Imported here: the base module pulls pydantic models the earlier steps
  # do not need.
  from sdfb_core.engines.base import (  # pylint: disable=import-outside-toplevel
      GenerationConfig, GenerationContext,
  )

  ctx = GenerationContext(
      table_schema=_schema(),
      reference_rows=ROWS,
      reference_digest=REFERENCE_DIGEST,
      pipeline_run_id="walkthrough",
      num_rows=8,
  )
  engine = b1.B1RagEngine()
  engine.setup(_ScriptedClient(), ctx)
  print("pool built     :", engine._free_text_pools["merchant_name"])
  source = set(_MERCHANTS) - {_HEAD}
  source_ids = {r["txn_id"] for r in ROWS}
  rows = [
      r.model_dump()
      for r in engine.generate_batch(12, GenerationConfig(seed=2026))
  ]
  print("\n12 generated rows (seed=2026):")
  for r in rows:
    name = r["merchant_name"]
    tag = ("head literal, re-emitted at its source share"
           if name == _HEAD else "COPY" if name in source else "novel")
    id_tag = "COPY" if r["txn_id"] in source_ids else "novel id"
    print(f"  {r['txn_id'] or '':7s}({id_tag}) {r['channel']:5s} "
          f"{r['amount']:7.2f} {r['currency']} {name!s:25s} <- {tag}")
  engine.teardown()


def main() -> None:
  step_chunking()
  vectors, values = step_embedding()
  step_index(vectors, values)
  seeds = step_strategies(vectors, values)
  step_prompt_and_gates(seeds)
  step_end_to_end()


if __name__ == "__main__":
  main()
