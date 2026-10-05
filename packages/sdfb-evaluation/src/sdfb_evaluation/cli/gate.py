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
"""What a finished `sdfb-eval run` exits with (Rulings R88g, R90g).

    FINAL row                              exit
    ─────────────────────────────────────  ────────────────────────────────
    status FAILED                          3, whatever `--fail_on` says
    any other status                       the `--fail_on` gate: 0, or 1

    --fail_on   exit 1 when the run holds
    ──────────  ──────────────────────────────────────────
    none        never (the default)
    fail        at least one metric at FAIL
    warn        at least one metric at WARN or FAIL

A FINAL row can read FAILED without anything having raised: when no
launch table could be evaluated, the pipeline writes that row itself and
ends normally. `final_exit_code` therefore reads the row's status first
— 3 is not a gate and `--fail_on none` does not mask it. (An evaluation
that fails with an exception never gets here: the driver appends its
FAILED row and re-raises.)

The gate reads the DATA verdict only: the headline counts of the
measured rows (aggregate `*_score` rows excluded, as in the registry's
`metrics_*` columns). A run's own status is otherwise not a verdict on
the data — a PARTIAL or SKIPPED run is reported on the summary line and
does not trip it.

`--thresholds_uri` changes what the GATE calls a warn or a fail, never
what is stored: every `evaluation_metrics` row keeps the status the
packaged catalogue gives it, so two runs with the same
`catalogue_version` stay comparable. The file is YAML:

    thresholds:
      column.ks: {warn: 0.05, fail: 0.10}
      row.exact_match_rate_nonkey: {fail: 0.0005}     # warn: the catalogue's

and each named metric's rows are graded again from their stored value,
interval, noise floor and detail by the same `scoring.status_for` (D5:
the noise check still applies), with the two thresholds replaced. A row
the first grading could not reach again from its stored columns — a
non-finite value, written as NULL — keeps its stored status.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

import yaml

from sdfb_evaluation.catalogue import Metric, load_catalogue
from sdfb_evaluation.scoring import is_aggregate, status_for
from sdfb_evaluation.types import Method, MetricValue, Status

__all__ = [
    "EXIT_FAILED",
    "EXIT_TRIPPED",
    "FAIL_ON",
    "exit_code",
    "final_exit_code",
    "gate_counts",
    "load_thresholds",
]

FAIL_ON = ("none", "warn", "fail")
EXIT_TRIPPED = 1
EXIT_FAILED = 3  # 2 is argparse's usage error
_FAILED = "FAILED"
_KEYS = frozenset({"warn", "fail"})

Thresholds = Mapping[str, tuple[float | None, float | None]]


def _read_text(uri: str) -> str:
  if uri.startswith("gs://"):
    from apache_beam.io.filesystems import FileSystems  # pylint: disable=import-outside-toplevel  # only a gs:// URI needs Beam's filesystems

    with FileSystems.open(uri) as handle:
      content: bytes = handle.read()
    return content.decode("utf-8")
  with open(uri, encoding="utf-8") as local:
    return local.read()


def _number(metric_id: str, key: str, value: Any) -> float | None:
  if value is None:
    return None
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError(f"thresholds {metric_id}.{key}: expected a number or "
                     f"null, got {value!r}")
  return float(value)


def load_thresholds(uri: str) -> dict[str, tuple[float | None, float | None]]:
  """`{metric id: (warn, fail)}` from the YAML at `uri` (a local path or
  `gs://`), a key the file leaves out keeping the catalogue's value.

  Raises:
    ValueError: no `thresholds` mapping, an id the catalogue does not
      have, a key other than warn/fail, or a threshold that is not a
      number.
    OSError: the file cannot be read.
  """
  document = yaml.safe_load(_read_text(uri))
  entries = document.get("thresholds") if isinstance(document,
                                                     Mapping) else None
  if not isinstance(entries, Mapping):
    raise ValueError(f"{uri}: expected a top-level `thresholds` mapping of "
                     "metric id to {warn, fail}")
  catalogue = load_catalogue()
  known = set(catalogue.ids())
  overrides: dict[str, tuple[float | None, float | None]] = {}
  for metric_id, entry in entries.items():
    if metric_id not in known:
      raise ValueError(f"{uri}: {metric_id!r} is not a catalogue metric id "
                       "(`sdfb-eval catalogue --format md` lists them)")
    if not isinstance(entry, Mapping) or not set(entry) <= _KEYS:
      raise ValueError(f"{uri}: thresholds {metric_id} must be a mapping "
                       "with warn and/or fail")
    metric = catalogue.get(metric_id)
    overrides[metric_id] = (_number(metric_id, "warn",
                                    entry.get("warn", metric.warn)),
                            _number(metric_id, "fail",
                                    entry.get("fail", metric.fail)))
  return overrides


def _regraded(row: Mapping[str, Any], metric: Metric) -> str:
  """`row`'s status under `metric` (its catalogue entry, the thresholds
  replaced)."""
  detail = row.get("detail") or {}
  if row.get("value") is None and detail.get("nonfinite"):
    return str(row["status"])
  mv = MetricValue(
      metric_id=metric.id,
      table=str(row["table_name"]),
      value=row.get("value"),
      column=row.get("column_name"),
      column_2=row.get("column_name_2"),
      edge=row.get("edge"),
      source_value=row.get("source_value"),
      synthetic_value=row.get("synthetic_value"),
      baseline_value=row.get("baseline_value"),
      noise_floor=row.get("noise_floor"),
      ci_low=row.get("ci_low"),
      ci_high=row.get("ci_high"),
      n_source=row.get("n_source"),
      n_synthetic=row.get("n_synthetic"),
      method=Method(row.get("method") or Method.EXACT),
      sample_rate=row.get("sample_rate"),
      column_kind=row.get("column_kind"),
      detail=detail)
  enforced = detail.get("enforced", True)
  return status_for(
      metric, mv,
      enforced=enforced if isinstance(enforced, bool) else True).value


def gate_counts(rows: Iterable[Mapping[str, Any]],
                thresholds: Thresholds) -> dict[str, int]:
  """How many measured `evaluation_metrics` rows hold each status (and
  `total`), aggregate rows excluded; a metric named in `thresholds` is
  graded again under its (warn, fail). The rows are not modified.

  Raises:
    ValueError: a row's status is not a `Status` value.
  """
  catalogue = load_catalogue() if thresholds else None
  regraded = {
      metric_id:
          dataclasses.replace(catalogue.get(metric_id), warn=warn, fail=fail)
      for metric_id, (warn, fail) in thresholds.items()
      if catalogue is not None
  }
  counts = {status.value: 0 for status in Status}
  total = 0
  for row in rows:
    metric_id = str(row["metric_id"])
    if is_aggregate(metric_id):
      continue
    status = str(row["status"])
    if metric_id in regraded:
      status = _regraded(row, regraded[metric_id])
    if status not in counts:
      raise ValueError(f"{metric_id}: unknown status {status!r}")
    counts[status] += 1
    total += 1
  counts["total"] = total
  return counts


def exit_code(fail_on: str, counts: Mapping[str, Any]) -> int:
  """0, or 1 when the gate trips (module docstring).

  Raises:
    ValueError: `fail_on` is not none | warn | fail.
  """
  if fail_on not in FAIL_ON:
    raise ValueError(f"fail_on {fail_on!r}: expected one of {list(FAIL_ON)}")
  failing = int(counts.get("fail") or 0)
  warning = int(counts.get("warn") or 0)
  if fail_on == "fail" and failing:
    return EXIT_TRIPPED
  if fail_on == "warn" and (failing or warning):
    return EXIT_TRIPPED
  return 0


def final_exit_code(final: Mapping[str, Any],
                    fail_on: str,
                    counts: Mapping[str, Any],
                    *,
                    gated: bool = True) -> int:
  """The exit code of a run whose FINAL registry row is `final` (module
  docstring): 3 when it reads FAILED; otherwise the gate's 0 or 1, or 0
  when the caller applies no gate (`gated=False`, the flex entry).

  Raises:
    ValueError: `fail_on` is not none | warn | fail.
  """
  code = exit_code(fail_on, counts)  # validates fail_on either way
  if final.get("status") == _FAILED:
    return EXIT_FAILED
  return code if gated else 0
