"""Unit tests for `GenerateRecordsDoFn.setup()` — specifically that a gs://
`embedder_uri` is warm-pulled to a local path before the engine builds its
embedder.

Regression test: previously the DoFn passed the raw gs:// URI straight into
the engine, which handed it to `transformers.from_pretrained` — which cannot
read gs:// and raised `OSError: Repo id must be in the form ...`.
"""

from __future__ import annotations

import sys
import types

import pytest
from sdfb_beam.dofns import generate as generate_mod
from sdfb_beam.dofns.generate import GenerateRecordsDoFn
from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.engines import GenerationContext


@pytest.fixture
def fake_gcs(monkeypatch):
    """Inject a fake `google.cloud.storage` module + return its recorder."""

    class _FakeBlob:
        def __init__(self, name):
            self.name = name
            self.downloaded_to = None

        def download_to_filename(self, dest):
            self.downloaded_to = dest

    recorder: dict = {"list_calls": [], "blobs": []}

    class _FakeStorageClient:
        def list_blobs(self, bucket, prefix=""):
            recorder["list_calls"].append((bucket, prefix))
            return list(recorder["blobs"])

    storage_mod = types.ModuleType("google.cloud.storage")
    storage_mod.Client = lambda *_a, **_k: _FakeStorageClient()
    cloud_mod = types.ModuleType("google.cloud")
    cloud_mod.storage = storage_mod
    google_mod = sys.modules.get("google") or types.ModuleType("google")

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
    recorder["blob_cls"] = _FakeBlob
    return recorder


class _FakeGeneratedRecord:
    """Minimal stand-in for a `GeneratedRecord` — only needs `model_dump()`."""

    def __init__(self, i: int) -> None:
        self._i = i

    def model_dump(self, mode: str = "python") -> dict:
        return {"i": self._i}


class _RecordingEngine:
    """Stand-in engine that captures the ctx it was set up with."""

    last_ctx: GenerationContext | None = None

    def setup(self, model_client, ctx):
        type(self).last_ctx = ctx

    def generate_batch(self, n, cfg):
        for i in range(n):
            yield _FakeGeneratedRecord(i)

    def teardown(self):
        pass


@pytest.fixture
def recording_engine(monkeypatch):
    _RecordingEngine.last_ctx = None
    monkeypatch.setattr(
        generate_mod, "get_engine", lambda _name: _RecordingEngine
    )
    return _RecordingEngine


def _dofn(ctx):
    return GenerateRecordsDoFn(
        engine_name="b1_rag",
        model_client=FakeModelClient(reference_pool=[{"x": 1}]),
        ctx=ctx,
    )


def test_setup_localizes_gs_embedder_uri(
    fake_gcs, recording_engine, monkeypatch, tmp_path, customers_schema
):
    monkeypatch.setattr(generate_mod, "EMBEDDER_LOCAL_DIR", str(tmp_path / "emb"))
    prefix = "synthetic/models/embedders/bge-small-en-v1.5/v1/"
    blob_cls = fake_gcs["blob_cls"]
    fake_gcs["blobs"] = [blob_cls(prefix + "config.json")]

    ctx = GenerationContext(
        table_schema=customers_schema,
        embedder_uri=f"gs://env-bucket/{prefix}",
    )
    dofn = _dofn(ctx)
    dofn.setup()

    # The pull ran against the right prefix...
    assert fake_gcs["list_calls"] == [("env-bucket", prefix)]
    # ...and the engine got a LOCAL path, never the gs:// URI.
    got = recording_engine.last_ctx.embedder_uri
    assert not got.startswith("gs://")
    assert got == str(tmp_path / "emb")


def test_setup_leaves_empty_embedder_uri_untouched(
    recording_engine, customers_schema
):
    """Empty URI ⇒ engine falls back to HashingEmbedder; no pull attempted."""
    ctx = GenerationContext(table_schema=customers_schema, embedder_uri="")
    _dofn(ctx).setup()
    assert recording_engine.last_ctx.embedder_uri == ""


def test_setup_passes_local_embedder_uri_through(
    recording_engine, tmp_path, customers_schema
):
    """A bare local path (M4 with staged weights) skips the pull, used as-is."""
    local = str(tmp_path / "staged-embedder")
    ctx = GenerationContext(table_schema=customers_schema, embedder_uri=local)
    _dofn(ctx).setup()
    assert recording_engine.last_ctx.embedder_uri == local


def test_setup_and_process_emit_milestones(
    caplog, recording_engine, customers_schema
):
    import logging

    ctx = GenerationContext(table_schema=customers_schema, embedder_uri="")
    dofn = _dofn(ctx)
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        dofn.setup()
        list(dofn.process({"n": 4, "batch_id": 0}))
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=dofn_setup_start" in text
    assert "SDFB_MILESTONE name=dofn_setup_done" in text
    assert "SDFB_MILESTONE name=batch_start" in text and "batch_id=0" in text
    assert "SDFB_MILESTONE name=batch_done" in text and "rows=4" in text
