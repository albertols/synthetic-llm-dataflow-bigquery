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
"""Opt-in probes for what the metric catalogue does not measure (DEV only).

The evaluator scores marginals, column pairs, rows and foreign-key edges.
It has no metric for row COHERENCE: whether one timestamp stays before
another in the same row, whether a child row's timestamp follows its
parent's, whether a combination of two categories exists in the source at
all. These probes measure exactly that, as aggregates, on the source and
landing tables of an evaluation digest (``evaluation_digest.py``):

  temporal_order      per pair of temporal columns of a table: the order the
                      source keeps (a <= b in at least 99.9 % of the rows)
                      and how often the synthetic rows break it;
  parent_child_order  per foreign-key edge: the same, between a parent's
                      and a child's temporal columns, over the joined rows;
  combination         per strongly dependent pair of categories the digest
                      lists: the share of synthetic rows whose combination
                      never occurs in the source.

They are PROBES, not catalogue metrics: no baseline, no noise floor, no
status. They bill BigQuery bytes on the source and landing tables as they
are now (not the evaluation's time-travel scope), so each query carries
``--max-bytes-billed`` and ``--dry-run`` prints the plan without running.

``sample`` writes a few landed synthetic rows per table for a reviewer (a
person or the agent judging row coherence) under ``real/samples/``. Rows
never go to ``oss/``.

Usage:
    python scripts/e2e/evaluation_probes.py run \
        --report-dir runs/evaluation_reports/<REPORT_ID> --project <PROJECT>
    python scripts/e2e/evaluation_probes.py sample \
        --report-dir runs/evaluation_reports/<REPORT_ID> --project <PROJECT> \
        --rows 40
"""

# The sibling imports need their sys.path entry inserted first.
# pylint: disable=wrong-import-position
# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel
# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluation_digest as digest_tool  # sibling import
import history_mappings  # sibling import

SCHEMA = "sdfb.evaluation_probes/1"
PROBES = ("temporal_order", "parent_child_order", "combination")
_TEMPORAL_TYPES = frozenset({"DATE", "DATETIME", "TIMESTAMP"})
_CATEGORY_KINDS = frozenset({"categorical", "boolean"})
_COLUMN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,299}")
_INVARIANT = 0.999  # the source keeps one order in at least this share
_BROKEN = 0.01  # the synthetic side breaks it in more than this share
_MAX_TEMPORAL = 8  # temporal columns of one table (28 pairs)
_MAX_EDGE_TEMPORAL = 3  # per side of an edge (9 combinations)
_DEFAULT_MAX_BYTES = 50 * 1024**3
_MAX_SAMPLE_PERCENT = 100.0
_PAIR = 2


def _col(name: str) -> str:
  """A validated, backtick-quoted column name (CWE-89 guard)."""
  if not _COLUMN_RE.fullmatch(name or ""):
    raise ValueError(f"column name {name!r} is not a plain identifier")
  return f"`{name}`"


def _ts(name: str, alias: str = "") -> str:
  return f"CAST({alias}{_col(name)} AS TIMESTAMP)"


def _sampled(table: str, percent: float) -> str:
  """``table`` with an optional block sample (a validated literal)."""
  if percent >= _MAX_SAMPLE_PERCENT:
    return table
  if not 0 < percent < _MAX_SAMPLE_PERCENT:
    raise ValueError("--sample-percent must be in (0, 100]")
  return f"{table} TABLESAMPLE SYSTEM ({percent:.6g} PERCENT)"


# --------------------------------------------------------------------------
# SQL builders (pure: tested without BigQuery)
# --------------------------------------------------------------------------
def temporal_order_sql(table_fqn: str,
                       columns: Sequence[str],
                       percent: float = 100.0) -> str:
  """One scan: for every pair of ``columns`` the rows where both are set
  and how they order."""
  parts = ["COUNT(*) AS n"]
  for i, j in itertools.combinations(range(len(columns)), 2):
    a, b = _ts(columns[i]), _ts(columns[j])
    parts += [
        f"COUNTIF({a} IS NOT NULL AND {b} IS NOT NULL) AS both_{i}_{j}",
        f"COUNTIF({a} < {b}) AS lt_{i}_{j}",
        f"COUNTIF({a} = {b}) AS eq_{i}_{j}",
    ]
  table = _sampled(digest_tool.quoted_fqn(table_fqn), percent)
  return f"SELECT {', '.join(parts)} FROM {table}"


