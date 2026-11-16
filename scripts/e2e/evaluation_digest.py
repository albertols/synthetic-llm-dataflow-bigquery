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
"""Digest one or many stored evaluations into a deterministic evidence pack.

The statistical evaluator (``packages/sdfb-evaluation``, ADR 0041) writes
thousands of metric rows per run. This tool reads them for ONE evaluation
or for a fleet (a time span, an engine, a relationship model) and writes the
numbers an assessment needs, so that the agent which judges them never does
the arithmetic itself:

  * ``real/evaluation_digest.json`` — real table and column names, for use
    where the data lives;
  * ``oss/evaluation_digest.json``  — the same digest with every table,
    column, edge and relationship model replaced by its alias from the
    persistent registry (``history_mappings.py``, ADR 0029). It is built by
    allow-list: a string reaches ``oss/`` only under a key that is known to
    hold an alias, an identifier of the evaluator's own vocabulary, or a
    validated id. Anything else stops the run instead of being written;
  * ``real/alias_map.json``         — the decode key of this report
    (LOCAL-ONLY, like the registry itself).

Sections of the digest: the selection (and what was left out), the headline
of each evaluation as the GUI states it, the level x family cells per engine
with a rubric grade, the failure signatures (metric x column kind) with their
prevalence across tables, a card per table, the worst rows per level, how
column-pair dependence failed (lost / invented / shifted), privacy readings
(absolute rates next to their lifts), the reasons metrics did not run, and
what changed between evaluations of the same table.

Usage (BigQuery through Application Default Credentials):
    python scripts/e2e/evaluation_digest.py digest \
        --project <PROJECT> --since-days 7 --engine b1_rag \
        --engine-backend b2_library=empirical
    python scripts/e2e/evaluation_digest.py digest \
        --project <PROJECT> --evaluation-id eval-20261115T151634Z-c06bb27b

Offline, from ``sdfb-eval report --format json`` exports:
    python scripts/e2e/evaluation_digest.py digest --report-json a.json \
        --report-json b.json

After the report is written in alias space:
    python scripts/e2e/evaluation_digest.py scan \
        --dir runs/evaluation_reports/<REPORT_ID>/oss \
        --alias-map runs/evaluation_reports/<REPORT_ID>/real/alias_map.json
    python scripts/e2e/evaluation_digest.py decode \
        --alias-map runs/evaluation_reports/<REPORT_ID>/real/alias_map.json \
        --in runs/evaluation_reports/<REPORT_ID>/oss/report.md \
        --out runs/evaluation_reports/<REPORT_ID>/real/report.md
"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel
# The sibling `history_mappings` import needs its sys.path entry first.
# pylint: disable=wrong-import-position
# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import argparse
import collections
import contextlib
import dataclasses
import datetime as _dt
import hashlib
import json
import math
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_mappings  # sibling import; path must be set up first

SCHEMA = "sdfb.evaluation_digest/1"
ALIAS_MAP_SCHEMA = "sdfb.evaluation_alias_map/1"
RUBRIC_VERSION = "1"
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOGUE = (
    _REPO_ROOT / "packages" / "sdfb-evaluation" / "src" / "sdfb_evaluation" /
    "catalogue" / "metrics.yaml")
DEFAULT_OUT_ROOT = Path("runs/evaluation_reports")

# -- the evaluator's vocabulary (mirrors scoring/__init__.py and the GUI) ----
GRADED = ("pass", "warn", "fail")
STATUSES = (*GRADED, "info", "not_evaluated")
LEVELS = ("field", "column", "pair", "row", "table", "relationship")
FAMILIES = ("fidelity", "privacy", "integrity", "diversity")
READABLE_STATUSES = frozenset(
    {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL"})
# FAIL at any value above 0: integrity by construction.
ZERO_TOLERANCE = frozenset({
    "table.pk_duplicate_rate",
    "table.identity_duplicate_rate",
    "relationship.orphan_rate",
})
# Reference-versus-holdout tests whose status reads a confidence bound.
COPYING_TESTS = frozenset({
    "field.value_memorization_lift",
    "field.pool_memorization_lift",
    "row.memorization_lift",
    "row.exposure_lift",
    "row.near_match_lift",
    "row.dcr_train_holdout_share",
})
NO_KEY_REASONS = frozenset({
    "no primary key declared in the relationship model",
    "no identity columns declared in the relationship model",
})
ASSOCIATION_METRICS = (
    "pair.pearson_delta",
    "pair.spearman_delta",
    "pair.cramers_v_delta",
    "pair.nmi_delta",
)
# An absolute rate that fails by chance on a narrow table, and the lift
# that separates chance from copying (catalogue pitfalls of both).
RATE_AND_LIFT = (
    ("row.exact_match_rate_nonkey", "row.memorization_lift"),
    ("row.near_match_rate", "row.near_match_lift"),
)
_SCORE_IDS = {
    "overall": "overall_score",
    "fidelity": "fidelity_score",
    "privacy": "privacy_score",
    "integrity": "integrity_score",
    "diversity": "diversity_score",
    "column_shape": "column_shape_score",
    "pair_trend": "pair_trend_score",
}

# -- rubric v1 (heuristic bands; the prompt documents and may recalibrate) ---
_UNPROVEN_SHARE = 0.5  # more than half of a cell not evaluated: grade U
_GRADE_BANDS = ((4, 0.01), (3, 0.05), (2, 0.20), (1, 0.50))  # max fail share
_GRADE_4_MAX_WARN = 0.10
_CAP_WIDESPREAD = 0.10  # share of tables with a capping FAIL
_CAP_ISOLATED, _CAP_WIDE = 2, 1
_FULL_EVIDENCE = 0.10  # at most this share not evaluated: "full" evidence
# -- prevalence of a failure signature across tables -------------------------
_SYSTEMIC_MIN_TABLES = 3
_SYSTEMIC_PREVALENCE = 0.5
_RECURRENT_MIN_TABLES = 2
_RECURRENT_PREVALENCE = 0.2
# -- how a pair's dependence failed (|association| on each side) -------------
_DEPENDENT = 0.2  # the source links the two columns
_INDEPENDENT = 0.1  # the source does not
_LOST_RATIO = 0.5  # the synthetic side keeps at most half of it
_STRONG = 0.5  # a candidate for the combination probe
_PAIR_CANDIDATES_PER_TABLE = 5
_MIN_HISTORY = 2  # evaluations of one table before a history is shown
_RECORD_DEPTH = 2  # sections live above this depth, records at or below it
_INLINE_CHARS = 4000  # a record longer than this is expanded

_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_DATASET_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,1023}")
_TABLE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$-]{0,1023}")
_FQN_PARTS = 3
EDGE_RE = re.compile(r"(?P<child>[^()]+)\((?P<cols>[^()]*)\)\s*->\s*"
                     r"(?P<ref>[^()]+)\((?P<ref_cols>[^()]*)\)")
_VERSION_RE = re.compile(
    r"(?P<base>\d[\w.]*)-(?P<branch>.+)-(?P<sha>[0-9a-f]{7})(?P<dirty>-dirty)?")
_ALIAS_TOKEN_RE = re.compile(
    r"\b(?:[A-Z]{1,3}_COL_\d{3}|[A-Z]{1,3}_TABLE|MODEL_\d{2}|"
    r"EXTERNAL_TABLE_\d{2})\b")

# Generation knobs copied from the registry row and its config snapshot:
# scalars only, never a URI, a table or a column list.
_KNOB_COLUMNS = (
    "similarity",
    "retrieval_method",
    "reference_rows_limit",
    "num_rows_requested",
    "uniqueness_mode",
    "freetext_expansion",
    "source_stats_tier",
    "seed",
    "env",
    "client_type",
    "vllm_dtype",
    "write_disposition",
    "profiler_version",
)
_KNOB_KEYS = frozenset({
    "reference_rows_limit", "source_stats", "num_rows", "similarity",
    "batch_size", "temporal_max_age_years_b1", "temporal_max_age_years_b2",
    "freetext_expansion", "pool_seed_strategy", "prompt_constraints",
    "free_text_pool_max", "pool_values_per_call", "pool_parallel_choices",
    "pool_stagnation_window", "pool_stagnation_min_novel",
    "pool_format_collapse_rounds", "source_domain_cap",
    "free_text_unique_ratio", "free_text_min_mean_len",
    "free_text_max_categories", "rag_row_docs_limit",
    "max_free_text_values_per_column", "rag_top_k", "build_rag_layer",
    "build_pool_layer", "uniqueness_mode", "driven_uniqueness_mode",
    "write_disposition", "repeat_share_tolerance", "generate_fk_relationships",
    "on_model_conflict", "fk_candidate_cap", "fk_key_sample_floor",
    "fk_key_sample_ceiling", "fk_key_sample_margin", "multi_table_mode"
})
_KNOB_VALUE_RE = re.compile(r"[A-Za-z0-9_.+-]{0,40}")
_REGISTRY_TABLE_FIELDS = (
    "role",
    "scope_mode",
    "scope_status",
    "source_drifted",
    "reference_verified",
    "reference_n",
    "exposure_n",
    "holdout_n",
    "rows_source",
    "rows_synthetic",
    "rows_expected",
    "sampled",
)
_VALIDATION_FIELDS = (
    "status",
    "valid_count",
    "dlq_count",
    "blocker_count",
    "observed_blocker_ratio",
    "repeat_share_within_tolerance",
)
# Numeric or boolean facts of a metric row's `detail` worth carrying.
_DETAIL_KEYS = frozenset({
    "d_hi", "raw_tv", "q_mass_on_p0", "outside", "invalid", "invented_rows",
    "counted_rows", "copies", "substantive", "matches", "duplicate_rows",
    "distinct_keys", "null_key_rows", "orphans", "nonnull_keys", "null_keys",
    "distinct_src", "distinct_syn", "pool_cap", "pool_target", "pool_size",
    "matched_n", "copies_r", "copies_h", "events_r", "events_h",
    "rows_synthetic", "rows_expected", "null_fraction_delta",
    "decile_cdf_delta", "distinct_rel_delta", "integrity_fail",
    "synthetic_constant", "enforced", "tail_share_source",
    "tail_share_synthetic", "closer_to_reference", "pmse", "ceiling"
})

# Why a metric did not run -> (class, kind, what closes it). First match wins.
_REASON_CLASSES: tuple[tuple[str, str, str, str], ...] = (
    ("no_key_declared", "not_applicable", r"declared in the relationship",
     "nothing to check; declare the key in the model only if one exists"),
    ("sampled_mode", "evaluation_setup", r"sampled mode cannot measure",
     "re-run the evaluation with --mode exact"),
    ("reference_not_verified", "evaluation_setup",
     r"not verified|no reference digest|reference digest mismatch",
     "pass --validation_runs_table and evaluate inside the time-travel "
     "window, so the reference sample verifies"),
    ("no_reference_panel", "evaluation_setup", r"no reference panel",
     "make reference_rows_limit known to the evaluation"),
    ("no_pool", "evaluation_setup", r"no free-text pool",
     "give the evaluation a readable freetext_pools table"),
    ("source_stats_missing", "evaluation_setup",
     r"source_stats|source_table_stats",
     "launch with a source_stats tier and a stats table"),
    ("no_expected_row_count", "evaluation_setup", r"no expected row count",
     "give validation_runs or the generation job id"),
    ("value_sampled_census", "evaluation_setup",
     r"value-sampled|Horvitz|head stratum|distinct count is not measured",
     "raise --max_shuffle_gb so the census is exact"),
    ("side_input_cap", "evaluation_setup", r"above the .* cap per panel",
     "reduce the panel or raise the cap"),
    ("failure", "failure", r"could not be (computed|set up|encoded|read)",
     "read the evaluation job's log; this is an evaluator failure"),
    ("edge_type_mismatch", "data_or_generation", r"key columns differ in type",
     "align the key types or correct the relationship model"),
    ("edge_side_unavailable", "data_or_generation",
     r"parent .*(not in the evaluation plan|not evaluated|cannot be read)|"
     r"child table is not evaluated|needs both sides|no source-side parent|"
     r"not among the parent",
     "evaluate the parent with the child, or fix the model's reference"),
    ("panel_too_small", "data_or_generation",
     r"\|R\| = |R and H hold|fewer than k \+ 1",
     "the nearest-neighbour metrics need a reference sample and a holdout of "
     "2,000 rows each: a larger reference_rows_limit, on a source at least "
     "twice that size"),
    ("no_census", "not_applicable", r"no value census",
     "not applicable: key or nested column"),
    ("no_exclusive_records", "not_applicable", r"held only by|no rare",
     "no test possible: nothing exclusive to the reference sample or to the "
     "holdout"),
    ("no_head_mask", "not_applicable", r"no source mask holds",
     "not applicable: near-unique masks (long prose)"),
    ("no_non_key_content", "not_applicable", r"every column is a key|non-key",
     "not applicable: nothing but keys to compare"),
    ("day_granular_or_time", "not_applicable", r"day-granular|no date part",
     "not applicable to this column"),
    ("null_pattern_width", "not_applicable", r"null patterns cover",
     "not applicable past 64 columns in this catalogue version"),
    ("too_few_to_measure", "not_applicable", r"fewer than",
     "not applicable at this size (k-anonymity floor or too few columns)"),
    ("constant_or_undefined", "not_applicable", r"constant|undefined",
     "not applicable: the statistic does not exist on this data"),
    ("empty_side", "data_or_generation",
     r"on the (source|synthetic) side|no (synthetic|source) rows were read|"
     r"^no (counted|matched-n|non-empty|substantive)|"
     r"no source (quantile|child|parent)|child key tuple",
     "one side holds nothing to compare"),
    ("rollup_empty", "not_applicable", r"no scored|no family score",
     "follows from its members"),
    ("ceiling_or_nonfinite", "not_applicable", r"ceiling|inf|NaN|no value",
     "the statistic has no finite value here"),
)
_REASON_OTHER = ("other", "unclassified", "read the reason text in real/")

# -- oss/ allow-list ----------------------------------------------------------
# Keys present in real/ only: real names, free text, URIs.
_REAL_ONLY = frozenset({
    "table_ref", "source_table", "landing_table", "reason_examples",
    "warning_examples", "status_reason", "run_id", "generation_job_name",
    "llm_model_uri", "project", "quality_dataset", "validation_runs_table",
    "report_json", "history_mappings", "tables_filter", "models_filter",
    "generator_version_full"
})
_ID_RE = re.compile(r"[A-Za-z0-9_.:+\[\] ()/-]{0,120}")
# Prose this script writes itself, from the evaluator's vocabulary only.
_PROSE_KEYS = frozenset(
    {"excluded_because", "grade_basis", "closes_with", "readings", "notes"})
_NAME_KEYS = frozenset(
    {"table", "column", "column_2", "edge", "relationship_model"})


class LeakError(RuntimeError):
  """A string that is not provably safe was about to be written to oss/."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def die(msg: str) -> NoReturn:
  print(f"ERROR: {msg}", file=sys.stderr)
  raise SystemExit(2)


