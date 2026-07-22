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

import time

import apache_beam as beam
from apache_beam.metrics import Metrics
from sdfb_core.engines import (
    GenerationConfig,
    GenerationContext,
    ModelClient,
    get_engine,
)
from sdfb_core.engines.identity import apply_identity_columns
from sdfb_core.observability import log_milestone
from sdfb_core.seeding import derive_batch_seed

# Where B.1's embedder weights land after the GCS warm-pull. Offline loaders
# (`transformers`) read from a local directory only — they cannot open a
# gs:// URI — so the DoFn pulls the prefix here before the engine builds its
# embedder. Mirrors the vLLM client's `/local-ssd/model` convention.
EMBEDDER_LOCAL_DIR = "/local-ssd/embedder"


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
        self._batch_seconds = Metrics.distribution("generation", "batch_msec")

    def setup(self):
        # The engine's embedder loads from a local directory only, so a gs://
        # `embedder_uri` must be warm-pulled to worker-local disk and the ctx
        # rewritten to the local path before the engine builds its embedder
        # (see GenerationContext.embedder_uri: "local paths … pulled by the
        # DoFn"). The LLM weights need no equivalent here — the ModelClient
        # pulls those itself in its own setup().
        t0 = time.monotonic()
        log_milestone("dofn_setup_start", engine=self.engine_name)
        ctx = self.ctx
        if ctx.embedder_uri.startswith("gs://"):
            from sdfb_beam.gcs import localize_gcs_prefix

            log_milestone("embedder_pull_start", uri=ctx.embedder_uri)
            t_pull = time.monotonic()
            local_dir = localize_gcs_prefix(ctx.embedder_uri, EMBEDDER_LOCAL_DIR)
            log_milestone(
                "embedder_pull_done",
                seconds=round(time.monotonic() - t_pull, 1),
            )
            ctx = ctx.model_copy(update={"embedder_uri": local_dir})
            self.ctx = ctx  # cache so a re-entrant setup() skips the pull

        # RAG read path (WS2 §4b.1): the BQ-backed ChunkStore cannot ride
        # the pickled graph — attach it worker-side, mirroring the
        # embedder localization above. Engines see only the ChunkStore
        # Protocol; an empty table just means the engine's fallback runs.
        if ctx.rag_chunks_table and ctx.chunk_store is None:
            from sdfb_beam.rag import store as rag_store

            ctx = ctx.model_copy(
                update={"chunk_store": rag_store.BigQueryChunkStore(ctx.rag_chunks_table)}
            )
            self.ctx = ctx

        # LLM ignition is LAZY (WS1 §3b): VLLMModelClient.generate_json()
        # calls its own idempotent, lock-serialized setup() on first use, so
        # a run whose columns never reach the LLM (b2 with only empirical/
        # identifier/jitter columns) never pays the vLLM bring-up — the
        # 2026-07-20 run spent 519 GPU-s igniting a server that generated
        # nothing. Failure stays loud: under strict_freetext a boot error
        # raises out of the first pool call. teardown() remains unconditional.

        engine_class = get_engine(self.engine_name)
        self._engine = engine_class()
        self._engine.setup(self.model_client, ctx)
        # Column name → BQ type, used to shape synthesized identity values
        # (STRING → UUIDv4, INTEGER/INT64 → non-negative int). Built once per
        # worker rather than per row.
        self._column_types = {
            column.name: column.bq_type for column in self.ctx.table_schema.columns
        }
        # Column name → BQ max_length, so a narrow STRING identity column
        # (e.g. VARCHAR(10)-style constraints) gets a truncated deterministic
        # value instead of the 36-char UUID overflowing it. Built once per
        # worker alongside `_column_types`.
        self._column_max_lengths = {
            column.name: column.max_length for column in self.ctx.table_schema.columns
        }
        log_milestone(
            "dofn_setup_done",
            engine=self.engine_name,
            seconds=round(time.monotonic() - t0, 1),
        )

    def process(self, request):
        n = int(request["n"])
        batch_id = int(request["batch_id"])
        if self.base_seed is None:
            # No explicit seed: derive one so batches never replay each other
            # while the run stays reproducible per run_id (E2E report §2).
            seed = derive_batch_seed(self.ctx.pipeline_run_id, batch_id)
            # Batch-independent seed for once-per-worker artifacts (the B.2
            # free-text pool build): stable within a run, varies across runs
            # via the salted run_id. batch_id=-1 keeps it outside every real
            # batch's seed namespace.
            pool_seed = derive_batch_seed(self.ctx.pipeline_run_id, -1)
        else:
            seed = self.base_seed + batch_id
            # Explicit seed ⇒ the pool build is reproducible across reruns
            # regardless of which batch reaches the worker first (P6).
            pool_seed = self.base_seed
        cfg = GenerationConfig(
            seed=seed,
            batch_size=n,
            similarity=self.similarity,
            engine_specific={"pool_seed": pool_seed},
        )
        log_milestone("batch_start", batch_id=batch_id, n=n)
        t0 = time.monotonic()
        count = 0
        try:
            for row_index, record in enumerate(
                self._engine.generate_batch(n, cfg)  # type: ignore[union-attr]
            ):
                self._yielded.inc()
                count += 1
                # Python-mode dump keeps datetime / Decimal as Python
                # objects; downstream stages convert to DataFrame and
                # back as needed.
                row = record.model_dump(mode="python")
                if self.ctx.identity_columns:
                    row = apply_identity_columns(
                        row,
                        identity_columns=self.ctx.identity_columns,
                        column_types=self._column_types,
                        column_max_lengths=self._column_max_lengths,
                        run_id=self.ctx.pipeline_run_id,
                        batch_id=batch_id,
                        row_index=row_index,
                    )
                yield row
            log_milestone(
                "batch_done",
                batch_id=batch_id,
                rows=count,
                seconds=round(time.monotonic() - t0, 1),
            )
            self._batch_seconds.update(int((time.monotonic() - t0) * 1000))
        except Exception as e:
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
        try:
            if self._engine is not None:
                try:
                    self._engine.teardown()
                finally:
                    self._engine = None
        finally:
            client_teardown = getattr(self.model_client, "teardown", None)
            if callable(client_teardown):
                client_teardown()
