"""Unit tests for `scripts/e2e/e2e_gcp_probe.py` (the vendored Dataflow/BQ probe).

Loaded via importlib the same way `test_deployment_prerequisites.py` loads
`scripts/deployment_prerequisites.py` (see that file's docstring/idiom). The
first test is a two-sides-of-one-contract test: it imports
`sdfb_core.observability.format_milestone` (the writer) and asserts the
probe's `_SDFB_MILESTONE_RE` (the reader, re-implemented standalone since the
probe must not import project packages at runtime) parses its output.

Offline-only: no GCP credentials or network calls. `bq_quality` is exercised
against a fake BigQuery client object (a class with a
`query(sql, job_config=None)` method) rather than a real client — see that
test's docstring for why the parameterized-query path is unit-testable
without mocking `google.cloud.bigquery` itself.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "e2e_gcp_probe.py"
_spec = importlib.util.spec_from_file_location("e2e_gcp_probe", _SCRIPT)
_probe_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _probe_module
_spec.loader.exec_module(_probe_module)


@pytest.fixture
def probe_module():
    return _probe_module


# --------------------------------------------------------------------------
# Step 1: SDFB_MILESTONE contract + legacy vllm_error regexes
# --------------------------------------------------------------------------
def test_probe_recognizes_sdfb_milestones(probe_module):
    from sdfb_core.observability import format_milestone

    line = format_milestone("vllm_ready", seconds=12.5)
    assert probe_module._SDFB_MILESTONE_RE.search(line)
    name = probe_module._SDFB_MILESTONE_RE.search(line).group("name")
    assert name == "vllm_ready"


def test_sdfb_milestone_re_ignores_uppercase_or_invalid_names(probe_module):
    # Milestone names are constrained to [a-z0-9_]+; a line that doesn't
    # match the prefix at all must not match.
    assert probe_module._SDFB_MILESTONE_RE.search("some unrelated log line") is None


def test_vllm_error_regexes(probe_module):
    labels = dict(probe_module._DEFAULT_MILESTONES)
    rx = labels["vllm_error"]
    for text in (
        "ValueError: Bfloat16 is only supported on GPUs with compute capability of at least 8.0",
        "out of resource: shared memory, Required: 98304, Hardware limit: 65536",
        "head size 512 is not supported by FlashInfer",
        "torch.cuda.OutOfMemoryError: CUDA out of memory",
    ):
        assert re.search(rx, text), text


def test_worker_log_milestones_captures_sdfb_generic_and_legacy(probe_module):
    """End-to-end through `_worker_log_milestones`: a real `format_milestone`
    line is mined into `sdfb.<name>` alongside legacy-wording matches, proving
    the generic capture and the fallback regexes coexist without clobbering.

    Uses `embedder_pulled` for the generic line (rather than e.g.
    `vllm_ready`) because its structured form ("SDFB_MILESTONE
    name=embedder_pulled files=12") happens not to also satisfy any legacy
    wording regex, keeping the two capture paths cleanly separated for the
    assertions below.
    """
    from sdfb_core.observability import format_milestone

    entries = [
        {
            "textPayload": format_milestone("embedder_pulled", files=12),
            "timestamp": "2026-07-06T00:00:10Z",
        },
        {
            "textPayload": "torch.cuda.OutOfMemoryError: CUDA out of memory",
            "timestamp": "2026-07-06T00:00:20Z",
        },
        {
            "textPayload": "vllm engine initialized, model loaded",
            "timestamp": "2026-07-06T00:00:05Z",
        },
    ]
    session = _FakeLogSession(entries)
    result = probe_module._worker_log_milestones(
        session, "proj", "job-1", probe_module._DEFAULT_MILESTONES, {}
    )
    assert result["timestamps"]["sdfb.embedder_pulled"] == "2026-07-06T00:00:10Z"
    assert result["timestamps"]["vllm_error"] == "2026-07-06T00:00:20Z"
    assert result["timestamps"]["vllm_ready"] == "2026-07-06T00:00:05Z"
    assert result["timestamps"]["vllm_engine_init"] == "2026-07-06T00:00:05Z"
    # Legacy "embedder_pulled" wording regex must not have fired on the
    # structured line — the two capture paths stay independent.
    assert "embedder_pulled" not in result["timestamps"]


def test_worker_log_milestones_attributes_pool_ladder_per_column(probe_module):
    """2026-08-20 B_TABLE R1: the report's topup → stagnated → fallback
    sequence could not be attributed to a column because the probe kept
    only the FIRST occurrence of each milestone name and dropped its
    `column=` field. Pool-ladder milestones are now also collected per
    column (first timestamp per (column, milestone))."""
    from sdfb_core.observability import format_milestone

    entries = [
        {
            "textPayload": format_milestone(
                "freetext_pool_shape_topup", column="CONCEPT", added=40
            ),
            "timestamp": "2026-07-06T00:01:00Z",
        },
        {
            "textPayload": format_milestone(
                "freetext_pool_stagnated", column="CONCEPT", novel=1
            ),
            "timestamp": "2026-07-06T00:05:00Z",
        },
        {
            "textPayload": format_milestone(
                "freetext_pool_built", column="REF_CODE", size=512
            ),
            "timestamp": "2026-07-06T00:02:00Z",
        },
        {  # duplicate for the same (column, milestone): first wins
            "textPayload": format_milestone(
                "freetext_pool_built", column="REF_CODE", size=512
            ),
            "timestamp": "2026-07-06T00:09:00Z",
        },
    ]
    session = _FakeLogSession(entries)
    result = probe_module._worker_log_milestones(
        session, "proj", "job-1", probe_module._DEFAULT_MILESTONES, {}
    )
    ladder = result["pool_ladder"]
    assert ladder["CONCEPT"]["freetext_pool_shape_topup"] == "2026-07-06T00:01:00Z"
    assert ladder["CONCEPT"]["freetext_pool_stagnated"] == "2026-07-06T00:05:00Z"
    assert ladder["REF_CODE"]["freetext_pool_built"] == "2026-07-06T00:02:00Z"


class _FakeResp:
    def __init__(self, data: dict):
        self._data = data
        self.status_code = 200

    def json(self) -> dict:
        return self._data


class _FakeLogSession:
    def __init__(self, entries: list[dict]):
        self._entries = entries
        self.calls = 0

    def post(self, url, json=None):
        self.calls += 1
        return _FakeResp({"entries": self._entries, "nextPageToken": None})


# --------------------------------------------------------------------------
# Step 3a: bq_quality run-id filter
# --------------------------------------------------------------------------
class _FakeQueryJob:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def result(self):
        return self._rows


class _FakeBqClient:
    """Records every `query()` call's SQL + job_config for assertions."""

    def __init__(self, rows: list[dict] | None = None):
        self.calls: list[tuple[str, object]] = []
        self._rows = rows if rows is not None else [{"run_id": "r1", "status": "ok"}]

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        return _FakeQueryJob(self._rows)


def test_bq_quality_no_run_id_uses_bare_limit_fallback(probe_module):
    client = _FakeBqClient()
    out = probe_module.bq_quality(client, "proj.synthetic_data_quality", [])
    sqls = [sql for sql, _cfg in client.calls]
    assert len(sqls) == 2  # validation_runs, dlq
    for sql in sqls:
        assert "LIMIT 50" in sql
        assert "run_id" not in sql
    assert out["validation_runs"] == [{"run_id": "r1", "status": "ok"}]


def test_bq_quality_with_run_ids_uses_parameterized_in_unnest(probe_module):
    client = _FakeBqClient()
    out = probe_module.bq_quality(
        client, "proj.synthetic_data_quality", ["run-a", "run-b"]
    )
    sqls_and_cfgs = client.calls
    assert len(sqls_and_cfgs) == 2
    for sql, cfg in sqls_and_cfgs:
        assert "WHERE run_id IN UNNEST(@run_ids)" in sql
        assert "LIMIT 50" in sql
        assert cfg is not None
        param = cfg.query_parameters[0]
        assert param.name == "run_ids"
        assert param.array_type == "STRING"
        assert list(param.values) == ["run-a", "run-b"]
    assert out["dlq"] == [{"run_id": "r1", "status": "ok"}]


def test_bq_quality_no_quality_dataset_is_noop(probe_module):
    client = _FakeBqClient()
    assert probe_module.bq_quality(client, "", ["run-a"]) == {}
    assert client.calls == []


# --------------------------------------------------------------------------
# Step 3b: --engine-label parsing + annotation
# --------------------------------------------------------------------------
def test_parse_engine_labels_maps_job_id_to_label(probe_module):
    out = probe_module._parse_engine_labels(["b1-rag=job-123", "b2-library=job-456"])
    assert out == {"job-123": "b1-rag", "job-456": "b2-library"}


def test_parse_engine_labels_ignores_malformed_entries(probe_module):
    out = probe_module._parse_engine_labels(["no-equals-sign"])
    assert out == {}


def test_annotate_engine_labels_stamps_matching_job_only(probe_module):
    results = [{"job_id": "job-123"}, {"job_id": "job-999"}]
    probe_module._annotate_engine_labels(results, {"job-123": "b1-rag"})
    assert results[0]["engine_label"] == "b1-rag"
    assert "engine_label" not in results[1]


# --------------------------------------------------------------------------
# Fix 5/6: SAFE_OFFSET + zero-denominator ratio semantics
# --------------------------------------------------------------------------
def test_ratio_zero_denominator_returns_none(probe_module):
    """An empty landing table (n=0) must not report a ratio of 0.0 — that
    reads as "measured and found to be zero" (e.g. copy_ratio=0.0 == "no
    memorization"), when in truth nothing was measured at all."""
    assert probe_module._ratio(0, 0) is None
    assert probe_module._ratio(5, 0) is None


def test_ratio_none_numerator_returns_none(probe_module):
    assert probe_module._ratio(None, 10) is None


def test_ratio_normal_division(probe_module):
    assert probe_module._ratio(5, 10) == 0.5
    assert probe_module._ratio(0, 10) == 0.0  # a real, measured zero is fine


def test_bq_cross_validation_top_count_uses_safe_offset(probe_module):
    """`APPROX_TOP_COUNT(col, 1)[OFFSET(0)]` throws on an all-NULL column
    (empty array; `OFFSET(0)` is a hard index and dies with no rows) — must
    be `[SAFE_OFFSET(0)]`, which returns NULL instead of killing the probe."""
    import inspect

    src = inspect.getsource(probe_module.bq_cross_validation)
    assert "SAFE_OFFSET(0)" in src
    assert "[OFFSET(0)]" not in src


# --------------------------------------------------------------------------
# Memorization flags — the 2026-07-16 report's CRITICAL gate gap: nothing
# scored copy_ratio, so 9 columns at 0.475-0.939 sailed through PASSED.
# --------------------------------------------------------------------------
def test_memorization_flags_flags_high_copy_ratio_high_cardinality(probe_module):
    columns = {
        "COL_048": {  # the worst 2026-07-16 leak: 93.9 % source values
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.939,
            "source_distinct": 19_815,
        },
        "COL_042": {  # just above both thresholds
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.475,
            "source_distinct": 1_142,
        },
    }
    flags = probe_module.memorization_flags(columns)
    assert [f["column"] for f in flags] == ["COL_048", "COL_042"]
    assert all(f["severity"] == "CRITICAL" for f in flags)
    assert flags[0]["copy_ratio"] == 0.939
    assert flags[0]["source_distinct"] == 19_815


def test_memorization_flags_skips_enums_constants_and_unmeasured(probe_module):
    columns = {
        "enum_by_design": {  # source_distinct <= 100: full coverage expected
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 1.0,
            "source_distinct": 60,
        },
        "constant": {  # constants carry no signal
            "type": "STRING",
            "is_constant": True,
            "copy_ratio": 1.0,
            "source_distinct": 5_000,
        },
        "below_ratio": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.29,
            "source_distinct": 5_000,
        },
        "not_measured": {  # no copy_ratio (not in source schema / empty)
            "type": "STRING",
            "is_constant": False,
        },
        "unmeasured_ratio_none": {  # _ratio(_, 0) → None must not compare
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": None,
            "source_distinct": 5_000,
        },
    }
    assert probe_module.memorization_flags(columns) == []


