"""feature_flag_tags — sorted, deterministic, config-derived (design §4 notes)."""

from sdfb_beam.pipeline import PipelineConfig, _build_feature_flag_tags, _engine_version


def _config(**overrides) -> PipelineConfig:
    from sdfb_core.contracts import TableSchema

    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.d.t"}, "columns": [{"name": "a", "type": "STRING"}]}
    )
    defaults = dict(
        table_schema=schema, engine_name="b1_rag", model_client=object(), num_rows=10
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def test_tags_sorted_and_stable():
    config = _config(similarity=0.5, identity_columns=("id",), embedder_id="bge", embedder_version="v1")
    tags = _build_feature_flag_tags(config)
    assert tags == sorted(tags)
    assert _build_feature_flag_tags(config) == tags
    assert "engine:b1_rag" in tags
    assert "similarity:0.50" in tags
    assert "identity_columns:id" in tags
    assert "embedder:bge-v1" in tags


def test_optional_tags_absent_when_unset():
    tags = _build_feature_flag_tags(_config())
    assert not any(t.startswith(("identity_columns:", "embedder:", "rag_read_path:")) for t in tags)


def test_engine_version_lookup_and_unknown_fallback():
    assert _engine_version("b1_rag") == "0.2.0"
    assert _engine_version("nope") == "unknown"