def parent_child_order_sql(child_fqn: str,
                           parent_fqn: str,
                           keys: Sequence[tuple[str, str]],
                           child_columns: Sequence[str],
                           parent_columns: Sequence[str],
                           percent: float = 100.0) -> str:
  """The joined rows of one edge: per (parent, child) temporal pair, the
  rows where both are set and the parent's instant is not after the
  child's."""
  parts = ["COUNT(*) AS n"]
  for i, j in itertools.product(
      range(len(parent_columns)), range(len(child_columns))):
    parent = _ts(parent_columns[i], "p.")
    child = _ts(child_columns[j], "c.")
    parts += [
        f"COUNTIF({parent} IS NOT NULL AND {child} IS NOT NULL) "
        f"AS both_{i}_{j}",
        f"COUNTIF({parent} <= {child}) AS le_{i}_{j}",
    ]
  on = " AND ".join(f"c.{_col(c)} = p.{_col(p)}" for c, p in keys)
  child = _sampled(digest_tool.quoted_fqn(child_fqn), percent)
  return (f"SELECT {', '.join(parts)} FROM {child} AS c "
          f"JOIN {digest_tool.quoted_fqn(parent_fqn)} AS p ON {on}")


def combination_sql(source_fqn: str, landing_fqn: str, column: str,
                    column_2: str) -> str:
  """Synthetic rows whose (column, column_2) combination the source never
  holds, over the synthetic rows where both are set."""
  a, b = _col(column), _col(column_2)
  pair = f"CAST({a} AS STRING) AS a, CAST({b} AS STRING) AS b"
  both = f"{a} IS NOT NULL AND {b} IS NOT NULL"
  return (f"WITH src AS (SELECT DISTINCT {pair} "
          f"FROM {digest_tool.quoted_fqn(source_fqn)} WHERE {both}) "
          "SELECT COUNT(*) AS n, COUNTIF(s.a IS NULL) AS unseen "
          f"FROM (SELECT {pair} FROM {digest_tool.quoted_fqn(landing_fqn)} "
          f"WHERE {both}) AS y "
          "LEFT JOIN src AS s ON y.a = s.a AND y.b = s.b")


def sample_sql(landing_fqn: str, rows: int, percent: float) -> str:
  """A few landed rows, from a block sample (cheap on a large table)."""
  table = _sampled(digest_tool.quoted_fqn(landing_fqn), percent)
  return f"SELECT * FROM {table} LIMIT {int(rows)}"


# --------------------------------------------------------------------------
# readings (pure)
# --------------------------------------------------------------------------
def _ratio(part: Any, whole: Any) -> float | None:
  return round(part / whole, 6) if whole else None


def order_reading(source_kept: float | None,
                  synthetic_kept: float | None) -> str:
  """``no_invariant`` unless the source keeps the order; then whether the
  synthetic side keeps it too."""
  if source_kept is None or source_kept < _INVARIANT:
    return "no_invariant"
  if synthetic_kept is None:
    return "invariant_not_measured"
  return ("invariant_broken" if 1.0 -
          synthetic_kept > _BROKEN else "invariant_kept")


