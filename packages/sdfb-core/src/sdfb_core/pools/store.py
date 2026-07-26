"""The `FreeTextPoolStore` seam (WS5 §2.1).

Analogous to `ChunkStore`: the engine depends on this Protocol, the
BigQuery-backed implementation lives in `sdfb_beam.pools.store` so
`sdfb-core` stays GCP-free, and tests inject `InMemoryFreeTextPoolStore`.

A SIBLING of `ChunkStore`, not a `chunk_kind` of it — pools key on
`model_uri` (which LLM produced the values), chunks key on `embedder_id` /
`embedder_version` (which vector space). One fetch signature cannot serve
both without lying about one of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from sdfb_core.pools.record import FreeTextPool


@runtime_checkable
class FreeTextPoolStore(Protocol):
    """Read surface over `synthetic_rag.freetext_pools`."""

    def fetch(self, reference_digest: str, model_uri: str) -> list[FreeTextPool]:
        """Every persisted pool for this reference sample + LLM."""
        ...

    def exists(self, reference_digest: str, model_uri: str) -> bool:
        """True when ANY pool exists (the build stage's idempotency check)."""
        ...


class InMemoryFreeTextPoolStore:
    """List-backed `FreeTextPoolStore` for tests and laptop runs."""

    def __init__(self, pools: Iterable[FreeTextPool] = ()) -> None:
        self._pools: list[FreeTextPool] = list(pools)

    def add(self, pools: Iterable[FreeTextPool]) -> None:
        self._pools.extend(pools)

    def fetch(self, reference_digest: str, model_uri: str) -> list[FreeTextPool]:
        return [
            p
            for p in self._pools
            if p.reference_digest == reference_digest and p.model_uri == model_uri
        ]

    def exists(self, reference_digest: str, model_uri: str) -> bool:
        return any(
            p.reference_digest == reference_digest and p.model_uri == model_uri
            for p in self._pools
        )


__all__ = ["FreeTextPoolStore", "InMemoryFreeTextPoolStore"]
