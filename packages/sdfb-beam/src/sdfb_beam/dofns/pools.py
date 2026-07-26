"""Build free-text pools ONCE, in their own branch (WS5 §2).

Mirrors the rag_chunks population branch: an independent stage running
concurrently with Generate, writing an artifact keyed on the reference
digest. Without it every autoscaled worker process rebuilt all pools —
108 rebuilds and 68,805 s of LLM service time on the 2026-07-26 1M run,
because `_POOL_CACHE` lives for exactly one worker process.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import apache_beam as beam
from sdfb_core.engines import get_engine
from sdfb_core.observability import log_milestone
from sdfb_core.pools import FreeTextPool

from sdfb_beam.pools.store import pool_to_row

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator


class BuildFreeTextPoolsDoFn(beam.DoFn):
    """Run the pool ladder once and emit one `freetext_pools` row per column.

    The engine is built in `setup()` (never in `process()`) exactly as
    `GenerateRecordsDoFn` does; `process` only serialises what the ladder
    already produced.
    """

    def __init__(self, engine_name: str, model_client, ctx) -> None:
        self.engine_name = engine_name
        self.model_client = model_client
        self.ctx = ctx
        self._engine: Any = None

    def setup(self) -> None:
        # The build branch must never read its own output — otherwise it
        # would short-circuit itself into writing nothing on a re-run.
        ctx = self.ctx.model_copy(
            update={"pool_store": None, "freetext_pools_table": ""}
        )
        self.ctx = ctx
        engine_class = get_engine(self.engine_name)
        self._engine = engine_class()
        t0 = time.monotonic()
        self._engine.setup(self.model_client, ctx)
        log_milestone(
            "pool_branch_setup_done",
            seconds=round(time.monotonic() - t0, 1),
            engine=self.engine_name,
        )

    def process(self, _element) -> Iterator[dict]:
        pools = getattr(self._engine, "_free_text_pools", None) or {}
        for column, values in pools.items():
            if not values:
                continue
            yield pool_to_row(
                FreeTextPool(
                    reference_digest=self.ctx.reference_digest,
                    model_uri=self.ctx.model_uri,
                    column=column,
                    target=len(values),
                    values=tuple(values),
                    stagnated=bool(
                        getattr(self._engine, "_pool_stagnated", {}).get(column, False)
                    ),
                    attempts=int(
                        getattr(self._engine, "_pool_attempts", {}).get(column, 0)
                    ),
                )
            )
        log_milestone("pool_branch_emitted", columns=len(pools))

    def teardown(self) -> None:
        if self._engine is not None:
            self._engine.teardown()


__all__ = ["BuildFreeTextPoolsDoFn"]