def is_rollup(metric_id: str) -> bool:
  """True for the roll-up ids: ``table.*_score`` and every ``model.*`` id."""
  return metric_id.startswith("model.") or (metric_id.startswith("table.") and
                                            metric_id.endswith("_score"))


def _num(value: Any) -> float | None:
  if isinstance(value, bool) or value is None:
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _share(part: int, whole: int) -> float | None:
  return round(part / whole, 6) if whole else None


def _median(values: Iterable[float | None]) -> float | None:
  present = [v for v in values if v is not None]
  return round(statistics.median(present), 9) if present else None


def _iso(value: Any) -> str | None:
  if value is None:
    return None
  if isinstance(value, _dt.datetime):
    if value.tzinfo is None:
      value = value.replace(tzinfo=_dt.UTC)
    return value.astimezone(_dt.UTC).isoformat()
  return str(value)


def _detail(row: Mapping[str, Any]) -> dict[str, Any]:
  detail = row.get("detail")
  if isinstance(detail, str):
    try:
      detail = json.loads(detail)
    except ValueError:
      return {}
  return dict(detail) if isinstance(detail, Mapping) else {}


def normalize_fqn(fqn: str | None) -> str | None:
  """``project.dataset.table`` without backticks, ``:`` read as ``.``."""
  if not fqn:
    return None
  text = fqn.strip().strip("`")
  if text.count(":") == 1 and text.count(".") == 1:
    text = text.replace(":", ".")
  return text


def quoted(project: str, dataset: str, table: str | None = None) -> str:
  """A validated, backtick-quoted table or dataset path (CWE-89 guard)."""
  parts = [(project, _PROJECT_RE), (dataset, _DATASET_RE)]
  if table is not None:
    parts.append((table, _TABLE_RE))
  for text, pattern in parts:
    if not pattern.fullmatch(text or ""):
      die(f"refusing to build SQL from the identifier {text!r}")
  return "`" + ".".join(text for text, _ in parts) + "`"


def quoted_fqn(fqn: str) -> str:
  """``quoted`` for a ``project.dataset.table`` string."""
  parts = (normalize_fqn(fqn) or "").split(".")
  if len(parts) != _FQN_PARTS:
    die(f"expected project.dataset.table, got {fqn!r}")
  return quoted(parts[0], parts[1], parts[2])


# --------------------------------------------------------------------------
# catalogue (direction and target of each metric)
# --------------------------------------------------------------------------
def load_catalogue(path: Path | None) -> dict[str, dict[str, Any]]:
  """``metric_id -> entry`` from the evaluator's metrics.yaml, or ``{}``."""
  if path is None or not Path(path).exists():
    return {}
  try:
    import yaml
  except ImportError:  # pragma: no cover - env-dependent
    return {}
  data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
  return {m["id"]: m for m in data.get("metrics", [])}