def test_memorization_flags_sorted_by_copy_ratio_desc(probe_module):
    columns = {
        "a": {"is_constant": False, "copy_ratio": 0.4, "source_distinct": 200},
        "b": {"is_constant": False, "copy_ratio": 0.9, "source_distinct": 200},
    }
    flags = probe_module.memorization_flags(columns)
    assert [f["column"] for f in flags] == ["b", "a"]


# --------------------------------------------------------------------------
# Sentinel/temporal awareness — the 2026-07-23 b1_rag run: the by-design
# sentinel parity ("0001-01-01" re-injected at observed frequency) plus the
# interim now-10y clamp pushed 6 date-shaped columns to copy_ratio 0.60-0.97
# and the raw rule flagged all 6 CRITICAL, though none is per-row copying.
# --------------------------------------------------------------------------
def test_memorization_flags_sentinel_dominated_column_not_flagged(probe_module):
    # COL_042 shape: 97 % of landing rows are the sentinel "0001-01-01".
    columns = {
        "COL_042": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.97,
            "copy_ratio_nonsentinel": 0.0,
            "sentinel_fraction": 0.97,
            "temporal_day_granularity": True,
            "source_distinct": 1_142,
        },
    }
    assert probe_module.memorization_flags(columns) == []


def test_memorization_flags_day_granularity_collision_downgraded_to_info(
    probe_module,
):
    # COL_034 shape: non-sentinel days collide inside the clamped 10y window
    # (~3650 possible days vs a dense 210k-row source) — expected by domain
    # size, not per-row memorization. Stays visible, but never CRITICAL.
    columns = {
        "COL_034": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.604,
            "copy_ratio_nonsentinel": 0.509,
            "sentinel_fraction": 0.194,
            "temporal_day_granularity": True,
            "source_distinct": 4_830,
        },
    }
    flags = probe_module.memorization_flags(columns)
    assert len(flags) == 1
    assert flags[0]["severity"] == "INFO"
    assert "domain" in flags[0]["rule"]


