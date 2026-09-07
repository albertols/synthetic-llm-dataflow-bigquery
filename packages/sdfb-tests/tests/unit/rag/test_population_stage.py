"""WS2 §4b.1: the --build_rag_layer population branch, DirectRunner +
HashingEmbedder + in-memory sink. Chunk → embed → BQ-row shape."""

from __future__ import annotations

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline
from sdfb_beam.rag.population import (
    ChunkReferenceRowsDoFn,
    EmbedChunksDoFn,
    chunk_to_bq_row,
)
from sdfb_core.rag.chunking import chunk_row

_ROWS = [
    {"id": 1, "notes": "first note", "code": "A"},
    {"id": 2, "notes": None, "code": "B"},
]
_DOFN_KW = dict(
    source_fqn="p.d.t",
    reference_digest="dig",
    column_order=["id", "notes", "code"],
    free_text_columns=["notes"],
    pk_columns=["id"],
    embedder_id="hashing-384",
    embedder_version="v1",
)


def test_population_branch_end_to_end_direct_runner():
    with TestPipeline() as p:
        rows = (
            p
            | beam.Create(_ROWS)
            | "Chunk" >> beam.ParDo(ChunkReferenceRowsDoFn(**_DOFN_KW))
            | "Batch" >> beam.BatchElements(min_batch_size=1, max_batch_size=10)
            | "Embed" >> beam.ParDo(EmbedChunksDoFn(embedder_uri=""))
            | "Collect" >> beam.combiners.ToList()
        )

        def _check(out):
            # row 1 → row_doc + notes; row 2 → row_doc only (notes null)
            assert len(out) == 3
            kinds = sorted(r["chunk_kind"] for r in out)
            assert kinds == ["free_text_col", "row_doc", "row_doc"]
            for r in out:
                assert len(r["embedding"]) == 384
                assert abs(sum(v * v for v in r["embedding"]) - 1.0) < 1e-6
                assert r["reference_digest"] == "dig"
                assert r["embedder_id"] == "hashing-384"
                assert r["created_at"]

        _ = rows | beam.Map(lambda x, f=_check: f(x))


def test_chunk_to_bq_row_shape():
    chunk = chunk_row(_ROWS[0], **{k: _DOFN_KW[k] for k in (
        "column_order", "free_text_columns", "source_fqn",
        "reference_digest", "embedder_id", "embedder_version", "pk_columns",
    )})[0]
    row = chunk_to_bq_row(chunk, [0.0] * 4, "2026-07-20T00:00:00+00:00")
    assert row["chunk_id"] == chunk.chunk_id
    assert row["source_pk"] == '{"id": 1}'
    assert row["metadata"] == "{}"
    assert row["embedding"] == [0.0] * 4
    assert set(row) == {
        "chunk_id", "source_fqn", "source_pk", "row_digest",
        "reference_digest", "chunk_index", "chunk_kind", "chunk_text",
        "embedder_id", "embedder_version", "embedding", "metadata",
        "created_at",
    }


def test_population_branch_dedupes_values_and_caps_row_docs():
    """Wiring contract: row_docs only for the MAX_ROW_DOC_ROWS prefix,
    free_text_col chunks one per distinct (column, value)."""
    from sdfb_core.rag.chunking import (
        MAX_ROW_DOC_ROWS,
        chunk_free_text_value,
        distinct_free_text_values,
    )

    rows = [{"id": i, "notes": f"note {i % 3}", "code": "A"} for i in range(50)]
    values = distinct_free_text_values(rows, ["notes"])
    assert values == {"notes": ["note 0", "note 1", "note 2"]}

    with TestPipeline() as p:
        row_docs = (
            p
            | "Rows" >> beam.Create(rows[:MAX_ROW_DOC_ROWS])
            | "Chunk" >> beam.ParDo(
                ChunkReferenceRowsDoFn(**{**_DOFN_KW, "free_text_columns": []})
            )
        )
        value_chunks = (
            p
            | "Vals" >> beam.Create(
                [(c, v) for c, vs in values.items() for v in vs]
            )
            | "ValChunk" >> beam.MapTuple(
                lambda c, v: chunk_free_text_value(
                    c, v,
                    source_fqn=_DOFN_KW["source_fqn"],
                    reference_digest=_DOFN_KW["reference_digest"],
                    embedder_id=_DOFN_KW["embedder_id"],
                    embedder_version=_DOFN_KW["embedder_version"],
                )
            )
        )
        out = (
            (row_docs, value_chunks)
            | beam.Flatten()
            | "Fanout" >> beam.Reshuffle()
            | "Batch" >> beam.BatchElements(min_batch_size=1, max_batch_size=10)
            | "Embed" >> beam.ParDo(EmbedChunksDoFn(embedder_uri=""))
            | beam.combiners.ToList()
        )

        def _check(rows_out):
            kinds = [r["chunk_kind"] for r in rows_out]
            assert kinds.count("row_doc") == 50
            assert kinds.count("free_text_col") == 3  # deduped, not 50
            for r in rows_out:
                assert len(r["embedding"]) == 384

        _ = out | beam.Map(lambda x, f=_check: f(x))


