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
"""Where the Beam pass reads its rows and writes its output rows.

    Sources.read(p, table, side) ─► PCollection[dict]
      source, synthetic   BigQuerySources  ReadFromBigQuery(table=read table,
                                           method=DIRECT_READ, selected_fields=
                                           plan columns, use_native_datetime=
                                           True) → normalize_direct_read
                          InMemorySources  beam.Create(rows_by[(table, side)])
      reference, holdout  both             beam.Create(table.panel.r_rows /
                                           h_rows), projected to the plan
                                           columns — never re-read (D3)

    Sinks.write(rows, table) ─► the PCollections complete once the rows are
                                committed (the FINAL registry row waits on
                                them as side inputs, D7)
      BigQuerySinks    WriteToBigQuery(FILE_LOADS, WRITE_APPEND, CREATE_NEVER,
                       the package schema) → (load job ids, copy job ids);
                       never STREAMING_INSERTS
      LocalJsonSinks   NDJSON shards <out_dir>/<table>/<NNNN>-<label>-*.jsonl,
                       one prefix per write → (files,); `read_rows` reads
                       only this instance's prefixes, and a table directory
                       holding another run's files is refused; `write_rows`
                       is the driver's own write (a registry event, written
                       at once as one shard of its own prefix)
      ClientLoadSinks  LocalJsonSinks, then — after the pipeline, driver-side
                       — `load(bq)`: one `Bq.load_json` job per table, the
                       registry (evaluation_data_history) last

The read table of each side is materialised before the pipeline runs (the
CLI executes `EvaluationPlan.prepare_sql`, Ruling R5): the pinned source
clone and the scope or sample table, both plain tables DIRECT_READ can
read.

DIRECT_READ types. The encoder hashes canonical values, so a cell must
reach it with the Python type the BigQuery client gives the same cell —
the client is what reads the R/H panel (`context.reference.fetch_panel`)
and what the generator read its reference sample with
(`sdfb_beam.io.bq_sources.load_reference_rows`). Beam 2.74's
`_CustomBigQueryStorageSource` reads Avro through fastavro by default and
Arrow (`read_arrow`, each cell `.as_py()`) with `use_native_datetime=True`:

    BigQuery type    client (panel)       Arrow DIRECT_READ     Avro DIRECT_READ
    ───────────────  ───────────────────  ────────────────────  ────────────────
    TIMESTAMP        aware UTC datetime   aware UTC datetime    aware UTC datetime
    DATETIME         naive datetime       naive datetime        text  ◄ fixed here
    DATE, TIME       date, time           date, time            date, time
    NUMERIC          Decimal              Decimal, scale 9      Decimal, scale 9
    BIGNUMERIC       Decimal              Decimal, scale 38     Decimal, scale 38
    BYTES            bytes                bytes                 bytes
    JSON             parsed value         JSON text ◄ fixed     JSON text ◄ fixed
    GEOGRAPHY        WKT text             WKT text              WKT text
    RECORD, ARRAY    dict, list           dict, list            dict, list

Decimal scale needs no fix (`canonical_value` strips trailing zeros). Arrow
is chosen because it types DATETIME natively at every depth, RECORD
sub-fields included; `normalize_direct_read` fixes the top-level JSON (and,
should an Avro read ever feed it, DATETIME text) by the plan's `bq_type`.
Known limits, all on types the generator does not write or nests: a JSON
sub-field inside a RECORD stays text, and INTERVAL (client `relativedelta`,
Arrow `MonthDayNano`) and RANGE are not normalised — rows holding them hash
differently on the panel and DIRECT_READ sides.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import abc
import glob
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import apache_beam as beam
from apache_beam.io.gcp.bigquery import (
    BigQueryDisposition,
    ReadFromBigQuery,
    WriteToBigQuery,
)

from sdfb_evaluation.canonical import json_safe
from sdfb_evaluation.schemas import TABLES, load_schema
from sdfb_evaluation.types import Side

if TYPE_CHECKING:
  from sdfb_evaluation.context.plan import ColumnPlan, TablePlan

__all__ = [
    "LOAD_ORDER",
    "BigQuerySinks",
    "BigQuerySources",
    "ClientLoadSinks",
    "InMemorySources",
    "JsonLoader",
    "LocalJsonSinks",
    "Sinks",
    "Sources",
    "json_line",
    "normalize_direct_read",
]

# The metric tables first, the registry last: its FINAL row must never be
# visible before the rows it summarises (D7).
LOAD_ORDER = (
    "evaluation_metrics",
    "evaluation_profiles",
    "evaluation_row_flags",
    "evaluation_data_history",
)
_PANEL_SIDES = frozenset({Side.REFERENCE, Side.HOLDOUT})
# Beam's default shard template (-SSSSS-of-NNNNN) after a write's prefix.
_SHARD_SUFFIX = "-[0-9]*-of-[0-9]*.jsonl"
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
# Beam's file sink writes under <dir>/beam-temp-<prefix name>-<uuid1 hex>
# and removes it when it finalises; a pipeline that fails leaves it.
_BEAM_TEMP = "beam-temp-"
_BEAM_TEMP_ID = re.compile(r"[0-9a-f]{32}")
_GLOB_CHARS = re.compile(r"[*?\[\]]")
_SHOWN_FILES = 3


# --------------------------------------------------------------------------
# DIRECT_READ types
# --------------------------------------------------------------------------
def _type_fixes(
    columns: Sequence[ColumnPlan]) -> tuple[tuple[str, str, bool], ...]:
  """(name, bq_type, repeated) of the columns DIRECT_READ types unlike the
  client: JSON (text) and DATETIME (text on the Avro path)."""
  return tuple((c.name, c.bq_type, c.mode == "REPEATED")
               for c in columns
               if c.bq_type in ("JSON", "DATETIME"))


def _fixed(name: str, bq_type: str, value: Any) -> Any:
  if not isinstance(value, str):
    return value
  try:
    if bq_type == "JSON":
      return json.loads(value)
    return datetime.fromisoformat(value)
  except ValueError as exc:
    # The message names the column and the type, never the value.
    raise ValueError(f"{bq_type} column {name!r}: DIRECT_READ returned text "
                     f"that is not {bq_type} ({type(exc).__name__})") from exc


def _apply_fixes(row: Mapping[str, Any],
                 fixes: Sequence[tuple[str, str, bool]]) -> dict[str, Any]:
  out = dict(row)
  for name, bq_type, repeated in fixes:
    value = out.get(name)
    if value is None:
      continue
    if repeated:
      out[name] = [_fixed(name, bq_type, item) for item in value]
    else:
      out[name] = _fixed(name, bq_type, value)
  return out


def normalize_direct_read(row: Mapping[str, Any],
                          columns: Sequence[ColumnPlan]) -> dict[str, Any]:
  """A DIRECT_READ row with the BigQuery client's Python types (module
  docstring): top-level JSON text parsed, DATETIME text made a naive
  `datetime`. Returns a new dict; `row` is not modified.

  Raises:
    ValueError: a JSON/DATETIME cell's text does not parse (the message
      names the column, never the value).
  """
  return _apply_fixes(row, _type_fixes(columns))


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def _panel_rows(table: TablePlan, side: Side) -> list[dict]:
  """The panel side's rows restricted to the plan's columns: the panel is
  `SELECT ref.*` of the source, and `beam.Create` embeds its elements in
  the job graph, so source-only columns would only make it bigger. A
  plan column a row lacks stays absent (the encoder names it)."""
  panel = table.panel
  if panel is None:
    raise ValueError(
        f"{table.landing_table}: no R/E/H panel was planned, so the {side} "
        "side has no rows; the reference-based metrics are not evaluated "
        "for this table (check TablePlan.panel before reading it)")
  names = [c.name for c in table.columns]
  rows = panel.r_rows if side is Side.REFERENCE else panel.h_rows
  return [{name: row[name] for name in names if name in row} for row in rows]


class Sources(abc.ABC):
  """Reads one (table, side) into a `PCollection[dict]`.

  The reference and holdout sides always come from the plan's panel
  (`beam.Create`), whatever the implementation — the exact rows the
  planner read and verified.
  """

  def read(self, p: beam.Pipeline, table: TablePlan,
           side: Side | str) -> beam.PCollection:
    """The rows of `table`'s `side`, labelled by landing table and side.

    Raises:
      ValueError: a panel side with no panel, or (per implementation) a
        side with nothing to read.
    """
    side = Side(side)
    label = f"Read[{table.landing_table}/{side}]"
    if side in _PANEL_SIDES:
      panel: beam.PCollection = p | label >> beam.Create(
          _panel_rows(table, side))
      return panel
    return self._read(p, table, side, label)

  @abc.abstractmethod
  def _read(self, p: beam.Pipeline, table: TablePlan, side: Side,
            label: str) -> beam.PCollection:
    """The source or synthetic side's rows."""


