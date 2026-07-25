"""BgeEmbedder device selection + VRAM release (2026-07-25 E2E).

Both T4s sat idle while 33,610 chunks embedded on CPU (1,506 s). "auto"
uses CUDA when available; demote_to_cpu() releases VRAM afterward so
vLLM's ignition (which sizes its KV-cache budget from free memory) never
competes with a resident embedder. Fake torch modules keep this laptop-
runnable and deterministic.
"""

from __future__ import annotations

import sys
import types


def _fake_stack(monkeypatch, cuda_available: bool, log: dict):
    class _FakeModel:
        def to(self, device):
            log["moves"] = [*log.get("moves", []), device]
            return self

        def eval(self):
            return self

    class _Loader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return _FakeModel()

    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoModel = _Loader
    transformers_mod.AutoTokenizer = _Loader
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        empty_cache=lambda: log.__setitem__("emptied", log.get("emptied", 0) + 1),
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def test_auto_resolves_to_cuda_when_available(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=True, log=log)
    emb = BgeEmbedder(str(tmp_path), device="auto")
    assert emb.device == "cuda"
    assert log["moves"] == ["cuda"]


def test_auto_falls_back_to_cpu(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=False, log=log)
    emb = BgeEmbedder(str(tmp_path), device="auto")
    assert emb.device == "cpu"


def test_demote_to_cpu_moves_and_frees(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=True, log=log)
    emb = BgeEmbedder(str(tmp_path), device="auto")
    emb.demote_to_cpu()
    assert emb.device == "cpu"
    assert log["moves"] == ["cuda", "cpu"]
    assert log["emptied"] == 1
    emb.demote_to_cpu()  # idempotent — no second move/empty
    assert log["moves"] == ["cuda", "cpu"]
    assert log["emptied"] == 1


def test_explicit_cpu_never_touches_cuda(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=True, log=log)
    emb = BgeEmbedder(str(tmp_path))
    assert emb.device == "cpu"
    emb.demote_to_cpu()
    assert log.get("emptied") is None