def test_memorization_flags_high_entropy_column_still_critical(probe_module):
    # A non-temporal high-cardinality column with real verbatim copies must
    # keep its CRITICAL flag even when the new fields are present.
    columns = {
        "leaky": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.9,
            "copy_ratio_nonsentinel": 0.9,
            "sentinel_fraction": 0.0,
            "temporal_day_granularity": False,
            "source_distinct": 19_815,
        },
        "temporal_info": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.7,
            "copy_ratio_nonsentinel": 0.5,
            "sentinel_fraction": 0.36,
            "temporal_day_granularity": True,
            "source_distinct": 7_574,
        },
    }
    flags = probe_module.memorization_flags(columns)
    assert [f["severity"] for f in flags] == ["CRITICAL", "INFO"]
    assert flags[0]["column"] == "leaky"


def test_bq_cross_validation_sql_is_sentinel_and_day_aware(probe_module):
    """The per-column memorization SQL must measure sentinel rows (year 1 /
    9999) separately and detect day-granularity values, with SAFE_CAST so
    BYTES columns cannot kill the probe."""
    import inspect

    src = inspect.getsource(probe_module.bq_cross_validation)
    assert "sentinel" in src
    assert "SAFE_CAST" in src
    assert "(0001|9999)-" in src


# --------------------------------------------------------------------------
# Dataflow job `parameters` — the 2026-07-16 report's MAJOR probe gap:
# launch params (reference_rows_limit, pk_cols, identity_cols, seed) were
# unconfirmable from e2e_gcp_metrics.json.
# --------------------------------------------------------------------------
def test_job_params_extracts_sdk_pipeline_options_display_data(probe_module):
    job = {
        "environment": {
            "sdkPipelineOptions": {
                "display_data": [
                    {
                        "key": "reference_rows_limit",
                        "namespace": "sdfb_beam.pipeline._SdfbOptions",
                        "type": "INTEGER",
                        "value": 2000,
                    },
                    {
                        "key": "pk_cols",
                        "namespace": "sdfb_beam.pipeline._SdfbOptions",
                        "type": "STRING",
                        "value": "id",
                    },
                ]
            }
        }
    }
    params = probe_module._job_params(job)
    assert params["reference_rows_limit"] == 2000
    assert params["pk_cols"] == "id"