class BigQuerySources(Sources):
  """DIRECT_READ (BigQuery Storage Read API, Arrow) of each side's read
  table, restricted to the plan's columns, then `normalize_direct_read`."""

  def transform(self, table: TablePlan, side: Side | str) -> ReadFromBigQuery:
    """The `ReadFromBigQuery` of one source/synthetic side.

    Raises:
      ValueError: a panel side, or a side with nothing to read.
    """
    side = Side(side)
    if side in _PANEL_SIDES:
      raise ValueError(f"the {side} side comes from the plan's panel, not "
                       "from BigQuery")
    if side is Side.SOURCE:
      read_table = table.source_read_table
    else:
      read_table = table.synthetic_read_table or table.scope.read_table
    if not read_table:
      why = table.skip_reason or "see the plan warnings"
      raise ValueError(f"{table.landing_table}: nothing to read for the "
                       f"{side} side (no read table was planned: {why})")
    return ReadFromBigQuery(
        table=read_table,
        method=ReadFromBigQuery.Method.DIRECT_READ,
        selected_fields=[c.name for c in table.columns],
        use_native_datetime=True)

  def _read(self, p: beam.Pipeline, table: TablePlan, side: Side,
            label: str) -> beam.PCollection:
    fixes = _type_fixes(table.columns)
    rows: beam.PCollection = p | label >> self.transform(table, side)
    if fixes:
      rows = rows | f"{label}/Types" >> beam.Map(_apply_fixes, fixes)
    return rows