def read_temporal_order(columns: Sequence[str], source: Mapping[str, Any],
                        synthetic: Mapping[str, Any]) -> list[dict[str, Any]]:
  """Per pair: the direction the source keeps, and both sides' adherence."""
  out = []
  for i, j in itertools.combinations(range(len(columns)), 2):
    key = f"{i}_{j}"
    shares = {}
    for side, row in (("source", source), ("synthetic", synthetic)):
      both = row.get(f"both_{key}") or 0
      le = (row.get(f"lt_{key}") or 0) + (row.get(f"eq_{key}") or 0)
      ge = both - (row.get(f"lt_{key}") or 0)
      shares[side] = (both, _ratio(le, both), _ratio(ge, both))
    src_both, src_le, src_ge = shares["source"]
    forward = (src_le or 0.0) >= (src_ge or 0.0)
    kept_source = src_le if forward else src_ge
    kept_synthetic = shares["synthetic"][1 if forward else 2]
    out.append({
        "column": columns[i],
        "column_2": columns[j],
        "source_order": "not_after" if forward else "not_before",
        "both_set_source": src_both,
        "both_set_synthetic": shares["synthetic"][0],
        "kept_source": kept_source,
        "kept_synthetic": kept_synthetic,
        "reading": order_reading(kept_source, kept_synthetic),
    })
  return out


def read_parent_child(child_columns: Sequence[str],
                      parent_columns: Sequence[str], source: Mapping[str, Any],
                      synthetic: Mapping[str, Any]) -> list[dict[str, Any]]:
  """Per (parent, child) temporal pair: parent-not-after-child adherence."""
  out = []
  for i, j in itertools.product(
      range(len(parent_columns)), range(len(child_columns))):
    key = f"{i}_{j}"
    kept_source = _ratio(source.get(f"le_{key}"), source.get(f"both_{key}"))
    kept_synthetic = _ratio(
        synthetic.get(f"le_{key}"), synthetic.get(f"both_{key}"))
    out.append({
        "column": child_columns[j],
        "parent_column_index": i,
        "source_order": "parent_not_after_child",
        "joined_source": source.get("n"),
        "joined_synthetic": synthetic.get("n"),
        "kept_source": kept_source,
        "kept_synthetic": kept_synthetic,
        "reading": order_reading(kept_source, kept_synthetic),
    })
  return out


# --------------------------------------------------------------------------
# planning from a digest
# --------------------------------------------------------------------------
def pick_tables(digest: Mapping[str, Any], names: Sequence[str],
                limit: int) -> list[dict[str, Any]]:
  """The table cards to probe: the named ones, else the ``limit`` with the
  most pair-level failures (lowest overall score first on a tie). A card
  without both table paths cannot be probed."""
  cards = [
      c for c in digest.get("tables", [])
      if c.get("source_table") and c.get("landing_table")
  ]
  if names:
    return [c for c in cards if c["table"] in names]
  cards.sort(key=lambda c: (-(c.get("fail_by_level") or {}).get("pair", 0), (
      c.get("scores") or {}).get("overall") or 0.0, c["table"]))
  seen, out = set(), []
  for card in cards:
    if card["table_ref"] not in seen and len(out) < limit:
      seen.add(card["table_ref"])
      out.append(card)
  return out


def _columns_of(client: Any, fqn: str, query: Any) -> list[dict[str, str]]:
  project, dataset, table = (digest_tool.normalize_fqn(fqn) or "").split(".")
  path = (
      digest_tool.quoted(project, dataset)[:-1] +
      ".INFORMATION_SCHEMA.COLUMNS`")
  rows = query(
      client, f"SELECT column_name, data_type FROM {path} "
      "WHERE table_name = @table ORDER BY ordinal_position", {"table": table})
  return [{"name": r["column_name"], "type": r["data_type"]} for r in rows]


def _temporal(columns: Sequence[Mapping[str, str]], limit: int) -> list[str]:
  return [
      c["name"]
      for c in columns
      if c["type"] in _TEMPORAL_TYPES and _COLUMN_RE.fullmatch(c["name"])
  ][:limit]