def test_job_params_reads_pipeline_description_typed_values(probe_module):
    job = {
        "pipelineDescription": {
            "displayData": [
                {"key": "seed", "namespace": "ns", "int64Value": "42"},
                {"key": "engine", "namespace": "ns", "strValue": "b1_rag"},
                {"key": "strict", "namespace": "ns", "boolValue": True},
            ]
        }
    }
    params = probe_module._job_params(job)
    assert params["seed"] == "42"
    assert params["engine"] == "b1_rag"
    assert params["strict"] is True


def test_job_params_empty_job_yields_empty_dict(probe_module):
    assert probe_module._job_params({}) == {}


# --------------------------------------------------------------------------
# copy_ratio_substantive (2026-08-07 A_TABLE R1): empty-parity and
# head-value re-emission are BY-DESIGN fidelity, not memorization.
# --------------------------------------------------------------------------
def test_flags_prefer_substantive_ratio_over_raw(probe_module):
    """COL_048 class: source 62.4% empty, engine re-emits empties at parity,
    raw copy_ratio reads 0.628 — but among substantive values nothing is
    copied. Must NOT flag."""
    columns = {
        "COL_048": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.628,
            "copy_ratio_nonsentinel": 0.628,
            "copy_ratio_substantive": 0.004,
            "source_distinct": 19_815,
        },
    }
    assert probe_module.memorization_flags(columns) == []


