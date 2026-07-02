"""Generation DoFn — instantiates the engine in `setup()`, yields records
from `process()`.

The DoFn carries an engine *name* (string) rather than an engine class
reference, looking the class up at worker setup time via
`sdfb_core.engines.get_engine(name)`. This keeps the DoFn picklable and
avoids leaking package-boundary class references across the
pickle/dill boundary.

Engine internal failures (an unexpected exception inside
`engine.generate_batch`) are caught here and routed to the `failed`
tagged output for the DLQ. Engine *silently dropped* invalid candidates
do NOT surface here — by contract, those are the engine's own
repair-loop concern.

REF: .claude/skills/engine-contract.md
REF: .claude/skills/beam-dofn.md
"""

from __future__ import annotations

import apache_beam as beam
from apache_beam.metrics import Metrics

from sdfb_core.engines import (
    GenerationConfig,
    GenerationContext,
    ModelClient,
    get_engine,
)


class GenerateRecordsDoFn(beam.DoFn):
    """Wraps a `GenerationEngine` inside Beam's worker lifecycle."""

    def __init__(
        self,
        engine_name: str,
        model_client: ModelClient,
        ctx: GenerationContext,
        similarity: float = 0.5,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.engine_name = engine_name
        self.model_client = model_client
        self.ctx = ctx
        self.similarity = similarity
        self.base_seed = seed
        self._engine = None  # built in setup()

        self._yielded = Metrics.counter("generation", "yielded")
        self._failed = Metrics.counter("generation", "failed")

    def setup(self):
        engine_class = get_engine(self.engine_name)
        self._engine = engine_class()
        self._engine.setup(self.model_client, self.ctx)

    def process(self, request):
        n = int(request["n"])
        batch_id = int(request["batch_id"])
        seed = None if self.base_seed is None else self.base_seed + batch_id
        cfg = GenerationConfig(
            seed=seed,
            batch_size=n,
            similarity=self.similarity,
        )
        try:
            for record in self._engine.generate_batch(n, cfg):  # type: ignore[union-attr]
                self._yielded.inc()
                # Python-mode dump keeps datetime / Decimal as Python
                # objects; downstream stages convert to DataFrame and
                # back as needed.
                yield record.model_dump(mode="python")
        except Exception as e:  # noqa: BLE001  — defensive at engine boundary
            self._failed.inc()
            yield beam.pvalue.TaggedOutput(
                "failed",
                {
                    "raw_request": request,
                    "error_type": "engine",
                    "error_detail": f"{type(e).__name__}: {e}",
                    "rule_id": "engine_failure",
                    "stage": "pre_write",
                },
            )

    def teardown(self):
        if self._engine is not None:
            try:
                self._engine.teardown()
            finally:
                self._engine = None
