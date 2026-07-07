"""Per-batch seed derivation for the no-``--seed`` case.

Without this, ``seed=None`` reaches the engines' ``_mix_seed(None) → 0`` path
and every batch replays an identical draw (the 97.6 %-duplicate defect from
the 2026-07 E2E report). Deriving from ``(run_id, batch_id)`` keeps runs
reproducible — re-running the same run_id reproduces the exact output — while
guaranteeing no two batches share an RNG stream.
"""

from __future__ import annotations

import hashlib


def derive_batch_seed(run_id: str, batch_id: int) -> int:
    """Stable, collision-resistant seed from (run_id, batch_id), in [0, 2^63)."""
    digest = hashlib.blake2b(
        f"{run_id}\x1f{batch_id}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") >> 1