def test_flags_fire_on_substantive_copying_and_carry_the_field(probe_module):
    columns = {
        "LEAKY": {
            "type": "STRING",
            "is_constant": False,
            "copy_ratio": 0.20,
            "copy_ratio_nonsentinel": 0.20,
            "copy_ratio_substantive": 0.45,
            "source_distinct": 5_000,
        },
    }
    flags = probe_module.memorization_flags(columns)
    assert [f["column"] for f in flags] == ["LEAKY"]
    assert flags[0]["copy_ratio_substantive"] == 0.45
    assert flags[0]["severity"] == "CRITICAL"


def test_copy_fraction_rule_scores_substantive_when_present(probe_module):
    per_col = {
        "COL_048": {
            "in_source_schema": True,
            "copy_ratio_nonsentinel": 0.628,
            "copy_ratio_substantive": 0.0,
            "source_distinct": 19_815,
            "distinct": 500,
            "source_distinct_ratio": 0.09,
        },
    }
    results = probe_module.evaluate_freetext_rules(per_col)
    cf = [r for r in results if r["rule"] == "freetext.copy_fraction"]
    assert len(cf) == 1
    assert cf[0]["passed"] is True
    assert cf[0]["value"] == 0.0


def test_substantive_sql_excludes_empty_and_frequent_source_values(probe_module):
    """The membership subquery must (a) drop trimmed-empty landing values
    and (b) exempt source values with frequency >= the k-anonymity floor —
    head-value re-emission (`KW3000` at 77% share) is enum mass, and a
    value shared by dozens of source rows identifies nobody."""
    sql = probe_module._substantive_copy_sql("`p.d.landing`", "`p.d.source`", "`c`")
    flat = " ".join(sql.split())
    assert "HAVING COUNT(*) <" in flat
    assert "TRIM(" in flat