class Runner:
  """Runs probe queries (or only prints them), with a byte ceiling."""

  def __init__(self,
               client: Any,
               args: argparse.Namespace,
               query: Any = None) -> None:
    self.client = client
    self.dry_run = bool(args.dry_run)
    self.max_bytes = int(args.max_bytes_billed)
    self.percent = float(args.sample_percent)
    self.query = query or digest_tool.run_query
    self.notes: list[str] = []
    self.planned: list[str] = []

  def one(self, sql: str) -> dict[str, Any] | None:
    """The single row of an aggregate query; None on a dry run."""
    self.planned.append(sql)
    if self.dry_run:
      return None
    rows = self._run(sql)
    return rows[0] if rows else {}

  def _run(self, sql: str) -> list[dict[str, Any]]:
    if self.query is not digest_tool.run_query:
      return self.query(self.client, sql, {})
    from google.cloud import bigquery

    job = self.client.query(
        sql,
        job_config=bigquery.QueryJobConfig(maximum_bytes_billed=self.max_bytes))
    return [dict(row.items()) for row in job.result()]


def _base(card: Mapping[str, Any], probe: str) -> dict[str, Any]:
  return {
      "probe": probe,
      "evaluation_id": card["evaluation_id"],
      "engine": card["engine"],
      "table": card["table"],
      "table_ref": card["table_ref"],
  }


def probe_temporal_order(card: Mapping[str, Any], runner: Runner,
                         columns: Sequence[str]) -> list[dict[str, Any]]:
  """Intra-row order of a table's temporal columns, on both sides."""
  if len(columns) < _PAIR:
    return []
  source = runner.one(
      temporal_order_sql(card["source_table"], columns, runner.percent))
  synthetic = runner.one(
      temporal_order_sql(card["landing_table"], columns, runner.percent))
  if source is None or synthetic is None:
    return []
  return [
      _base(card, "temporal_order") | reading
      for reading in read_temporal_order(columns, source, synthetic)
  ]


def probe_parent_child(card: Mapping[str, Any], edge: Mapping[str, Any],
                       parent: Mapping[str, Any], runner: Runner,
                       child_columns: Sequence[str],
                       parent_columns: Sequence[str]) -> list[dict[str, Any]]:
  """Parent-to-child order across one edge, on both sides."""
  match = digest_tool.EDGE_RE.fullmatch(edge["edge"].strip())
  if not match or not child_columns or not parent_columns:
    return []
  keys = list(
      zip([c.strip() for c in match["cols"].split(",")],
          [c.strip() for c in match["ref_cols"].split(",")],
          strict=True))
  rows = []
  for side in ("source_table", "landing_table"):
    rows.append(
        runner.one(
            parent_child_order_sql(card[side], parent[side], keys,
                                   child_columns, parent_columns,
                                   runner.percent)))
  if rows[0] is None or rows[1] is None:
    return []
  out = []
  for reading in read_parent_child(child_columns, parent_columns, rows[0],
                                   rows[1]):
    index = reading.pop("parent_column_index")
    out.append(
        _base(card, "parent_child_order") | {"edge": edge["edge"]} | reading | {
            "parent": {
                "table": parent["table"],
                "table_ref": parent["table_ref"],
                "column": parent_columns[index],
            }
        })
  return out


def probe_combination(card: Mapping[str, Any], pair: Mapping[str, Any],
                      runner: Runner) -> list[dict[str, Any]]:
  """Invented combinations of one strongly dependent category pair."""
  row = runner.one(
      combination_sql(card["source_table"], card["landing_table"],
                      pair["column"], pair["column_2"]))
  if row is None:
    return []
  share = _ratio(row.get("unseen"), row.get("n"))
  return [
      _base(card, "combination") | {
          "column":
              pair["column"],
          "column_2":
              pair["column_2"],
          "source_association":
              pair.get("source_value"),
          "rows_both_set":
              row.get("n"),
          "unseen_combination_share":
              share,
          "reading": ("no_rows" if share is None else "invented_combinations"
                      if share > _BROKEN else "combinations_kept"),
      }
  ]


