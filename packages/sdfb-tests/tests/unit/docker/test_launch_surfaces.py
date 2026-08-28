"""Launch surfaces expose the flags the CLI accepts (ADR 0024 §3c).

A Dataflow flex-template launch validates parameters against
``docker/flex_template_metadata.json`` and the Composer DAG only forwards
declared ``Param``s — a flag missing from either surface is unreachable in
production even when ``run_pipeline.py`` supports it. ``--prompt_debug``
shipped in ADR 0024 without either exposure, so the milestone it gates
(``freetext_pool_prompt``) could never be turned on from a real run.

The composer check is textual (the DAG imports airflow, which is not a
laptop dependency) — same trade-off the file's other referencers accept.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[5]
_METADATA = _REPO_ROOT / "docker" / "flex_template_metadata.json"
_COMPOSER_DAG = _REPO_ROOT / "composer" / "synthetic_beam_bigquery.py"


def _metadata_param(name: str) -> dict:
    metadata = json.loads(_METADATA.read_text())
    by_name = {p["name"]: p for p in metadata["parameters"]}
    assert name in by_name, f"{name} missing from flex_template_metadata.json"
    return by_name[name]


def test_flex_template_exposes_prompt_debug():
    param = _metadata_param("prompt_debug")
    assert param["isOptional"] is True
    assert param["regexes"] == ["^(off|redacted|full)?$"]


def test_flex_template_prompt_debug_matches_cli_choices():
    """The metadata regex and the argparse choices must not drift."""
    import re

    (regex,) = _metadata_param("prompt_debug")["regexes"]
    for choice in ("off", "redacted", "full", ""):
        assert re.fullmatch(regex, choice), choice
    assert not re.fullmatch(regex, "verbose")


def test_composer_dag_declares_and_forwards_prompt_debug():
    dag_text = _COMPOSER_DAG.read_text()
    assert '"prompt_debug": Param(' in dag_text
    assert '"prompt_debug": "{{ params.prompt_debug }}"' in dag_text
