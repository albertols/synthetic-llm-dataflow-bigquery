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


def test_uniqueness_mode_surfaces_accept_every_cli_mode():
    """ADR 0034 added `exact_chained`; the template regex, the Composer
    enum and argparse choices must agree or the mode is unreachable."""
    import re

    from sdfb_beam.dofns.uniqueness import UNIQUENESS_MODES

    (regex,) = _metadata_param("uniqueness_mode")["regexes"]
    for mode in (*UNIQUENESS_MODES, ""):
        assert re.fullmatch(regex, mode), mode
    dag_text = _COMPOSER_DAG.read_text()
    for mode in UNIQUENESS_MODES:
        assert f'"{mode}"' in dag_text, mode


def test_flex_template_and_composer_expose_initial_workers():
    """ADR 0034: the initial worker count is a per-trigger knob."""
    import re

    param = _metadata_param("initial_workers")
    assert param["isOptional"] is True
    (regex,) = param["regexes"]
    for ok in ("", "4", "16"):
        assert re.fullmatch(regex, ok), ok
    assert not re.fullmatch(regex, "four")
    dag_text = _COMPOSER_DAG.read_text()
    assert '"initial_workers": Param(' in dag_text
    assert '"initial_workers": "{{ params.initial_workers }}"' in dag_text


def test_composer_dag_exposes_sdk_containers_topology():
    """ADR 0034: `sdk_containers=multi` lifts `no_use_multiple_sdk_containers`
    for a vLLM launch; `single` (default) keeps the RUN_PLAYBOOK §3 pin."""
    dag_text = _COMPOSER_DAG.read_text()
    assert '"sdk_containers": Param(' in dag_text
    assert "enum=[\"single\", \"multi\"]" in dag_text
    assert "params.sdk_containers == 'single'" in dag_text