def run_probes(digest: Mapping[str, Any], runner: Runner,
               args: argparse.Namespace, query: Any) -> list[dict[str, Any]]:
  """Every requested probe over the picked tables."""
  cards = pick_tables(digest, args.table, args.max_tables)
  by_slice = {
      (c["evaluation_id"], c["table"]): c for c in digest.get("tables", [])
  }
  wanted = set(args.probe or PROBES)
  schema: dict[str, list[dict[str, str]]] = {}

  def columns(fqn: str) -> list[dict[str, str]]:
    if fqn not in schema:
      schema[fqn] = _columns_of(runner.client, fqn, query)
    return schema[fqn]

  out: list[dict[str, Any]] = []
  for card in cards:
    try:
      temporal = _temporal(columns(card["source_table"]), _MAX_TEMPORAL)
      if "temporal_order" in wanted:
        out += probe_temporal_order(card, runner, temporal)
      if "parent_child_order" in wanted:
        out += _edges_of(card, digest, by_slice, runner, columns, temporal)
      if "combination" in wanted:
        for pair in digest.get("pairs", {}).get("strong_source_pairs", []):
          if (pair["table_ref"] == card["table_ref"] and
              pair["evaluation_id"] == card["evaluation_id"] and
              set(pair["kind_pair"].split(" x ")) <= _CATEGORY_KINDS):
            out += probe_combination(card, pair, runner)
    except Exception as e:  # pylint: disable=broad-exception-caught
      runner.notes.append(
          f"a probe of one table failed ({type(e).__name__}); its other "
          "probes were skipped")
  return out


def _edges_of(card: Mapping[str, Any], digest: Mapping[str, Any],
              by_slice: Mapping[tuple, Mapping[str, Any]], runner: Runner,
              columns: Any, child_temporal: Sequence[str]) -> list[dict]:
  out: list[dict] = []
  for edge in digest.get("edges", []):
    if (edge["table_ref"] != card["table_ref"] or
        edge["evaluation_id"] != card["evaluation_id"]):
      continue
    match = digest_tool.EDGE_RE.fullmatch(edge["edge"].strip())
    name = match["ref"].strip().rsplit(".", 1)[-1] if match else ""
    parent = by_slice.get((card["evaluation_id"], name))
    if not parent or not parent.get("source_table") or not parent.get(
        "landing_table"):
      runner.notes.append("an edge whose parent is outside this digest was "
                          "not probed")
      continue
    parent_temporal = _temporal(
        columns(parent["source_table"]), _MAX_EDGE_TEMPORAL)
    out += probe_parent_child(card, edge, parent, runner,
                              child_temporal[:_MAX_EDGE_TEMPORAL],
                              parent_temporal)
  return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def _aliaser(alias_map: Mapping[str, Any],
             registry_path: Path) -> digest_tool.Aliaser:
  aliaser = digest_tool.Aliaser(
      history_mappings.HistoryMappings.load(registry_path))
  for entry in alias_map.get("tables", []):
    aliaser.refs.setdefault(entry["table"], entry["table_ref"])
  aliaser.models.update(alias_map.get("models", {}))
  aliaser.external.update(alias_map.get("external_tables", {}))
  return aliaser