def test_embed_dofn_teardown_demotes_gpu_embedder():
    class _FakeEmbedder:
        def __init__(self):
            self.demoted = 0

        def demote_to_cpu(self):
            self.demoted += 1

    dofn = EmbedChunksDoFn(embedder_uri="")
    dofn._embedder = _FakeEmbedder()
    fake = dofn._embedder
    dofn.teardown()
    assert fake.demoted == 1
    assert dofn._embedder is None


def test_embed_dofn_localizes_a_gcs_embedder_before_building_it(monkeypatch):
    """2026-07-28 R1 rerun: setup() imported EMBEDDER_LOCAL_DIR from
    dofns.generate, which no longer exports it (moved to dofns.localize in
    d2e1711) — ImportError killed RagEmbedChunks on every bundle. The gs://
    branch must resolve against the localize module and never a stale
    re-export."""
    import sdfb_beam.gcs as gcs_mod
    import sdfb_core.rag.embedding as embedding_mod
    from sdfb_beam.dofns import localize as localize_mod

    pulled = {}

    def _fake_pull(uri, dest):
        pulled["uri"] = uri
        pulled["dest"] = dest
        return dest

    class _SpyEmbedder:
        def __init__(self, uri, device="auto"):
            pulled["built_from"] = uri

    monkeypatch.setattr(gcs_mod, "localize_gcs_prefix", _fake_pull)
    monkeypatch.setattr(embedding_mod, "BgeEmbedder", _SpyEmbedder)

    dofn = EmbedChunksDoFn(embedder_uri="gs://bucket/models/embedders/bge/v1")
    dofn.setup()

    assert pulled["dest"] == localize_mod.EMBEDDER_LOCAL_DIR
    assert pulled["built_from"] == localize_mod.EMBEDDER_LOCAL_DIR, (
        "the embedder must be built from the local pull, never the gs:// URI"
    )


# ---------------------------------------------------------------------------
# ADR 0034 D6 follow-up (2026-09-07 R7m): under sdk_containers=multi the
# cold RAG population ran its embedder in 8 SDK processes at once, each
# holding a CUDA context + weights while vLLM tried to spawn — 8 x 20 s of
# `vllm_unfittable_wait` and a 344 s ignition (190 s single). The
# population embed runs on CPU under the multi topology; the pool branch's
# own embedder (one process) keeps "auto".
# ---------------------------------------------------------------------------
def test_pipeline_config_threads_the_rag_embed_device_to_the_population_dofn():
    import apache_beam as beam
    from sdfb_beam.pipeline import PipelineConfig, build_pipeline
    from sdfb_beam.rag.population import EmbedChunksDoFn
    from sdfb_core.contracts import TableSchema

    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [{"name": "v", "type": "STRING", "mode": "REQUIRED"}],
        }
    )

    def _embed_dofns(node):
        found = []
        for part in getattr(node, "parts", []):
            fn = getattr(part.transform, "fn", None)
            if isinstance(fn, EmbedChunksDoFn):
                found.append(fn)
            found.extend(_embed_dofns(part))
        return found

    def _build(device):
        cfg = PipelineConfig(
            table_schema=schema, engine_name="fake", model_client=None,
            num_rows=4, batch_size=4, run_id="r1", rag_embed_device=device,
        )
        p = beam.Pipeline()
        build_pipeline(
            p, reference_rows=[{"v": "a"}], config=cfg,
            landing_sink=beam.Map(lambda x: x), dlq_sink=beam.Map(lambda x: x),
            rag_chunks_sink=beam.Map(lambda x: x),
        )
        return _embed_dofns(p.transforms_stack[0])

    (default,) = _build("auto")
    assert default.device == "auto"
    (cpu,) = _build("cpu")
    assert cpu.device == "cpu"


def test_launcher_puts_population_embeds_on_cpu_under_the_multi_topology():
    from sdfb_beam.cli.run_pipeline import resolve_rag_embed_device

    class _Multi:
        cross_process = True

    class _Single:
        cross_process = False

    assert resolve_rag_embed_device(_Multi()) == "cpu"
    assert resolve_rag_embed_device(_Single()) == "auto"
    assert resolve_rag_embed_device(object()) == "auto"  # fake / mlx clients
