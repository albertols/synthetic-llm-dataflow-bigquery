"""The `Embedder` seam for the B.1 RAG engine.

The engine never imports `transformers` / `torch` at module scope. It
depends only on the `Embedder` Protocol below. Two implementations:

  - `BgeEmbedder` — production. Loads `bge-small-en-v1.5` (384-dim) from a
    **local directory** (weights mirrored to GCS, pulled once on the M4).
    `transformers` / `torch` / `safetensors` are imported lazily inside
    `__init__`, so importing this module on a bare laptop never drags in
    those deps. NEVER calls `from_pretrained("BAAI/...")` against the Hub —
    only a local path. `HF_HUB_OFFLINE=1` is therefore safe.

  - `HashingEmbedder` — deterministic, dependency-free fallback used by the
    contract tests and as the engine's zero-config default when the real
    embedder's deps are not installed. It maps text → a fixed-dim unit
    vector via a seeded hash, so retrieval is exact and reproducible with
    no model download. NOT a semantic embedder — it exists so the engine is
    importable and testable on the laptop (the spine's fidelity primitives
    do the statistical work; the embedder only conditions which exemplars
    are retrieved).

REFs:
  - bge-small-en-v1.5: BAAI, MIT license, mirrored per config/models.yml
  - GReaT row serialization (what we embed): arXiv 2210.06280
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence


@runtime_checkable
class Embedder(Protocol):
    """Maps a batch of strings to fixed-dimension float vectors.

    Returned vectors are plain Python (``list[list[float]]``) so the seam
    never forces a NumPy dependency on callers. They need NOT be
    L2-normalized — the index normalizes on add/query.
    """

    @property
    def dim(self) -> int:
        """Embedding dimensionality (e.g. 384 for bge-small)."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input string, in input order."""
        ...


class HashingEmbedder:
    """Deterministic, dependency-free `Embedder`.

    Feature-hashing: each whitespace token contributes a signed unit to a
    bucket chosen by a salted SHA-256 of the token. The result is L2
    near-normalized but, more importantly, **identical across processes and
    runs** for the same input — which is exactly what the contract's
    `test_seed_reproducibility` and the "deterministic top-k" acceptance
    criterion require, with zero model download.

    Use `seed` to decorrelate buckets between independent indexes.
    """

    def __init__(self, dim: int = 384, seed: int = 0) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim
        self._seed = seed

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = text.split() or [text]
        for tok in tokens:
            bucket, sign = self._bucket_and_sign(tok)
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # Degenerate (e.g. empty text): put unit mass in a stable bucket.
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    def _bucket_and_sign(self, token: str) -> tuple[int, float]:
        h = hashlib.sha256()
        h.update(str(self._seed).encode("utf-8"))
        h.update(b"\x00")
        h.update(token.encode("utf-8"))
        digest = h.digest()
        bucket = int.from_bytes(digest[:8], "big") % self._dim
        sign = 1.0 if (digest[8] & 1) == 0 else -1.0
        return bucket, sign


class BgeEmbedder:
    """Production `Embedder` wrapping `bge-small-en-v1.5` on CPU.

    Loads from a **local directory** only (weights mirrored to GCS, warm-
    pulled on the M4). `transformers` + `torch` are imported lazily in
    `__init__` so this class can be referenced (and the module imported) on
    a laptop without the `[embedding]` extra installed. Mean-pooled,
    L2-normalized CLS-free pooling per the bge recipe.

    This class is exercised on the M4 (mark such tests `@pytest.mark.gpu`
    or guard on import availability); the contract tests use
    `HashingEmbedder` instead.
    """

    def __init__(
        self,
        model_path: str,
        *,
        dim: int = 384,
        max_length: int = 512,
        device: str = "cpu",
    ) -> None:
        # Lazy heavy imports — never at module scope (keeps sdfb-core pure).
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self._dim = dim
        self._max_length = max_length
        self._device = device
        # local_files_only=True is belt-and-braces on top of HF_HUB_OFFLINE=1:
        # a local path with this flag can never reach the Hub.
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_path, local_files_only=True
        )
        self._model = AutoModel.from_pretrained(
            model_path, local_files_only=True
        ).to(device)
        self._model.eval()

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        torch = self._torch
        out: list[list[float]] = []
        # Modest batches keep CPU memory bounded on a 10k-row reference.
        batch = 64
        text_list = list(texts)
        with torch.no_grad():
            for start in range(0, len(text_list), batch):
                chunk = text_list[start : start + batch]
                enc = self._tokenizer(
                    chunk,
                    padding=True,
                    truncation=True,
                    max_length=self._max_length,
                    return_tensors="pt",
                ).to(self._device)
                model_out = self._model(**enc)
                # bge uses mean pooling over the last hidden state.
                token_emb = model_out.last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).type_as(token_emb)
                summed = (token_emb * mask).sum(dim=1)
                counts = mask.sum(dim=1).clamp(min=1e-9)
                pooled = summed / counts
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                out.extend(pooled.cpu().tolist())
        return out


__all__ = ["BgeEmbedder", "Embedder", "HashingEmbedder"]