class InMemorySources(Sources):
  """`beam.Create` over `rows_by[(table name, side)]` for the source and
  synthetic sides (tests, fixtures, the acceptance run).

  Raises (at construction):
    ValueError: a reference/holdout key — those sides always come from
      the plan's panel.
  """

  def __init__(self, rows_by: Mapping[tuple[str, Side | str],
                                      Sequence[Mapping[str, Any]]]):
    rows: dict[tuple[str, Side], list[Mapping[str, Any]]] = {}
    for (name, side_value), side_rows in rows_by.items():
      side = Side(side_value)
      if side in _PANEL_SIDES:
        raise ValueError(f"({name}, {side}): the reference and holdout sides "
                         "always come from the plan's panel, never from "
                         "in-memory rows")
      rows[(name, side)] = list(side_rows)
    self._rows = rows

  def _read(self, p: beam.Pipeline, table: TablePlan, side: Side,
            label: str) -> beam.PCollection:
    key = (table.name, side)
    if key not in self._rows:
      raise ValueError(f"no in-memory rows for ({table.name}, {side}); "
                       f"known: {sorted(str(k) for k in self._rows)}")
    rows: beam.PCollection = p | label >> beam.Create(self._rows[key])
    return rows


# --------------------------------------------------------------------------
# sinks
# --------------------------------------------------------------------------
def _check_table(table: str) -> None:
  if table not in TABLES:
    raise ValueError(f"{table!r} is not an evaluation table; expected one of "
                     f"{list(TABLES)}")


def json_line(row: Mapping[str, Any], table: str) -> str:
  """`row` as one canonical NDJSON line: JSON-safe (NaN/±Inf → null, as a
  BigQuery load requires), sorted keys, compact separators.

  Raises:
    TypeError: a value JSON cannot hold (sinks take rows as
      `to_metric_row`/`registry_seed` build them: strings, numbers,
      booleans, lists, dicts).
  """
  try:
    return json.dumps(
        json_safe(dict(row)),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False)
  except (TypeError, ValueError) as exc:
    raise TypeError(f"{table}: a row is not JSON-serialisable ({exc})") from exc