def _load(report_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
  paths = [
      report_dir / "real" / name
      for name in ("evaluation_digest.json", "alias_map.json")
  ]
  for path in paths:
    if not path.exists():
      digest_tool.die(f"{path} not found: run evaluation_digest.py digest "
                      "first")
  digest, alias_map = (
      json.loads(path.read_text(encoding="utf-8")) for path in paths)
  return digest, alias_map


def _cmd_run(args: argparse.Namespace,
             client: Any = None,
             query: Any = None) -> int:
  digest, alias_map = _load(args.report_dir)
  client = client or digest_tool.bq_client(args.project)
  runner = Runner(client, args, query)
  records = run_probes(digest, runner, args, query or digest_tool.run_query)
  if args.dry_run:
    print("\n\n".join(runner.planned))
    print(f"\n{len(runner.planned)} query(ies) planned; nothing was run")
    return 0
  payload = {
      "schema": SCHEMA,
      "space": "real",
      "thresholds": {
          "invariant": _INVARIANT,
          "broken": _BROKEN
      },
      "sample_percent": runner.percent,
      "notes": sorted(set(runner.notes)),
      "probes": records,
  }
  aliaser = _aliaser(alias_map, args.history_mappings)
  oss = digest_tool.to_oss(payload, aliaser)
  oss["space"] = "oss"
  tokens = digest_tool.real_tokens(alias_map)
  hits = digest_tool.scan_text("\n".join(digest_tool.strings(oss)), {
      **tokens, "columns": set()
  })
  if hits["identifiers"] or hits["tables"]:
    digest_tool.die("a real name survived aliasing; nothing was written: " +
                    json.dumps(hits))
  aliaser.registry.save(args.history_mappings)
  digest_tool.dump(args.report_dir / "real" / "evaluation_probes.json", payload)
  digest_tool.dump(args.report_dir / "oss" / "evaluation_probes.json", oss)
  broken = sum(r["reading"] in ("invariant_broken", "invented_combinations")
               for r in records)
  print(f"probes: {len(records)} reading(s), {broken} broken invariant(s) or "
        f"invented combination(s), {len(runner.planned)} query(ies)")
  print(f"real → {args.report_dir / 'real' / 'evaluation_probes.json'}")
  print(f"oss  → {args.report_dir / 'oss' / 'evaluation_probes.json'}")
  return 0


def _cmd_sample(args: argparse.Namespace, client: Any = None) -> int:
  digest, alias_map = _load(args.report_dir)
  aliases = {t["table_ref"]: t["alias"] for t in alias_map.get("tables", [])}
  client = client or digest_tool.bq_client(args.project)
  out_dir = args.report_dir / "real" / "samples"
  out_dir.mkdir(parents=True, exist_ok=True)
  for card in pick_tables(digest, args.table, args.max_tables):
    sql = sample_sql(card["landing_table"], args.rows, args.sample_percent)
    rows = [dict(row.items()) for row in client.query(sql).result()]
    path = out_dir / f"{aliases.get(card['table_ref'], card['table'])}.jsonl"
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, default=str) + "\n"
            for row in rows),
        encoding="utf-8")
    print(f"{len(rows)} landed row(s) → {path}   (real values: local only)")
  return 0


def build_parser() -> argparse.ArgumentParser:
  """The command line: ``run`` (aggregate probes) and ``sample`` (rows)."""
  ap = argparse.ArgumentParser(
      description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = ap.add_subparsers(dest="cmd", required=True)
  for name, text in (("run", "aggregate probes on source and landing tables"),
                     ("sample", "a few landed rows per table, for a reviewer")):
    sp = sub.add_parser(name, help=text)
    sp.add_argument("--report-dir", type=Path, required=True)
    sp.add_argument("--project", required=True, help="billing project")
    sp.add_argument(
        "--table", action="append", default=[], help="real table name")
    sp.add_argument(
        "--max-tables",
        type=int,
        default=5,
        help="without --table: the tables with the most pair-level failures")
    sp.add_argument(
        "--sample-percent",
        type=float,
        default=100.0 if name == "run" else 1.0,
        help="TABLESAMPLE SYSTEM percent (100 reads the whole table)")
    if name == "run":
      sp.add_argument("--probe", action="append", choices=PROBES, default=[])
      sp.add_argument(
          "--max-bytes-billed", type=int, default=_DEFAULT_MAX_BYTES)
      sp.add_argument(
          "--dry-run", action="store_true", help="print the SQL, run nothing")
      sp.add_argument(
          "--history-mappings",
          type=Path,
          default=history_mappings.DEFAULT_PATH)
    else:
      sp.add_argument("--rows", type=int, default=40)
  return ap


def main(argv: list[str] | None = None) -> int:
  """Entry point; returns the process exit code."""
  args = build_parser().parse_args(argv)
  try:
    return _cmd_run(args) if args.cmd == "run" else _cmd_sample(args)
  except digest_tool.LeakError as e:
    digest_tool.die(f"{e}; nothing was written to oss/")


if __name__ == "__main__":
  raise SystemExit(main())
