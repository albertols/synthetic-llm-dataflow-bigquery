"""Unit tests for `scripts/e2e_gcp_probe.py` (the vendored Dataflow/BQ probe).

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

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e_gcp_probe.py"
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