def _direction(row: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
  if meta.get("direction"):
    return str(meta["direction"])
  warn, fail = _num(row.get("threshold_warn")), _num(row.get("threshold_fail"))
  if warn is not None and fail is not None and warn > fail:
    return "higher_better"
  return "lower_better"


def _gated(value: float | None, row: Mapping[str, Any],
           meta: Mapping[str, Any]) -> float | None:
  """``value`` as the catalogue grades it: a distance to the target on a
  ``target`` metric, the value itself otherwise."""
  if value is None or _direction(row, meta) != "target":
    return value
  target = _num(meta.get("target"))
  if target is None:
    target = _num(row.get("source_value")) or 0.0
  return abs(value - target)


def gate(row: Mapping[str, Any], meta: Mapping[str, Any]) -> float | None:
  """The quantity the row's status was read from."""
  value = _num(row.get("value"))
  if meta.get("uses_ci_bound") or row["metric_id"] in COPYING_TESTS:
    bound = ("ci_high"
             if _direction(row, meta) == "higher_better" else "ci_low")
    value = _num(row.get(bound))
  return _gated(value, row, meta)


def _reaches(direction: str, value: float, threshold: float) -> bool:
  return value <= threshold if direction == "higher_better" else (value
                                                                  >= threshold)


def excess(row: Mapping[str, Any], meta: Mapping[str, Any]) -> float:
  """How far past its fail threshold a row is, in units of the threshold
  (the raw value on a zero-tolerance metric). Ranks the offenders."""
  value, fail = gate(row, meta), _num(row.get("threshold_fail"))
  if value is None or fail is None:
    return 0.0
  if fail == 0:
    return value
  sign = -1.0 if _direction(row, meta) == "higher_better" else 1.0
  return round(sign * (value - fail) / abs(fail), 6)


def baseline_limited(row: Mapping[str, Any], meta: Mapping[str,
                                                           Any]) -> bool | None:
  """True when a perfect copier of the reference sample would itself reach
  the warn threshold: the reference sample, not the engine, limits the
  metric. None when the row has no baseline."""
  baseline = _gated(_num(row.get("baseline_value")), row, meta)
  warn = _num(row.get("threshold_warn"))
  if baseline is None or warn is None:
    return None
  return _reaches(_direction(row, meta), baseline, warn)


def classify_reason(reason: str | None) -> tuple[str, str, str]:
  """(class, kind, what closes it) of a not-evaluated reason."""
  text = reason or ""
  for name, kind, pattern, closes in _REASON_CLASSES:
    if re.search(pattern, text):
      return name, kind, closes
  return _REASON_OTHER


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------
@dataclasses.dataclass
class Loaded:
  """Everything the digest is computed from."""
  evaluations: list[dict[str, Any]]
  metrics: list[dict[str, Any]]
  validation_runs: list[dict[str,
                             Any]] = dataclasses.field(default_factory=list)
  ddl_columns: dict[str, list[str]] = dataclasses.field(default_factory=dict)
  notes: list[str] = dataclasses.field(default_factory=list)


def load_report_json(paths: Sequence[Path]) -> Loaded:
  """Read ``sdfb-eval report --format json`` exports (no BigQuery)."""
  evaluations, metrics = [], []
  for path in paths:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if "evaluation" not in doc or "metrics" not in doc:
      die(f"{path} is not a `sdfb-eval report --format json` export")
    evaluations.append(dict(doc["evaluation"]))
    metrics.extend(dict(row) for row in doc["metrics"])
  return Loaded(
      evaluations,
      metrics,
      notes=["read from report JSON exports, not from BigQuery"])


def registry_sql(project: str, dataset: str, *, ids: Sequence[str],
                 window: tuple[Any, Any] | None) -> tuple[str, dict[str, Any]]:
  """The registry read: the last event of each evaluation in scope."""
  where, params = [], {}
  if ids:
    where.append("evaluation_id IN UNNEST(@ids)")
    params["ids"] = list(ids)
  if window:
    where.append("evaluated_at >= @from_ts AND evaluated_at < @to_ts")
    params["from_ts"], params["to_ts"] = window
  sql = (f"SELECT * FROM {quoted(project, dataset, 'evaluation_latest')}" +
         (" WHERE " + " AND ".join(where) if where else ""))
  return sql, params


_METRIC_COLUMNS = (
    "evaluation_id, table_name, landing_table, source_table, level, family, "
    "metric_id, column_name, column_name_2, column_kind, edge, value, "
    "source_value, synthetic_value, baseline_value, score, status, "
    "threshold_warn, threshold_fail, noise_floor, noise_floor_method, ci_low, "
    "ci_high, n_source, n_synthetic, method, sample_rate, detail")


def metrics_sql(project: str, dataset: str, ids: Sequence[str], lo: Any,
                hi: Any) -> tuple[str, dict[str, Any]]:
  """The metric rows of the selected evaluations, pruned by partition."""
  sql = (f"SELECT {_METRIC_COLUMNS} "
         f"FROM {quoted(project, dataset, 'evaluation_metrics')} "
         "WHERE evaluation_id IN UNNEST(@ids) "
         "AND evaluated_at >= @lo AND evaluated_at <= @hi")
  return sql, {"ids": list(ids), "lo": lo, "hi": hi}


def validation_runs_sql(table_fqn: str,
                        run_ids: Sequence[str]) -> tuple[str, dict[str, Any]]:
  """The generator's own verdict rows for the evaluated runs."""
  sql = (f"SELECT * FROM {quoted_fqn(table_fqn)} "
         "WHERE run_id IN UNNEST(@run_ids)")
  return sql, {"run_ids": list(run_ids)}


def ddl_columns_sql(project: str, dataset: str,
                    tables: Sequence[str]) -> tuple[str, dict[str, Any]]:
  """Column order of the source tables, for alias numbering (ADR 0029)."""
  path = quoted(project, dataset)[:-1] + ".INFORMATION_SCHEMA.COLUMNS`"
  sql = (f"SELECT table_name, column_name FROM {path} "
         "WHERE table_name IN UNNEST(@tables) "
         "ORDER BY table_name, ordinal_position")
  return sql, {"tables": list(tables)}


def run_query(client: Any, sql: str,
              params: Mapping[str, Any]) -> list[dict[str, Any]]:
  """Run one parameterized query and return plain dict rows."""
  from google.cloud import bigquery

  typed = []
  for name, value in params.items():
    if isinstance(value, (list, tuple)):
      typed.append(bigquery.ArrayQueryParameter(name, "STRING", list(value)))
    elif isinstance(value, _dt.datetime):
      typed.append(bigquery.ScalarQueryParameter(name, "TIMESTAMP", value))
    else:
      typed.append(bigquery.ScalarQueryParameter(name, "STRING", value))
  job = client.query(
      sql, job_config=bigquery.QueryJobConfig(query_parameters=typed))
  return [dict(row.items()) for row in job.result()]


def bq_client(project: str) -> Any:
  try:
    from google.cloud import bigquery
  except ImportError as e:  # pragma: no cover - env-dependent
    die(f"missing GCP client libs ({e}); or pass --report-json")
  return bigquery.Client(project=project)


def load_bq(client: Any,
            args: argparse.Namespace,
            query: Any = run_query) -> Loaded:
  """Read the registry, the metric rows and (optionally) validation_runs."""
  loaded = Loaded([], [])
  sql, params = registry_sql(
      args.project,
      args.quality_dataset,
      ids=args.evaluation_id,
      window=_window(args))
  loaded.evaluations = query(client, sql, params)
  keep = select(loaded.evaluations, _criteria(args))[0]
  if not keep:
    return loaded
  stamps = [e["evaluated_at"] for e in keep if e.get("evaluated_at")]
  if not stamps:
    die("the registry rows carry no evaluated_at: cannot prune the read")
  sql, params = metrics_sql(args.project, args.quality_dataset,
                            [e["evaluation_id"] for e in keep], min(stamps),
                            max(stamps))
  loaded.metrics = query(client, sql, params)
  _load_validation_runs(client, args, keep, loaded, query)
  _load_ddl_columns(client, keep, loaded, query)
  return loaded


def _load_validation_runs(client: Any, args: argparse.Namespace,
                          keep: Sequence[Mapping[str, Any]], loaded: Loaded,
                          query: Any) -> None:
  if not args.validation_runs_table:
    return
  run_ids = sorted({
      t.get("run_id")
      for e in keep
      for t in (e.get("tables") or [])
      if t.get("run_id")
  })
  if not run_ids:
    loaded.notes.append("no run_id in the registry rows: validation_runs "
                        "was not read")
    return
  try:
    sql, params = validation_runs_sql(args.validation_runs_table, run_ids)
    loaded.validation_runs = query(client, sql, params)
  except Exception as e:  # pylint: disable=broad-exception-caught
    loaded.notes.append(
        f"validation_runs not read ({type(e).__name__}): the table cards "
        "carry no generator verdict")


def _load_ddl_columns(client: Any, keep: Sequence[Mapping[str, Any]],
                      loaded: Loaded, query: Any) -> None:
  by_dataset: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
  for evaluation in keep:
    for table in evaluation.get("tables") or []:
      parts = (normalize_fqn(table.get("source_table")) or "").split(".")
      if len(parts) == _FQN_PARTS:
        by_dataset[(parts[0], parts[1])].add(parts[2])
  for (project, dataset), tables in sorted(by_dataset.items()):
    try:
      sql, params = ddl_columns_sql(project, dataset, sorted(tables))
      rows = query(client, sql, params)
    except Exception as e:  # pylint: disable=broad-exception-caught
      loaded.notes.append(
          f"column order not read for one dataset ({type(e).__name__}): its "
          "new aliases are numbered by column name, not by DDL position")
      continue
    for row in rows:
      loaded.ddl_columns.setdefault(f"{project}.{dataset}.{row['table_name']}",
                                    []).append(row["column_name"])


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def _window(args: argparse.Namespace) -> tuple[Any, Any] | None:
  if args.since_days is not None:
    now = _dt.datetime.now(_dt.UTC)
    return now - _dt.timedelta(days=args.since_days), now
  if args.from_ts or args.to_ts:
    lo = _aware(args.from_ts or "1970-01-01T00:00:00+00:00")
    hi = _aware(args.to_ts) if args.to_ts else _dt.datetime.now(_dt.UTC)
    return lo, hi
  return None


def _aware(text: str) -> _dt.datetime:
  stamp = _dt.datetime.fromisoformat(text)
  return stamp if stamp.tzinfo else stamp.replace(tzinfo=_dt.UTC)


def _criteria(args: argparse.Namespace) -> dict[str, Any]:
  window = _window(args)
  return {
      "evaluation_ids": sorted(args.evaluation_id),
      "from": _iso(window[0]) if window else None,
      "to": _iso(window[1]) if window else None,
      "engines": sorted(args.engine),
      "models_filter": sorted(args.relationship_model),
      "tables_filter": sorted(args.table),
      "latest_per": args.latest_per,
  }


def select(
    evaluations: Sequence[Mapping[str, Any]], criteria: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
  """Split the registry rows into (read, left out with the reason)."""
  keep, excluded = [], []
  for evaluation in sorted(evaluations, key=lambda e: str(e["evaluation_id"])):
    reason = _exclusion(evaluation, criteria)
    if reason is None:
      keep.append(dict(evaluation))
    elif reason:
      excluded.append({
          "evaluation_id": evaluation["evaluation_id"],
          "status": evaluation.get("status"),
          "excluded_because": reason,
      })
  return keep, excluded


def _exclusion(evaluation: Mapping[str, Any],
               criteria: Mapping[str, Any]) -> str | None:
  """None to read it, "" to drop it silently (outside the filters), else
  the reason it cannot be read."""
  ids = criteria.get("evaluation_ids") or []
  if ids and evaluation["evaluation_id"] not in ids:
    return ""
  engines = criteria.get("engines") or []
  if engines and evaluation.get("engine") not in engines:
    return ""
  models = criteria.get("models_filter") or []
  if models and evaluation.get("relationship_model") not in models:
    return ""
  status = evaluation.get("status")
  if status == "RUNNING":
    return "no terminal event: still running, or a launch that died unseen"
  if status not in READABLE_STATUSES:
    return f"status {status}: nothing to read (the evaluation, not the data)"
  return None


# --------------------------------------------------------------------------
# slices: one evaluated table of one evaluation
# --------------------------------------------------------------------------
@dataclasses.dataclass
class Slice:
  """One table as one evaluation measured it."""
  evaluation_id: str
  evaluated_at: str
  engine: str
  table: str
  table_ref: str
  registry: dict[str, Any]
  rows: list[dict[str, Any]] = dataclasses.field(default_factory=list)
  rollups: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)

  @property
  def key(self) -> tuple[str, str]:
    return self.table_ref, self.engine


def engine_effective(evaluation: Mapping[str, Any],
                     backends: Mapping[str, str]) -> str:
  """The engine as it actually generated: ``b2_library`` says which backend
  only when the operator states it (BigQuery does not record it)."""
  engine = str(evaluation.get("engine") or "unknown")
  if engine in backends:
    return f"{engine}[{backends[engine]}]"
  if engine == "b2_library":
    return "b2_library[backend unverified]"
  return engine


def build_slices(
    keep: Sequence[Mapping[str, Any]],
    metrics: Iterable[Mapping[str, Any]],
    backends: Mapping[str, str],
    tables_filter: Sequence[str] = ()) -> list[Slice]:
  """Group the metric rows by (evaluation, table)."""
  by_id = {e["evaluation_id"]: e for e in keep}
  slices: dict[tuple[str, str], Slice] = {}
  for row in metrics:
    evaluation = by_id.get(row.get("evaluation_id"))
    name = row.get("table_name")
    if evaluation is None or not name or name == "__model__":
      continue
    if tables_filter and name not in tables_filter:
      continue
    key = (evaluation["evaluation_id"], name)
    if key not in slices:
      slices[key] = _new_slice(evaluation, name, row, backends)
    parsed = {**row, "detail": _detail(row)}
    if is_rollup(parsed["metric_id"]):
      slices[key].rollups[parsed["metric_id"]] = parsed
    else:
      slices[key].rows.append(parsed)
  return [slices[key] for key in sorted(slices)]


def _new_slice(evaluation: Mapping[str, Any], name: str, row: Mapping[str, Any],
               backends: Mapping[str, str]) -> Slice:
  registry = next((dict(t)
                   for t in (evaluation.get("tables") or [])
                   if t.get("name") == name), {})
  ref = normalize_fqn(registry.get("source_table") or row.get("source_table"))
  return Slice(
      evaluation_id=evaluation["evaluation_id"],
      evaluated_at=_iso(evaluation.get("evaluated_at")) or "",
      engine=engine_effective(evaluation, backends),
      table=name,
      table_ref=ref or name,
      registry=registry)


def current(slices: Sequence[Slice], latest_per: str) -> list[Slice]:
  """The slices the status is read from: the latest evaluation of each
  (table, engine), or every slice with ``latest_per == "none"``."""
  if latest_per == "none":
    return list(slices)
  latest: dict[tuple[str, str], Slice] = {}
  for item in slices:
    held = latest.get(item.key)
    if held is None or (item.evaluated_at,
                        item.evaluation_id) > (held.evaluated_at,
                                               held.evaluation_id):
      latest[item.key] = item
  return [latest[key] for key in sorted(latest)]


# --------------------------------------------------------------------------
# readings of one row
# --------------------------------------------------------------------------
def inapplicable_key(row: Mapping[str, Any]) -> bool:
  """A key check on a table that declares no such key (ADR 0042)."""
  return (row["metric_id"]
          in ("table.pk_duplicate_rate", "table.identity_duplicate_rate") and
          row["status"] == "not_evaluated" and
          row["detail"].get("reason") in NO_KEY_REASONS)


def downgraded(row: Mapping[str, Any]) -> bool:
  """A crossing that sampling noise explains (stored as a pass)."""
  return row["detail"].get("noise_downgraded_from") in ("warn", "fail")


def _counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
  counts = dict.fromkeys(STATUSES, 0)
  for row in rows:
    status = row["status"] if row["status"] in counts else "not_evaluated"
    counts[status] += 1
  return counts


def _capping(row: Mapping[str, Any]) -> bool:
  return row["status"] == "fail" and (row["metric_id"] in ZERO_TOLERANCE or
                                      row["metric_id"] in COPYING_TESTS)


# --------------------------------------------------------------------------
# digest sections
# --------------------------------------------------------------------------
def headline(evaluation: Mapping[str, Any],
             slices: Sequence[Slice]) -> dict[str, Any]:
  """The run in the GUI's own terms (``interpret.ts::headline``)."""
  rows = [r for s in slices for r in s.rows]
  counts = _counts(rows)
  fails = [r for r in rows if r["status"] == "fail"]
  by_family = collections.Counter(r["family"] for r in fails)
  by_level = collections.Counter(r["level"] for r in fails)
  top_family = min(by_family, key=lambda f: (-by_family[f], f), default=None)
  top_level = min(by_level, key=lambda lv: (-by_level[lv], lv), default=None)
  return {
      "overall_score":
          _num(evaluation.get("overall_score")),
      "measured":
          len(rows),
      **counts,
      "not_applicable_keys":
          sum(inapplicable_key(r) for r in rows),
      "noise_downgraded":
          sum(downgraded(r) for r in rows),
      "privacy_not_evaluated":
          sum(r["family"] == "privacy" and r["status"] == "not_evaluated"
              for r in rows),
      "top_fail_family":
          top_family,
      "top_fail_family_fails":
          by_family.get(top_family, 0),
      "top_fail_level":
          top_level,
      "fail_by_level": {
          lv: by_level.get(lv, 0) for lv in LEVELS
      },
      "fail_by_family": {
          f: by_family.get(f, 0) for f in FAMILIES
      },
  }


def grade(fail_share: float | None, warn_share: float | None,
          unproven_share: float | None,
          capped_share: float | None) -> tuple[str, str]:
  """Rubric v1: a cell's grade (4 best, 0 worst, U unproven) and its basis."""
  if fail_share is None or (unproven_share or 0.0) > _UNPROVEN_SHARE:
    return "U", "more than half not evaluated, or nothing graded"
  value, basis = 0, f"fail share {fail_share:.3f}"
  for band, limit in _GRADE_BANDS:
    if fail_share <= limit:
      value = band
      break
  if value == _GRADE_BANDS[0][0] and (warn_share or 0.0) > _GRADE_4_MAX_WARN:
    value, basis = 3, basis + f", warn share {warn_share:.3f}"
  if capped_share:
    cap = _CAP_WIDE if capped_share >= _CAP_WIDESPREAD else _CAP_ISOLATED
    if value > cap:
      value = cap
      basis += (f"; capped at {cap}: enforced-key or copying FAIL on "
                f"{capped_share:.3f} of the tables")
  return str(value), basis


def _tables(rows: Iterable[tuple[Slice, Mapping[str, Any]]],
            wanted: Any) -> int:
  return len({(s.evaluation_id, s.table) for s, r in rows if wanted(r)})


def cells(slices: Sequence[Slice]) -> list[dict[str, Any]]:
  """Engine x level x family: the status mix and its rubric grade."""
  groups: dict[tuple[str, str, str], list[tuple[Slice, dict]]] = (
      collections.defaultdict(list))
  for item in slices:
    for row in item.rows:
      groups[(item.engine, row["level"], row["family"])].append((item, row))
  out = []
  for (engine, level, family), members in sorted(groups.items()):
    rows = [r for _, r in members]
    counts = _counts(rows)
    graded = sum(counts[s] for s in GRADED)
    not_applicable = sum(inapplicable_key(r) for r in rows)
    measured = _tables(members, lambda r: r["status"] in GRADED)
    capped = _tables(members, _capping)
    unproven = _share(counts["not_evaluated"] - not_applicable,
                      len(rows) - not_applicable)
    value, basis = grade(
        _share(counts["fail"], graded), _share(counts["warn"], graded),
        unproven, _share(capped, measured))
    out.append({
        "engine": engine,
        "level": level,
        "family": family,
        "rows": len(rows),
        **counts,
        "not_applicable_keys": not_applicable,
        "noise_downgraded": sum(downgraded(r) for r in rows),
        "fail_share": _share(counts["fail"], graded),
        "warn_share": _share(counts["warn"], graded),
        "unproven_share": unproven,
        "tables_measured": measured,
        "tables_with_fail": _tables(members, lambda r: r["status"] == "fail"),
        "tables_with_capping_fail": capped,
        "grade": value,
        "evidence": _evidence(value, unproven),
        "grade_basis": basis,
    })
  return out


def _evidence(value: str, unproven_share: float | None) -> str:
  """How much of a cell the grade rests on: ``full`` or ``partial``."""
  if value == "U":
    return "none"
  return "full" if (unproven_share or 0.0) <= _FULL_EVIDENCE else "partial"


def _scope(tables_measured: int, tables_with_fail: int) -> str:
  if not tables_with_fail:
    return "clean"
  prevalence = tables_with_fail / tables_measured if tables_measured else 0.0
  if (tables_measured >= _SYSTEMIC_MIN_TABLES and
      prevalence >= _SYSTEMIC_PREVALENCE):
    return "systemic"
  if (tables_with_fail >= _RECURRENT_MIN_TABLES and
      prevalence >= _RECURRENT_PREVALENCE):
    return "recurrent"
  return "local"


def signatures(slices: Sequence[Slice],
               catalogue: Mapping[str, Any]) -> list[dict[str, Any]]:
  """Engine x metric x column kind: how often it fails, across how many
  tables, and whether the reference sample already explains it."""
  groups: dict[tuple[str, str, str], list[tuple[Slice, dict]]] = (
      collections.defaultdict(list))
  for item in slices:
    for row in item.rows:
      kind = row.get("column_kind") or "-"
      groups[(item.engine, row["metric_id"], kind)].append((item, row))
  out = []
  for (engine, metric_id, kind), members in sorted(groups.items()):
    meta = catalogue.get(metric_id, {})
    rows = [r for _, r in members]
    counts = _counts(rows)
    graded = sum(counts[s] for s in GRADED)
    failing = [r for r in rows if r["status"] == "fail"]
    limited = [baseline_limited(r, meta) for r in failing]
    known = [flag for flag in limited if flag is not None]
    measured = _tables(members, lambda r: r["status"] in GRADED)
    with_fail = _tables(members, lambda r: r["status"] == "fail")
    out.append({
        "engine":
            engine,
        "metric_id":
            metric_id,
        "column_kind":
            kind,
        "level":
            rows[0]["level"],
        "family":
            rows[0]["family"],
        "rows":
            len(rows),
        **counts,
        "noise_downgraded":
            sum(downgraded(r) for r in rows),
        "fail_share":
            _share(counts["fail"], graded),
        "tables_measured":
            measured,
        "tables_with_fail":
            with_fail,
        "prevalence":
            _share(with_fail, measured),
        "scope":
            _scope(measured, with_fail),
        "median_value":
            _median(
                _num(r.get("value")) for r in rows if r["status"] in GRADED),
        "median_baseline":
            _median(
                _num(r.get("baseline_value"))
                for r in rows
                if r["status"] in GRADED),
        "median_fail_value":
            _median(_num(r.get("value")) for r in failing),
        "fails_with_baseline":
            len(known),
        "baseline_limited_share":
            _share(sum(known), len(known)),
        "threshold_warn":
            _num(rows[0].get("threshold_warn")),
        "threshold_fail":
            _num(rows[0].get("threshold_fail")),
        "direction":
            _direction(rows[0], meta),
    })
  return out


def _clean(signature: Mapping[str, Any]) -> bool:
  """No crossing to read: no row of the signature warned or failed (why a
  row did not run is the ``not_evaluated`` section's)."""
  return not (signature["fail"] or signature["warn"])


def _clean_by_engine(found: Sequence[Mapping[str, Any]]) -> list[dict]:
  """The signatures with no crossing, as ``metric_id:column_kind`` per
  engine: what an engine gets right, named but not detailed."""
  by_engine: dict[str, list[str]] = collections.defaultdict(list)
  for signature in found:
    if _clean(signature) and signature["pass"]:
      by_engine[signature["engine"]].append(
          f"{signature['metric_id']}:{signature['column_kind']}")
  return [{
      "engine": engine,
      "clean": sorted(names)
  } for engine, names in sorted(by_engine.items())]


def _record(item: Slice, row: Mapping[str, Any],
            meta: Mapping[str, Any]) -> dict[str, Any]:
  detail = {
      key: value
      for key, value in sorted(row["detail"].items())
      if key in _DETAIL_KEYS and isinstance(value, (int, float, bool))
  }
  return {
      "evaluation_id": item.evaluation_id,
      "engine": item.engine,
      "table": item.table,
      "table_ref": item.table_ref,
      "column": row.get("column_name"),
      "column_2": row.get("column_name_2"),
      "edge": row.get("edge"),
      "metric_id": row["metric_id"],
      "level": row["level"],
      "family": row["family"],
      "column_kind": row.get("column_kind"),
      "status": row["status"],
      "value": _num(row.get("value")),
      "source_value": _num(row.get("source_value")),
      "synthetic_value": _num(row.get("synthetic_value")),
      "baseline_value": _num(row.get("baseline_value")),
      "noise_floor": _num(row.get("noise_floor")),
      "ci_low": _num(row.get("ci_low")),
      "ci_high": _num(row.get("ci_high")),
      "threshold_warn": _num(row.get("threshold_warn")),
      "threshold_fail": _num(row.get("threshold_fail")),
      "score": _num(row.get("score")),
      "excess": excess(row, meta),
      "baseline_limited": baseline_limited(row, meta),
      "n_source": row.get("n_source"),
      "n_synthetic": row.get("n_synthetic"),
      "method": row.get("method"),
      "detail": detail,
  }


def offenders(slices: Sequence[Slice], catalogue: Mapping[str, Any], top_k: int,
              per_table: int) -> dict[str, list[dict[str, Any]]]:
  """The worst FAIL rows of each level, at most ``per_table`` a table."""
  by_level: dict[str, list[dict[str, Any]]] = {lv: [] for lv in LEVELS}
  for item in slices:
    for row in item.rows:
      if row["status"] == "fail" and row["level"] in by_level:
        by_level[row["level"]].append(
            _record(item, row, catalogue.get(row["metric_id"], {})))
  out = {}
  for level, records in by_level.items():
    records.sort(key=_offender_order)
    taken: collections.Counter = collections.Counter()
    kept = []
    for record in records:
      slot = (record["evaluation_id"], record["table"])
      if taken[slot] < per_table and len(kept) < top_k:
        taken[slot] += 1
        kept.append(record)
    out[level] = kept
  return out


def _offender_order(record: Mapping[str, Any]) -> tuple:
  return (-int(record["metric_id"] in ZERO_TOLERANCE),
          -int(record["metric_id"] in COPYING_TESTS), -record["excess"],
          record["evaluation_id"], record["table"], record["metric_id"],
          record["column"] or "", record["column_2"] or "", record["edge"] or
          "")


def table_cards(slices: Sequence[Slice],
                validation: Mapping[str, Mapping[str, Any]]) -> list[dict]:
  """One card per evaluated table: its scores, where it fails, its scope."""
  cards = []
  for item in slices:
    fails = [r for r in item.rows if r["status"] == "fail"]
    scores = {
        name: _num((item.rollups.get(f"table.{suffix}") or {}).get("value"))
        for name, suffix in _SCORE_IDS.items()
    }
    by_level = collections.Counter(r["level"] for r in fails)
    card = {
        "evaluation_id":
            item.evaluation_id,
        "evaluated_at":
            item.evaluated_at,
        "engine":
            item.engine,
        "table":
            item.table,
        "table_ref":
            item.table_ref,
        "source_table":
            item.registry.get("source_table"),
        "landing_table":
            item.registry.get("landing_table"),
        "run_id":
            item.registry.get("run_id"),
        **{
            f: item.registry.get(f) for f in _REGISTRY_TABLE_FIELDS
        },
        "scores":
            scores,
        **_counts(item.rows),
        "fail_by_level": {
            lv: by_level.get(lv, 0) for lv in LEVELS
        },
        "zero_tolerance_fails":
            sorted({
                r["metric_id"]
                for r in fails
                if r["metric_id"] in ZERO_TOLERANCE
            }),
        "copying_fails":
            sorted({
                r["metric_id"] for r in fails if r["metric_id"] in COPYING_TESTS
            }),
        "validation":
            _validation_card(validation.get(item.registry.get("run_id") or "")),
    }
    cards.append(card)
  cards.sort(key=lambda c: (c["scores"]["overall"] is None, c["scores"][
      "overall"] or 0.0, c["evaluation_id"], c["table"]))
  return cards


def _validation_card(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
  if not row:
    return None
  by_rule = row.get("dlq_by_rule")
  if isinstance(by_rule, str):
    try:
      by_rule = json.loads(by_rule)
    except ValueError:
      by_rule = None
  rules = {
      str(rule): count
      for rule, count in sorted((by_rule or {}).items())
      if isinstance(count, (int, float)) and re.fullmatch(r"[a-z_.]+", rule)
  } if isinstance(by_rule, Mapping) else {}
  excluded = row.get("excluded_blocker_rules") or []
  if isinstance(excluded, str):
    excluded = [part for part in re.split(r"[,\s]+", excluded) if part]
  return {
      **{
          f: row.get(f) for f in _VALIDATION_FIELDS
      },
      "dlq_by_rule":
          rules,
      "excluded_blocker_rules":
          sorted(
              str(rule)
              for rule in excluded
              if re.fullmatch(r"[a-z_.]+", str(rule))),
  }


def _dependence(source: float | None, synthetic: float | None,
                status: str) -> str:
  """How a graded association row reads: kept, lost, invented or shifted."""
  if status == "pass" or source is None or synthetic is None:
    return "kept" if status == "pass" else "shifted"
  if abs(source) >= _DEPENDENT and abs(synthetic) <= _LOST_RATIO * abs(source):
    return "lost"
  if abs(source) < _INDEPENDENT and abs(synthetic) >= _DEPENDENT:
    return "invented"
  return "shifted"


def pairs(slices: Sequence[Slice]) -> dict[str, Any]:
  """Column-pair dependence: where the source links two columns, does the
  synthetic side keep the link? Independent per-column sampling loses every
  one and invents none; this section makes that pattern countable."""
  groups: dict[tuple[str, str, str], collections.Counter] = (
      collections.defaultdict(collections.Counter))
  candidates = []
  for item in slices:
    kinds = {
        r["column_name"]: r.get("column_kind")
        for r in item.rows
        if r.get("column_name") and not r.get("column_name_2") and
        r.get("column_kind")
    }
    strong = []
    for row in item.rows:
      if (row["metric_id"] not in ASSOCIATION_METRICS or
          row["status"] not in GRADED):
        continue
      source = _num(row.get("source_value"))
      synthetic = _num(row.get("synthetic_value"))
      kind_pair = " x ".join(
          sorted((kinds.get(row.get("column_name")) or
                  "unknown", kinds.get(row.get("column_name_2")) or "unknown")))
      tally = groups[(item.engine, row["metric_id"], kind_pair)]
      tally["graded"] += 1
      tally[row["status"]] += 1
      tally[_dependence(source, synthetic, row["status"])] += 1
      if source is not None and abs(source) >= _DEPENDENT:
        tally["source_dependent"] += 1
        tally["source_dependent_fail"] += row["status"] == "fail"
      if source is not None and abs(source) < _INDEPENDENT:
        tally["source_independent"] += 1
        tally["source_independent_fail"] += row["status"] == "fail"
      if source is not None and abs(source) >= _STRONG:
        strong.append((item, row, kind_pair))
    strong.sort(key=lambda entry: (-abs(_num(entry[1][
        "source_value"]) or 0.0), entry[1]["metric_id"], entry[1]["column_name"]
                                   or "", entry[1]["column_name_2"] or ""))
    seen: set[tuple[str, str]] = set()
    for entry_item, row, kind_pair in strong:
      pair = (row.get("column_name") or "", row.get("column_name_2") or "")
      if pair in seen or len(seen) >= _PAIR_CANDIDATES_PER_TABLE:
        continue
      seen.add(pair)
      candidates.append({
          "evaluation_id":
              entry_item.evaluation_id,
          "engine":
              entry_item.engine,
          "table":
              entry_item.table,
          "table_ref":
              entry_item.table_ref,
          "column":
              pair[0],
          "column_2":
              pair[1],
          "kind_pair":
              kind_pair,
          "metric_id":
              row["metric_id"],
          "status":
              row["status"],
          "source_value":
              _num(row.get("source_value")),
          "synthetic_value":
              _num(row.get("synthetic_value")),
          "reading":
              _dependence(
                  _num(row.get("source_value")),
                  _num(row.get("synthetic_value")), row["status"]),
      })
  by_kind = []
  for (engine, metric_id, kind_pair), tally in sorted(groups.items()):
    by_kind.append({
        "engine":
            engine,
        "metric_id":
            metric_id,
        "kind_pair":
            kind_pair,
        **{
            key: tally.get(key, 0)
            for key in ("graded", "pass", "warn", "fail", "kept", "lost",
                        "invented", "shifted", "source_dependent",
                        "source_dependent_fail", "source_independent",
                        "source_independent_fail")
        },
        "dependent_fail_share":
            _share(
                tally.get("source_dependent_fail", 0),
                tally.get("source_dependent", 0)),
        "independent_fail_share":
            _share(
                tally.get("source_independent_fail", 0),
                tally.get("source_independent", 0)),
    })
  return {
      "thresholds": {
          "dependent": _DEPENDENT,
          "independent": _INDEPENDENT,
          "lost_ratio": _LOST_RATIO,
          "strong": _STRONG
      },
      "by_kind": by_kind,
      "strong_source_pairs": candidates,
  }


def edges(slices: Sequence[Slice]) -> list[dict[str, Any]]:
  """One card per foreign-key edge (the row's table is the CHILD): each
  relationship metric's value and status, and whether the edge is
  enforced or only documented."""
  out = []
  for item in slices:
    by_edge: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in item.rows:
      if row["level"] == "relationship" and row.get("edge"):
        by_edge[row["edge"]].append(row)
    for label, rows in sorted(by_edge.items()):
      enforced = next(
          (r["detail"]["enforced"] for r in rows if "enforced" in r["detail"]),
          None)
      out.append({
          "evaluation_id": item.evaluation_id,
          "engine": item.engine,
          "table": item.table,
          "table_ref": item.table_ref,
          "edge": label,
          "enforced": enforced,
          "metrics": {
              r["metric_id"].split(".", 1)[1]: {
                  "value": _num(r.get("value")),
                  "status": r["status"]
              } for r in sorted(rows, key=lambda r: r["metric_id"])
          },
      })
  return out


def privacy_readings(slices: Sequence[Slice]) -> list[dict[str, Any]]:
  """Per table: an absolute match rate read next to its lift, and whether
  the privacy block ran at all."""
  out = []
  for item in slices:
    by_id = {r["metric_id"]: r["status"] for r in item.rows}
    privacy = [r for r in item.rows if r["family"] == "privacy"]
    readings = []
    for rate, lift in RATE_AND_LIFT:
      if by_id.get(rate) != "fail":
        continue
      if by_id.get(lift) == "pass":
        readings.append(f"{rate} fails with {lift} passing: chance-level "
                        "collisions on a narrow table, not evidence of copying")
      elif by_id.get(lift) in ("warn", "fail"):
        readings.append(f"{rate} and {lift} both cross: copying")
      else:
        readings.append(f"{rate} fails and {lift} did not run: unresolved")
    copying = sorted(
        r["metric_id"]
        for r in privacy
        if r["status"] == "fail" and r["metric_id"] in COPYING_TESTS)
    not_run = sorted(r["metric_id"]
                     for r in privacy
                     if r["status"] == "not_evaluated" and
                     r["level"] == "row" and r["metric_id"] in COPYING_TESTS)
    if readings or copying or not_run:
      out.append({
          "evaluation_id":
              item.evaluation_id,
          "engine":
              item.engine,
          "table":
              item.table,
          "table_ref":
              item.table_ref,
          "privacy_rows":
              len(privacy),
          "privacy_not_evaluated":
              sum(r["status"] == "not_evaluated" for r in privacy),
          "copying_tests_failing":
              copying,
          "row_copying_tests_not_run":
              not_run,
          "readings":
              readings,
      })
  return out


def not_evaluated(slices: Sequence[Slice]) -> list[dict[str, Any]]:
  """Why metrics did not run, by class, with what would close each gap."""
  groups: dict[tuple[str, str], dict[str, Any]] = {}
  for item in slices:
    for row in item.rows:
      if row["status"] != "not_evaluated":
        continue
      reason = row["detail"].get("reason")
      name, kind, closes = classify_reason(reason)
      entry = groups.setdefault(
          (name, row["family"]), {
              "reason_class": name,
              "kind": kind,
              "family": row["family"],
              "closes_with": closes,
              "rows": 0,
              "tables": set(),
              "metrics": collections.Counter(),
              "examples": collections.Counter(),
          })
      entry["rows"] += 1
      entry["tables"].add((item.evaluation_id, item.table))
      entry["metrics"][row["metric_id"]] += 1
      entry["examples"][str(reason)] += 1
  out = []
  for key in sorted(groups, key=lambda k: (-groups[k]["rows"], k)):
    entry = groups[key]
    out.append({
        "reason_class":
            entry["reason_class"],
        "kind":
            entry["kind"],
        "family":
            entry["family"],
        "closes_with":
            entry["closes_with"],
        "rows":
            entry["rows"],
        "tables":
            len(entry["tables"]),
        "metric_ids":
            sorted(entry["metrics"]),
        "reason_examples": [
            text for text, _ in sorted(
                entry["examples"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        ],
    })
  return out


def cross_run(slices: Sequence[Slice]) -> dict[str, Any]:
  """What changed between evaluations of the same table: its score history
  per engine, the status flips from the oldest to the newest, and the
  engines side by side where a table was generated by more than one."""
  by_key: dict[tuple[str, str], list[Slice]] = collections.defaultdict(list)
  for item in slices:
    by_key[item.key].append(item)
  history, flips = [], []
  for (ref, engine), items in sorted(by_key.items()):
    items.sort(key=lambda s: (s.evaluated_at, s.evaluation_id))
    if len(items) < _MIN_HISTORY:
      continue
    history.append({
        "table": items[-1].table,
        "table_ref": ref,
        "engine": engine,
        "evaluations": [_history_point(item) for item in items],
    })
    flips.append({
        "table": items[-1].table,
        "table_ref": ref,
        "engine": engine,
        "from_evaluation_id": items[0].evaluation_id,
        "to_evaluation_id": items[-1].evaluation_id,
        **_flips(items[0], items[-1]),
    })
  by_table: dict[str, list[Slice]] = collections.defaultdict(list)
  for item in current(slices, "table"):
    by_table[item.table_ref].append(item)
  side_by_side = [{
      "table":
          items[0].table,
      "table_ref":
          ref,
      "engines": [
          _history_point(item) | {
              "engine": item.engine
          }
          for item in sorted(items, key=lambda s: s.engine)
      ],
  }
                  for ref, items in sorted(by_table.items())
                  if len({i.engine for i in items}) > 1]
  return {
      "history": history,
      "status_flips": flips,
      "engines_side_by_side": side_by_side,
  }


def _history_point(item: Slice) -> dict[str, Any]:
  return {
      "evaluation_id": item.evaluation_id,
      "evaluated_at": item.evaluated_at,
      "scores": {
          name: _num((item.rollups.get(f"table.{suffix}") or {}).get("value"))
          for name, suffix in _SCORE_IDS.items()
      },
      "fail": sum(r["status"] == "fail" for r in item.rows),
      "graded": sum(r["status"] in GRADED for r in item.rows),
  }


def _row_key(row: Mapping[str, Any]) -> tuple:
  return (row["metric_id"], row.get("column_name") or
          "", row.get("column_name_2") or "", row.get("edge") or "")


def _flips(old: Slice, new: Slice) -> dict[str, Any]:
  before = {_row_key(r): r["status"] for r in old.rows}
  regressed: collections.Counter = collections.Counter()
  improved: collections.Counter = collections.Counter()
  for row in new.rows:
    was = before.get(_row_key(row))
    if was not in GRADED or row["status"] not in GRADED:
      continue
    if was != "fail" and row["status"] == "fail":
      regressed[row["metric_id"]] += 1
    elif was == "fail" and row["status"] != "fail":
      improved[row["metric_id"]] += 1
  return {
      "regressed_rows": sum(regressed.values()),
      "improved_rows": sum(improved.values()),
      "regressed_by_metric": dict(sorted(regressed.items())),
      "improved_by_metric": dict(sorted(improved.items())),
  }


# --------------------------------------------------------------------------
# the selection block
# --------------------------------------------------------------------------
def _knobs(evaluation: Mapping[str, Any]) -> dict[str, Any]:
  """Scalar generation knobs: the registry's typed columns, then the
  allow-listed leaves of the config snapshot (never a URI or a name)."""
  knobs: dict[str, Any] = {}
  for column in _KNOB_COLUMNS:
    _keep_knob(knobs, column, evaluation.get(column))
  params = evaluation.get("generation_params")
  if isinstance(params, str):
    try:
      params = json.loads(params)
    except ValueError:
      params = None
  if isinstance(params, Mapping):
    _walk_knobs(params.get("values", params), "", knobs)
  return dict(sorted(knobs.items()))


def _walk_knobs(node: Any, prefix: str, knobs: dict[str, Any]) -> None:
  if not isinstance(node, Mapping):
    return
  for key, value in node.items():
    path = f"{prefix}.{key}" if prefix else str(key)
    if isinstance(value, Mapping):
      _walk_knobs(value, path, knobs)
    elif key in _KNOB_KEYS:
      _keep_knob(knobs, path, value)


def _keep_knob(knobs: dict[str, Any], name: str, value: Any) -> None:
  if isinstance(value, (bool, int, float)) or (isinstance(value, str) and
                                               _KNOB_VALUE_RE.fullmatch(value)):
    knobs[name] = value


def _safe_version(version: Any) -> str | None:
  """``<base>-<sha7>[-dirty]``: the branch name stays in real/."""
  if not version:
    return None
  match = _VERSION_RE.fullmatch(str(version))
  if match:
    return f"{match['base']}-{match['sha']}{match['dirty'] or ''}"
  return "h:" + hashlib.sha256(str(version).encode()).hexdigest()[:8]


def _model_label(uri: Any) -> str | None:
  """``family/model/version`` of a weights URI, without its bucket."""
  parts = str(uri or "").rstrip("/").split("/models/", 1)
  label = parts[1] if len(parts) > 1 else ""
  return label if label and _ID_RE.fullmatch(label) else None


def _warning_class(text: str) -> str:
  for name, pattern in (("scope", r"scope "), ("not_evaluated_table",
                                               r"not evaluated"),
                        ("validation_runs", r"validation_runs"),
                        ("relationships", r"relationship|generate_fk"),
                        ("reference", r"reference|panel"), ("storage_read_api",
                                                            r"Storage Read"),
                        ("one_sided_columns",
                         r"one side only|same kind of quantity"),
                        ("shuffle_budget", r"shuffle"), ("launch_lookup",
                                                         r"launch|job log")):
    if re.search(pattern, text):
      return name
  return "other"


def evaluation_entry(evaluation: Mapping[str, Any], backends: Mapping[str, str],
                     slices: Sequence[Slice]) -> dict[str, Any]:
  """One selected evaluation: what it is, how it was run, what it scored."""
  warnings = [str(w) for w in (evaluation.get("warnings") or [])]
  params = evaluation.get("evaluation_params")
  if isinstance(params, str):
    try:
      params = json.loads(params)
    except ValueError:
      params = {}
  params = params if isinstance(params, Mapping) else {}
  job_ids = [
      str(j)
      for j in (evaluation.get("generation_job_ids") or
                [evaluation.get("generation_job_id")])
      if j and re.fullmatch(r"[0-9_-]+", str(j))
  ]
  return {
      "evaluation_id":
          evaluation["evaluation_id"],
      "evaluated_at":
          _iso(evaluation.get("evaluated_at")),
      "status":
          evaluation.get("status"),
      "status_reason":
          evaluation.get("status_reason"),
      "mode":
          evaluation.get("mode"),
      "trigger":
          evaluation.get("trigger"),
      "engine":
          engine_effective(evaluation, backends),
      "engine_declared":
          evaluation.get("engine"),
      "generator_version":
          _safe_version(evaluation.get("generator_version")),
      "generator_version_full":
          evaluation.get("generator_version"),
      "generator_config_digest":
          evaluation.get("generator_config_digest"),
      "evaluator_version":
          evaluation.get("evaluator_version"),
      "catalogue_version":
          evaluation.get("catalogue_version"),
      "thresholds_digest":
          params.get("thresholds_digest"),
      "relationship_model":
          evaluation.get("relationship_model"),
      "model_adjusted":
          evaluation.get("model_adjusted"),
      "params_source":
          evaluation.get("params_source"),
      "llm_model":
          _model_label(evaluation.get("llm_model_uri")),
      "llm_model_uri":
          evaluation.get("llm_model_uri"),
      "generation_job_ids":
          job_ids,
      "tables_evaluated":
          len(slices),
      "scores": {
          name: _num(evaluation.get(column))
          for name, column in _SCORE_IDS.items()
          if name not in ("column_shape", "pair_trend")
      },
      "knobs":
          _knobs(evaluation),
      "warning_classes":
          dict(
              sorted(
                  collections.Counter(map(_warning_class, warnings)).items())),
      "warning_examples":
          warnings[:5],
      "headline":
          headline(evaluation, slices),
  }


def comparability(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
  """Groups of evaluations graded alike: numbers compare within a group."""
  groups: dict[tuple, list[str]] = collections.defaultdict(list)
  for entry in entries:
    key = (entry["catalogue_version"], entry["evaluator_version"],
           entry["mode"], entry["thresholds_digest"])
    groups[key].append(entry["evaluation_id"])
  return [{
      "catalogue_version": key[0],
      "evaluator_version": key[1],
      "mode": key[2],
      "thresholds_digest": key[3],
      "evaluations": len(ids),
      "evaluation_ids": sorted(ids),
  } for key, ids in sorted(groups.items(), key=lambda kv: str(kv[0]))]


# --------------------------------------------------------------------------
# the digest
# --------------------------------------------------------------------------
def build_digest(loaded: Loaded,
                 criteria: Mapping[str, Any],
                 *,
                 backends: Mapping[str, str],
                 catalogue: Mapping[str, Any],
                 top_k: int = 25,
                 per_table: int = 5) -> tuple[dict[str, Any], list[Slice]]:
  """The whole digest, in real space, and the slices it was built from."""
  keep, excluded = select(loaded.evaluations, criteria)
  every = build_slices(keep, loaded.metrics, backends,
                       criteria.get("tables_filter") or ())
  now = current(every, str(criteria.get("latest_per") or "table"))
  by_evaluation: dict[str, list[Slice]] = collections.defaultdict(list)
  for item in every:
    by_evaluation[item.evaluation_id].append(item)
  entries = [
      evaluation_entry(e, backends, by_evaluation[e["evaluation_id"]])
      for e in keep
  ]
  validation = {str(row.get("run_id")): row for row in loaded.validation_runs}
  found = signatures(now, catalogue)
  digest = {
      "schema": SCHEMA,
      "rubric_version": RUBRIC_VERSION,
      "space": "real",
      "selection": {
          "criteria":
              dict(criteria),
          "evaluations":
              entries,
          "excluded":
              excluded,
          "comparability":
              comparability(entries),
          "notes":
              sorted(set(loaded.notes)) + ([] if catalogue else [
                  "metric catalogue not read: directions are inferred "
                  "from the stored thresholds"
              ]),
      },
      "fleet": {
          "evaluations": len(keep),
          "table_slices": len(every),
          "table_slices_current": len(now),
          "tables": len({s.table_ref for s in now}),
          "engines": sorted({s.engine for s in now}),
          "measured_rows_current": sum(len(s.rows) for s in now),
      },
      "cells": cells(now),
      "signatures": [s for s in found if not _clean(s)],
      "signatures_clean": _clean_by_engine(found),
      "tables": table_cards(now, validation),
      "offenders": offenders(now, catalogue, top_k, per_table),
      "pairs": pairs(now),
      "edges": edges(now),
      "privacy_readings": privacy_readings(now),
      "not_evaluated": not_evaluated(now),
      "cross_run": cross_run(every),
  }
  return prune(digest), every


# --------------------------------------------------------------------------
# aliases and the oss/ twin
# --------------------------------------------------------------------------
class Aliaser:
  """Real names -> registry aliases, for the tables of one digest."""

  def __init__(self, registry: history_mappings.HistoryMappings) -> None:
    self.registry = registry
    self.refs: dict[str, str] = {}  # short table name -> table_ref
    self.models: dict[str, str] = {}
    self.external: dict[str, str] = {}

  def register(self,
               ref: str,
               name: str,
               columns: Iterable[str],
               ddl: Sequence[str] = ()) -> None:
    """Assign (or extend) the aliases of one table: DDL order first."""
    ordered = [*ddl, *sorted(set(columns) - set(ddl))]
    self.registry.assign_table(ref, [c for c in ordered if c])
    self.refs.setdefault(name, ref)

  def _entry(self, ref: str) -> dict[str, Any]:
    return self.registry.assign_table(ref, [])

  def table(self, ref: str) -> str:
    return str(self._entry(ref)["alias"])

  def column(self, ref: str, column: str | None) -> str | None:
    if not column:
      return None
    entry = self.registry.assign_table(ref, [column])
    return str(entry["columns"].get(column, column))

  def model(self, name: str | None) -> str | None:
    if not name:
      return None
    return self.models.setdefault(name, f"MODEL_{len(self.models) + 1:02d}")

  def edge(self, ref: str, label: str | None) -> str | None:
    """``child(cols) -> ref(ref_cols)`` with every part aliased."""
    if not label:
      return None
    match = EDGE_RE.fullmatch(label.strip())
    if not match:
      return "EDGE_" + hashlib.sha256(label.encode()).hexdigest()[:8]
    cols = ",".join(
        self.column(ref, c.strip()) or ""
        for c in match["cols"].split(",")
        if c.strip())
    parent = match["ref"].strip().rsplit(".", 1)[-1]
    parent_ref = self.refs.get(parent)
    names = [c.strip() for c in match["ref_cols"].split(",") if c.strip()]
    if parent_ref is None:
      alias = self.external.setdefault(
          match["ref"].strip(), f"EXTERNAL_TABLE_{len(self.external) + 1:02d}")
      ref_cols = ",".join(f"KEY_{i + 1}" for i in range(len(names)))
    else:
      alias = self.table(parent_ref)
      ref_cols = ",".join(self.column(parent_ref, c) or "" for c in names)
    return f"{self.table(ref)}({cols}) -> {alias}({ref_cols})"

  def alias_map(self, refs: Iterable[str],
                names: Mapping[str, str]) -> dict[str, Any]:
    """The decode key of this report: real names next to their aliases."""
    tables, projects, datasets = [], set(), set()
    for ref in sorted(set(refs)):
      entry = self._entry(ref)
      parts = ref.split(".")
      if len(parts) == _FQN_PARTS:
        projects.add(parts[0])
        datasets.add(parts[1])
      tables.append({
          "table_ref": ref,
          "table": names.get(ref, parts[-1]),
          "alias": entry["alias"],
          "columns": dict(entry["columns"]),
      })
    return {
        "schema": ALIAS_MAP_SCHEMA,
        "identifiers": {
            "projects": sorted(projects),
            "datasets": sorted(datasets)
        },
        "models": dict(sorted(self.models.items())),
        "external_tables": dict(sorted(self.external.items())),
        "tables": tables,
    }


def register_tables(slices: Sequence[Slice], ddl: Mapping[str, Sequence[str]],
                    aliaser: Aliaser) -> dict[str, str]:
  """Register every table of the digest; returns ``table_ref -> name``."""
  columns: dict[str, set[str]] = collections.defaultdict(set)
  names: dict[str, str] = {}
  for item in slices:
    names[item.table_ref] = item.table
    for row in [*item.rows, *item.rollups.values()]:
      columns[item.table_ref].update(
          row[key] for key in ("column_name", "column_name_2") if row.get(key))
  for ref in sorted(names):
    aliaser.register(ref, names[ref], columns[ref], ddl.get(ref, ()))
  return names


def to_oss(node: Any,
           aliaser: Aliaser,
           ref: str | None = None,
           key: str = "") -> Any:
  """The oss/ twin of a digest node, by allow-list (see the module doc)."""
  if isinstance(node, Mapping):
    ref = node.get("table_ref", ref)
    out: dict[str, Any] = {}
    for name, value in node.items():
      if name in _REAL_ONLY:
        continue
      if not _ID_RE.fullmatch(str(name)):
        raise LeakError(f"a dict key under {key!r} is not allow-listed")
      out[str(name)] = _oss_value(str(name), value, aliaser, ref)
    return out
  if isinstance(node, (list, tuple)):
    return [_oss_value(key, item, aliaser, ref) for item in node]
  return _oss_value(key, node, aliaser, ref)


def _oss_value(key: str, value: Any, aliaser: Aliaser, ref: str | None) -> Any:
  if isinstance(value, (Mapping, list, tuple)):
    return to_oss(value, aliaser, ref, key)
  if not isinstance(value, str):
    return value
  if key in _NAME_KEYS:
    return _oss_name(key, value, aliaser, ref)
  if key in _PROSE_KEYS:
    return value
  if not _ID_RE.fullmatch(value):
    raise LeakError(f"the string under {key!r} is not allow-listed for oss/")
  return value


def _oss_name(key: str, value: str, aliaser: Aliaser, ref: str | None) -> Any:
  if key == "relationship_model":
    return aliaser.model(value)
  if ref is None:
    raise LeakError(f"{key!r} has no table to be aliased against")
  if key == "table":
    return aliaser.table(ref)
  if key == "edge":
    return aliaser.edge(ref, value)
  return aliaser.column(ref, value)


def strings(node: Any) -> Iterable[str]:
  """Every string VALUE of a JSON tree (keys are this script's own)."""
  if isinstance(node, Mapping):
    for value in node.values():
      yield from strings(value)
  elif isinstance(node, (list, tuple)):
    for value in node:
      yield from strings(value)
  elif isinstance(node, str):
    yield node


def real_tokens(alias_map: Mapping[str, Any]) -> dict[str, set[str]]:
  """The real names a shareable file must not contain, by class."""
  tables, columns, identifiers = set(), set(), set()
  for entry in alias_map.get("tables", []):
    tables.add(entry["table"])
    identifiers.add(entry["table_ref"])
    columns.update(entry.get("columns", {}))
  ids = alias_map.get("identifiers", {})
  identifiers.update(ids.get("projects", []))
  identifiers.update(ids.get("datasets", []))
  tables.update(alias_map.get("models", {}))
  tables.update(alias_map.get("external_tables", {}))
  return {
      "identifiers": {t for t in identifiers if t},
      "tables": {t for t in tables if t},
      "columns": {c for c in columns if c},
  }


def scan_text(
    text: str, tokens: Mapping[str, set[str]], allow: Iterable[str] = ()
) -> dict[str, list[str]]:
  """Real names found in ``text``: identifiers by substring, table and
  column names as whole words. ``allow`` waives named tokens."""
  waived = set(allow)
  hits: dict[str, list[str]] = {"identifiers": [], "tables": [], "columns": []}
  for token in sorted(tokens["identifiers"] - waived):
    if token in text:
      hits["identifiers"].append(token)
  for kind in ("tables", "columns"):
    for token in sorted(tokens[kind] - waived):
      if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
                   text):
        hits[kind].append(token)
  return hits


def decode_text(text: str, alias_map: Mapping[str, Any]) -> str:
  """Alias tokens replaced by the real names (for use where the data is)."""
  back: dict[str, str] = {}
  for entry in alias_map.get("tables", []):
    back[entry["alias"]] = entry["table"]
    for real, alias in entry.get("columns", {}).items():
      back[alias] = real
  for real, alias in alias_map.get("models", {}).items():
    back[alias] = real
  for real, alias in alias_map.get("external_tables", {}).items():
    back[alias] = real
  return _ALIAS_TOKEN_RE.sub(lambda m: back.get(m.group(0), m.group(0)), text)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def prune(node: Any, depth: int = 0) -> Any:
  """Drop the None and empty values of the records (not of the sections),
  so a record names only what it knows."""
  if isinstance(node, Mapping):
    out = {}
    for key, value in node.items():
      kept = prune(value, depth + 1)
      if depth >= _RECORD_DEPTH and (kept is None or kept in ({}, [])):
        continue
      out[key] = kept
    return out
  if isinstance(node, (list, tuple)):
    return [prune(value, depth + 1) for value in node]
  return node


def render(node: Any, depth: int = 0) -> str:
  """JSON with one record a line: sections expand, records stay inline."""
  flat = json.dumps(node, ensure_ascii=False, default=_iso)
  pad = " " * depth
  if not isinstance(node, (Mapping, list)) or not node:
    return flat
  if isinstance(node, Mapping):
    if depth >= _RECORD_DEPTH and len(flat) <= _INLINE_CHARS:
      return flat
    body = [
        f"{pad} {json.dumps(str(key))}: {render(value, depth + 1)}"
        for key, value in node.items()
    ]
    return "{\n" + ",\n".join(body) + "\n" + pad + "}"
  if not any(isinstance(value, (Mapping, list)) for value in node):
    return flat
  body = [f"{pad} {render(value, depth + 1)}" for value in node]
  return "[\n" + ",\n".join(body) + "\n" + pad + "]"


def dump(path: Path, payload: Any) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(render(payload) + "\n", encoding="utf-8")


def _report_id(args: argparse.Namespace, digest: Mapping[str, Any]) -> str:
  if args.report_id:
    return args.report_id
  ids = [e["evaluation_id"] for e in digest["selection"]["evaluations"]]
  if len(ids) == 1:
    return str(ids[0])
  stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
  return f"evalrep-{stamp}-{len(ids)}runs"


def _parse_backends(specs: Sequence[str]) -> dict[str, str]:
  backends = {}
  for spec in specs:
    engine, sep, backend = spec.partition("=")
    if not sep or not re.fullmatch(r"[a-z0-9_]+", engine) or not re.fullmatch(
        r"[a-z0-9_ ]+", backend):
      die(f"--engine-backend expects engine=backend, got {spec!r}")
    backends[engine] = backend
  return backends


def _load(args: argparse.Namespace) -> Loaded:
  if args.report_json:
    return load_report_json(args.report_json)
  if not args.project:
    die("give --project (BigQuery) or --report-json (offline)")
  if not (args.evaluation_id or _window(args)):
    die("name --evaluation-id, or a window (--since-days / --from --to)")
  return load_bq(bq_client(args.project), args)


def _cmd_digest(args: argparse.Namespace) -> int:
  loaded = _load(args)
  catalogue = load_catalogue(args.catalogue)
  digest, slices = build_digest(
      loaded,
      _criteria(args),
      backends=_parse_backends(args.engine_backend),
      catalogue=catalogue,
      top_k=args.top_k,
      per_table=args.per_table)
  if not digest["selection"]["evaluations"]:
    die("no readable evaluation in scope: " +
        json.dumps(digest["selection"]["excluded"], default=_iso))
  registry = history_mappings.HistoryMappings.load(args.history_mappings)
  aliaser = Aliaser(registry)
  names = register_tables(slices, loaded.ddl_columns, aliaser)
  oss = to_oss(digest, aliaser)
  oss["space"] = "oss"
  alias_map = aliaser.alias_map(names, names)
  tokens = real_tokens(alias_map)
  hits = scan_text("\n".join(strings(oss)), {**tokens, "columns": set()})
  if hits["identifiers"] or hits["tables"]:
    die("a real name survived aliasing; nothing was written: " +
        json.dumps(hits))
  registry.save(args.history_mappings)
  base = args.out_root / _report_id(args, digest)
  dump(base / "real" / "evaluation_digest.json", digest)
  dump(base / "real" / "alias_map.json", alias_map)
  dump(base / "oss" / "evaluation_digest.json", oss)
  fleet = digest["fleet"]
  print(f"digest: {fleet['evaluations']} evaluation(s), "
        f"{fleet['table_slices_current']} table(s) read, "
        f"{fleet['measured_rows_current']} measured metric rows")
  print(f"real → {base / 'real'}")
  print(f"oss  → {base / 'oss'}   (aliased by construction; leak scan: clean)")
  return 0


def _cmd_scan(args: argparse.Namespace) -> int:
  alias_map = json.loads(args.alias_map.read_text(encoding="utf-8"))
  tokens = real_tokens(alias_map)
  fatal = review = 0
  for path in sorted(p for p in args.dir.rglob("*") if p.is_file()):
    text = path.read_bytes().decode("utf-8", errors="ignore")
    if path.suffix == ".json":
      # Keys are this toolchain's own; only values can carry a name.
      with contextlib.suppress(ValueError):
        text = "\n".join(strings(json.loads(text)))
    hits = scan_text(text, tokens, args.allow)
    for kind in ("identifiers", "tables"):
      for token in hits[kind]:
        fatal += 1
        print(f"LEAK   {path.name}: {kind[:-1]} {token!r}")
    for token in hits["columns"]:
      review += 1
      print(f"{'LEAK  ' if args.strict_columns else 'REVIEW'} "
            f"{path.name}: column name {token!r} appears as a word")
  if args.strict_columns:
    fatal += review
  if fatal:
    print(f"leak scan: {fatal} real name(s) found ❌")
    return 1
  suffix = (f" ({review} column-name word(s) to review: generic words such "
            "as `status` may be prose)") if review else ""
  print(f"leak scan: clean ✅{suffix}")
  return 0


def _cmd_decode(args: argparse.Namespace) -> int:
  alias_map = json.loads(args.alias_map.read_text(encoding="utf-8"))
  text = args.in_path.read_text(encoding="utf-8")
  args.out.parent.mkdir(parents=True, exist_ok=True)
  args.out.write_text(decode_text(text, alias_map), encoding="utf-8")
  print(f"decoded → {args.out}   (real names: keep it local)")
  return 0


def _cmd_show(args: argparse.Namespace) -> int:
  root = json.loads(args.digest.read_text(encoding="utf-8"))
  node: Any = root
  for part in args.section.split("."):
    if not isinstance(node, Mapping) or part not in node:
      die(f"no section {args.section!r}; top level: " + ", ".join(root))
    node = node[part]
  wanted = dict(spec.split("=", 1) for spec in args.where if "=" in spec)
  shown = 0
  for record in (node if isinstance(node, list) else [node]):
    if isinstance(record, Mapping) and any(
        str(record.get(key)) != value for key, value in wanted.items()):
      continue
    if args.limit and shown >= args.limit:
      break
    print(json.dumps(record, ensure_ascii=False))
    shown += 1
  return 0


def build_parser() -> argparse.ArgumentParser:
  """The command line: ``digest``, ``show``, ``scan`` and ``decode``."""
  ap = argparse.ArgumentParser(
      description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = ap.add_subparsers(dest="cmd", required=True)
  dg = sub.add_parser("digest", help="read evaluations, write real/ and oss/")
  dg.add_argument("--project", default="", help="GCP project of the tables")
  dg.add_argument("--quality-dataset", default="synthetic_data_quality")
  dg.add_argument(
      "--evaluation-id", action="append", default=[], help="repeatable")
  dg.add_argument(
      "--since-days", type=float, default=None, help="evaluated in the last N")
  dg.add_argument("--from", dest="from_ts", default="", help="ISO timestamp")
  dg.add_argument("--to", dest="to_ts", default="", help="ISO timestamp")
  dg.add_argument(
      "--engine", action="append", default=[], help="registry engine filter")
  dg.add_argument("--relationship-model", action="append", default=[])
  dg.add_argument(
      "--table", action="append", default=[], help="table name filter")
  dg.add_argument(
      "--latest-per",
      choices=("table", "none"),
      default="table",
      help="status is read from the latest evaluation of each table and "
      "engine (default), or from every evaluation in scope")
  dg.add_argument(
      "--engine-backend",
      action="append",
      default=[],
      help="engine=backend as the operator knows it, e.g. "
      "b2_library=empirical (BigQuery does not record the backend)")
  dg.add_argument(
      "--report-json",
      action="append",
      type=Path,
      default=[],
      help="offline: a `sdfb-eval report --format json` export (repeatable)")
  dg.add_argument(
      "--validation-runs-table",
      default="",
      help="project.dataset.validation_runs: adds the generator's verdict")
  dg.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
  dg.add_argument(
      "--history-mappings", type=Path, default=history_mappings.DEFAULT_PATH)
  dg.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
  dg.add_argument("--report-id", default="", help="folder name of the report")
  dg.add_argument("--top-k", type=int, default=25, help="offenders per level")
  dg.add_argument("--per-table", type=int, default=5, help="of one table")
  sc = sub.add_parser("scan", help="look for real names in a shareable folder")
  sc.add_argument("--dir", type=Path, required=True)
  sc.add_argument("--alias-map", type=Path, required=True)
  sc.add_argument("--allow", action="append", default=[], help="waive a token")
  sc.add_argument("--strict-columns", action="store_true")
  sh = sub.add_parser("show", help="print one section of a digest, filtered")
  sh.add_argument("--digest", type=Path, required=True)
  sh.add_argument(
      "--section", required=True, help="e.g. cells, offenders.pair, tables")
  sh.add_argument(
      "--where",
      action="append",
      default=[],
      help="key=value, repeatable (e.g. engine=b1_rag, scope=systemic)")
  sh.add_argument("--limit", type=int, default=0)
  dc = sub.add_parser("decode", help="aliases back to real names (local use)")
  dc.add_argument("--alias-map", type=Path, required=True)
  dc.add_argument("--in", dest="in_path", type=Path, required=True)
  dc.add_argument("--out", type=Path, required=True)
  return ap


def main(argv: list[str] | None = None) -> int:
  """Entry point; returns the process exit code."""
  args = build_parser().parse_args(argv)
  try:
    if args.cmd == "digest":
      return _cmd_digest(args)
    if args.cmd == "scan":
      return _cmd_scan(args)
    if args.cmd == "show":
      return _cmd_show(args)
    return _cmd_decode(args)
  except LeakError as e:
    die(f"{e}; nothing was written to oss/")


if __name__ == "__main__":
  raise SystemExit(main())
