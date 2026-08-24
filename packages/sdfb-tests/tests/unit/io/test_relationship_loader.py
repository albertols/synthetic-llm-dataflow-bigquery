"""Loading `config/relationships/` from disk, a file, or GCS (ADR 0032).

The launcher must find the models with zero ceremony: the packaged
directory is the default, a single file works, and a `gs://` override
needs no image rebuild. Absence is legitimate; a broken file is not.
"""

from __future__ import annotations

import logging

import pytest
from sdfb_beam.io.relationships import load_relationship_registry
from sdfb_core.contracts.relationships import RelationshipError

_MODEL_A = """
model: retail
tables:
  A_TABLE:
    pk: [A_COL_001]
  B_TABLE:
    pk: [B_COL_001]
    fk:
      - cols: [B_COL_002]
        ref: A_TABLE
        ref_cols: [A_COL_001]
"""

_MODEL_B = """
model: risk
tables:
  R_TABLE:
    pk: [R_COL_001]
"""


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text)
    return path


class TestDirectoryLoading:
    def test_every_model_in_the_folder_is_loaded(self, tmp_path):
        _write(tmp_path, "retail.yaml", _MODEL_A)
        _write(tmp_path, "risk.yml", _MODEL_B)
        registry = load_relationship_registry(str(tmp_path))
        assert {m.model for m in registry.models} == {"retail", "risk"}
        assert set(registry.component("A_TABLE")) == {"A_TABLE", "B_TABLE"}
        assert registry.component("R_TABLE") == ("R_TABLE",)

    def test_non_model_files_are_ignored(self, tmp_path):
        _write(tmp_path, "retail.yaml", _MODEL_A)
        _write(tmp_path, "README.md", "# not a model")
        assert len(load_relationship_registry(str(tmp_path)).models) == 1

    def test_a_single_file_uri_works(self, tmp_path):
        path = _write(tmp_path, "retail.yaml", _MODEL_A)
        registry = load_relationship_registry(str(path))
        assert [m.model for m in registry.models] == ["retail"]

    def test_the_source_path_is_carried_for_the_log_card(self, tmp_path):
        path = _write(tmp_path, "retail.yaml", _MODEL_A)
        registry = load_relationship_registry(str(tmp_path))
        assert registry.models[0].source.endswith("retail.yaml")
        assert str(path) in registry.card("A_TABLE")


class TestAbsenceAndFailure:
    def test_no_files_is_a_legitimate_empty_registry(self, tmp_path, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            registry = load_relationship_registry(str(tmp_path))
        assert registry.models == ()
        assert "name=relationships_absent" in caplog.text

    def test_an_empty_uri_loads_nothing(self):
        assert load_relationship_registry("").models == ()

    def test_a_broken_model_stops_the_launch(self, tmp_path):
        _write(tmp_path, "broken.yaml", "model: x\ntables:\n  T:\n    pk: oops: [\n")
        with pytest.raises(RelationshipError):
            load_relationship_registry(str(tmp_path))

    def test_an_unknown_ref_stops_the_launch(self, tmp_path):
        _write(
            tmp_path,
            "bad.yaml",
            "model: x\ntables:\n  T:\n    fk:\n      - cols: [A]\n"
            "        ref: NOPE\n        ref_cols: [A]\n",
        )
        with pytest.raises(RelationshipError, match="does not name a table"):
            load_relationship_registry(str(tmp_path))


def test_loaded_milestone_reports_what_the_run_will_use(tmp_path, caplog):
    _write(tmp_path, "retail.yaml", _MODEL_A)
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        registry = load_relationship_registry(str(tmp_path))
    assert "name=relationships_loaded" in caplog.text
    assert "models=retail" in caplog.text
    assert "tables=2" in caplog.text
    assert f"sha={registry.sha12()}" in caplog.text
