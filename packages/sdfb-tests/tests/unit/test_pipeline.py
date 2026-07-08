"""Unit tests for `sdfb_beam.pipeline.build_pipeline` config validation.

Regression test for the misleading failure mode where a typo'd
`identity_columns` entry (not a real column on the target schema) sends
every-row-but-one to the DLQ as `identity.unique` (every row keys on
`str(None)` since `record.get(bogus_column)` is always `None`), which then
trips the BLOCKER gate with an error that never mentions the actual typo.
Failing fast with a clear `ValueError` at graph-construction time is much
cheaper to diagnose.
"""

from __future__ import annotations

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, build_pipeline
from sdfb_tests import fakes  # noqa: F401  registers "minimal" engine
from sdfb_tests.fakes import FakeModelClient


def _config(schema, **overrides):
    defaults = dict(
        table_schema=schema,
        engine_name="minimal",
        model_client=FakeModelClient(reference_pool=[{}]),
        num_rows=5,
        batch_size=5,
        run_id="test-identity-validation",
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def test_build_pipeline_rejects_unknown_identity_column(tmp_path, customers_schema):
    config = _config(customers_schema, identity_columns=("not_a_real_column",))

    options = PipelineOptions(["--runner=DirectRunner"])
    with pytest.raises(ValueError, match="not_a_real_column"), beam.Pipeline(options=options) as p:
        build_pipeline(
            p,
            reference_rows=[],
            config=config,
            landing_sink=WriteToJsonLines(str(tmp_path / "landing")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq")),
        )


def test_build_pipeline_error_lists_valid_columns(tmp_path, customers_schema):
    config = _config(customers_schema, identity_columns=("bogus",))

    options = PipelineOptions(["--runner=DirectRunner"])
    with pytest.raises(ValueError) as exc_info, beam.Pipeline(options=options) as p:
        build_pipeline(
            p,
            reference_rows=[],
            config=config,
            landing_sink=WriteToJsonLines(str(tmp_path / "landing")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq")),
        )
    assert "customer_id" in str(exc_info.value)


def test_build_pipeline_accepts_valid_identity_column(tmp_path, customers_schema, customers_reference):
    config = _config(
        customers_schema,
        identity_columns=("customer_id",),
        model_client=FakeModelClient(reference_pool=customers_reference),
    )

    options = PipelineOptions(["--runner=DirectRunner"])
    with beam.Pipeline(options=options) as p:
        result = build_pipeline(
            p,
            reference_rows=customers_reference,
            config=config,
            landing_sink=WriteToJsonLines(str(tmp_path / "landing")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq")),
        )
    assert result["run_id"] == "test-identity-validation"