class Sinks(abc.ABC):
  """Writes output rows (dicts in an evaluation table's schema)."""

  @abc.abstractmethod
  def write(self,
            rows: beam.PCollection,
            table: str,
            *,
            label: str | None = None) -> tuple[beam.PCollection, ...]:
    """Write `rows` to `table`; return the PCollections that are complete
    once the rows are committed — the side inputs the FINAL registry row
    waits on (D7).

    Raises:
      ValueError: `table` is not one of the evaluation tables.
    """


class BigQuerySinks(Sinks):
  """BigQuery FILE_LOADS appends into the pre-created evaluation tables
  (`schemas.bq_mk_commands`): batch-shaped and cheaper than streaming, and
  their load/copy job ids are the "committed" signal. Rows go through
  `json_safe` first."""

  def __init__(self,
               project: str,
               dataset: str,
               *,
               gcs_temp_location: str | None = None):
    self.project = project
    self.dataset = dataset
    self.gcs_temp_location = gcs_temp_location

  def transform(self, table: str) -> WriteToBigQuery:
    """The `WriteToBigQuery` of one evaluation table."""
    _check_table(table)
    return WriteToBigQuery(
        table=f"{self.project}:{self.dataset}.{table}",
        schema={"fields": load_schema(table)},
        method=WriteToBigQuery.Method.FILE_LOADS,
        write_disposition=BigQueryDisposition.WRITE_APPEND,
        create_disposition=BigQueryDisposition.CREATE_NEVER,
        custom_gcs_temp_location=self.gcs_temp_location)

  def write(self,
            rows: beam.PCollection,
            table: str,
            *,
            label: str | None = None) -> tuple[beam.PCollection, ...]:
    write = self.transform(table)
    label = label or f"Write[{table}]"
    result = (
        rows
        | f"{label}/JsonSafe" >> beam.Map(json_safe)
        | f"{label}/Load" >> write)
    return (result.destination_load_jobid_pairs,
            result.destination_copy_jobid_pairs)


