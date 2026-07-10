"""GenerateRecordsDoFn must drive the ModelClient lifecycle.

E2E defect (2026-07-10 reports, all 5 live runs): nothing ever called
`VLLMModelClient.setup()`, so the first `generate_json` raised RuntimeError
inside the engines' broad except → `freetext_llm_fallback` → 100%
memorized reference data. The DoFn owns the worker lifecycle, so it must
call the client's optional `setup()` BEFORE `engine.setup()` (B.1 calls the
LLM during engine setup) and `teardown()` after the engine's teardown.
`FakeModelClient` has no lifecycle methods and must keep working unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from sdfb_beam.dofns import generate as generate_mod
from sdfb_beam.dofns.generate import GenerateRecordsDoFn


class _Record:
    def __init__(self, i: int) -> None:
        self._i = i

    def model_dump(self, mode="python"):
        return {"i": self._i}


class _StubEngine:
    """Records lifecycle order on the class-level `events` list."""

    events: ClassVar[list[str]] = []

    def setup(self, model_client, ctx):
        _StubEngine.events.append("engine_setup")
        # B.1 calls the LLM inside setup() — the client MUST be ready here.
        model_client.generate_json(prompt="p", json_schema={})

    def generate_batch(self, n, cfg):
        for i in range(n):
            yield _Record(i)

    def teardown(self):
        _StubEngine.events.append("engine_teardown")


class _SpyClient:
    """Lifecycle-bearing client (the VLLMModelClient shape)."""

    def __init__(self) -> None:
        self.ready = False

    def setup(self):
        _StubEngine.events.append("client_setup")
        self.ready = True

    def teardown(self):
        _StubEngine.events.append("client_teardown")
        self.ready = False

    def generate_json(self, prompt, json_schema, **kw):
        if not self.ready:
            raise RuntimeError("generate_json before setup()")
        return [{}]


class _NoLifecycleClient:
    """FakeModelClient shape — no setup()/teardown()."""

    def generate_json(self, prompt, json_schema, **kw):
        return [{}]


class _BoomOnSetupClient:
    """A client whose setup() fails to start the LLM server (e.g. vLLM
    subprocess boot failure). Must crash the worker (E2E 2026-07-10: the
    prior silent-fallback defect), not be swallowed."""

    def setup(self):
        raise RuntimeError("vllm boom")

    def generate_json(self, prompt, json_schema, **kw):
        return [{}]


def _ctx():
    # Structural stand-in; the DoFn only touches these attributes on the
    # non-gs:// path. Keeps the test free of TableSchema construction.
    return SimpleNamespace(
        embedder_uri="",
        table_schema=SimpleNamespace(columns=[]),
        identity_columns=[],
        pipeline_run_id="lifecycle-test",
    )


def _dofn(client, monkeypatch):
    monkeypatch.setattr(generate_mod, "get_engine", lambda name: _StubEngine)
    return GenerateRecordsDoFn(
        engine_name="stub", model_client=client, ctx=_ctx(), seed=7
    )


def test_client_setup_runs_before_engine_setup(monkeypatch):
    _StubEngine.events = []
    dofn = _dofn(_SpyClient(), monkeypatch)
    dofn.setup()  # must NOT raise "generate_json before setup()"
    assert _StubEngine.events[:2] == ["client_setup", "engine_setup"]


def test_client_teardown_runs_after_engine_teardown(monkeypatch):
    _StubEngine.events = []
    dofn = _dofn(_SpyClient(), monkeypatch)
    dofn.setup()
    dofn.teardown()
    assert _StubEngine.events == [
        "client_setup",
        "engine_setup",
        "engine_teardown",
        "client_teardown",
    ]


def test_client_without_lifecycle_still_works(monkeypatch):
    _StubEngine.events = []
    dofn = _dofn(_NoLifecycleClient(), monkeypatch)
    dofn.setup()
    rows = list(dofn.process({"n": 2, "batch_id": 0}))
    dofn.teardown()
    assert len(rows) == 2
    assert _StubEngine.events == ["engine_setup", "engine_teardown"]


def test_client_setup_failure_propagates_and_skips_engine_setup(monkeypatch):
    """A client.setup() exception (e.g. vLLM subprocess failed to boot)
    must re-raise out of GenerateRecordsDoFn.setup() so the worker crashes
    loudly — and engine.setup() must never run, so the engine can't limp
    forward without a live LLM and silently fall back to memorized
    reference data."""
    _StubEngine.events = []
    dofn = _dofn(_BoomOnSetupClient(), monkeypatch)
    with pytest.raises(RuntimeError, match="vllm boom"):
        dofn.setup()
    assert _StubEngine.events == []