class LocalJsonSinks(Sinks):
  """NDJSON shards under `<out_dir>/<table>/` (`json_line` per row), for
  the DirectRunner.

  Every `write` gets its own shard prefix, `<NNNN>-<label slug>` (a
  counter per instance), so two writes to one table never finalise onto
  the same file. `read_rows` reads only the prefixes this instance wrote,
  and `write` refuses a table directory that holds anything else — another
  run's shards, or another run's temp files — instead of mixing runs:
  give each evaluation a fresh `out_dir`. The temp directory Beam leaves
  under one of THIS instance's prefixes, when its pipeline failed at run
  time, is its own: the driver can still write its FAILED event
  (`write_rows`) beside it.
  """

  def __init__(self, out_dir: str):
    """Raises:
      ValueError: `out_dir` holds a glob character (`*?[]`): Beam's file
        sink finds its own shards by glob and would fail at finalize.
    """
    if _GLOB_CHARS.search(out_dir):
      raise ValueError(f"output directory {out_dir!r} holds a glob character "
                       "(*?[]): Beam's file sink matches its shards by glob "
                       "and cannot finalise there; choose another path")
    self.out_dir = out_dir
    self._writes = 0
    self._prefixes: dict[str, list[str]] = {}

  def table_dir(self, table: str) -> str:
    _check_table(table)
    return os.path.join(self.out_dir, table)

  def write(self,
            rows: beam.PCollection,
            table: str,
            *,
            label: str | None = None) -> tuple[beam.PCollection, ...]:
    """Raises:
      ValueError: `table` is not an evaluation table, or its directory
        holds files this instance did not write.
    """
    self._refuse_foreign(table)
    label = label or f"Write[{table}]"
    self._writes += 1
    slug = _UNSAFE_PATH_CHARS.sub("_", label).strip("_") or "write"
    prefix = os.path.join(self.table_dir(table), f"{self._writes:04d}-{slug}")
    self._prefixes.setdefault(table, []).append(prefix)
    files = (
        rows
        | f"{label}/Json#{self._writes}" >> beam.Map(json_line, table)
        | f"{label}/Files#{self._writes}" >> beam.io.WriteToText(
            prefix, file_name_suffix=".jsonl"))
    return (files,)

  def write_rows(self, rows: Sequence[Mapping[str, Any]], table: str, *,
                 label: str) -> str:
    """Driver-side: write `rows` to `table` NOW, as one finished shard
    under its own prefix (the registry's RUNNING and FAILED events, which
    the driver writes around the pipeline). The shard is this instance's
    own, so `read_rows` returns it and a later `write` to the same table
    is not refused. Returns the file.

    Raises:
      ValueError: `table` is not an evaluation table, or its directory
        holds files this instance did not write.
      TypeError: a row is not JSON-serialisable.
    """
    self._refuse_foreign(table)
    self._writes += 1
    slug = _UNSAFE_PATH_CHARS.sub("_", label).strip("_") or "write"
    prefix = os.path.join(self.table_dir(table), f"{self._writes:04d}-{slug}")
    lines = [json_line(row, table) for row in rows]
    os.makedirs(self.table_dir(table), exist_ok=True)
    path = f"{prefix}-00000-of-00001.jsonl"
    with open(path, "w", encoding="utf-8") as shard:
      shard.writelines(f"{line}\n" for line in lines)
    self._prefixes.setdefault(table, []).append(prefix)
    return path

  def _own_shards(self, table: str) -> list[str]:
    return sorted(
        path for prefix in self._prefixes.get(table, ())
        for path in glob.glob(glob.escape(prefix) + _SHARD_SUFFIX))

  def _own_leftover(self, table: str, name: str) -> bool:
    """Whether `name` is the temp directory Beam's file sink made for one
    of this instance's own writes: `beam-temp-<own prefix>-<32 hex>`."""
    for prefix in self._prefixes.get(table, ()):
      head = f"{_BEAM_TEMP}{os.path.basename(prefix)}-"
      if name.startswith(head) and _BEAM_TEMP_ID.fullmatch(name[len(head):]):
        return True
    return False

  def _refuse_foreign(self, table: str) -> None:
    directory = self.table_dir(table)
    if not os.path.isdir(directory):
      return
    own = set(self._own_shards(table))
    foreign = sorted(
        name for name in os.listdir(directory)
        if os.path.join(directory, name) not in own and
        not self._own_leftover(table, name))
    if foreign:
      shown = ", ".join(foreign[:_SHOWN_FILES])
      if len(foreign) > _SHOWN_FILES:
        shown += ", …"
      raise ValueError(
          f"{directory} already holds {len(foreign)} file(s) from another run "
          f"({shown}); this sink never mixes runs — write each evaluation to "
          "a fresh output directory")

  def read_rows(self, table: str) -> list[dict]:
    """Every row this instance wrote to `table` (shard order; `[]` when
    none) — never a shard another run left in the directory.

    Raises:
      ValueError: `table` is not an evaluation table.
    """
    _check_table(table)
    rows: list[dict] = []
    for path in self._own_shards(table):
      with open(path, encoding="utf-8") as shard:
        rows.extend(json.loads(line) for line in shard if line.strip())
    return rows


class JsonLoader(Protocol):
  """What `ClientLoadSinks.load` needs of `context.bq.Bq`."""

  def load_json(self, fqn: str, rows: Sequence[dict],
                schema: list[dict]) -> str:
    ...


class ClientLoadSinks(LocalJsonSinks):
  """`LocalJsonSinks` for the DirectRunner, then — after the pipeline
  finishes — `load(bq)` appends each table's rows with one BigQuery load
  job (`Bq.load_json`: WRITE_APPEND, CREATE_NEVER), in `LOAD_ORDER`, the
  registry last."""

  def __init__(self, out_dir: str, *, project: str, dataset: str):
    super().__init__(out_dir)
    self.project = project
    self.dataset = dataset

  def load(self, bq: JsonLoader) -> dict[str, str]:
    """Load every table that has rows, metric tables before the registry;
    returns `{table: load job id}`. A table with no rows gets no job."""
    jobs = {}
    for table in LOAD_ORDER:
      rows = self.read_rows(table)
      if rows:
        jobs[table] = bq.load_json(f"{self.project}.{self.dataset}.{table}",
                                   rows, load_schema(table))
    return jobs
