# ADR 0034 — Generation throughput: one dedup barrier, one engine per process, a fleet that starts full (and the multi-process experiment)

**Status:** ACCEPTED (2026-09-07) — laptop-proven (TDD, 1,379 tests, DirectRunner); the next R6/R7 launch pair is the acceptance gate
**Design:** [`docs/designs/2026-09-07-generation-throughput-where-time-goes.md`](../designs/2026-09-07-generation-throughput-where-time-goes.md)
**Evidence:** the 2026-08-29 R6 pair — cold `2026-08-29_07_33_36-13355700596190055276` (93.8 min) and its immediate warm re-trigger `2026-08-29_09_49_17-12681434869969021419` (86.1 min), both `C_TABLE ◄═ A_TABLE`, 10M rows/table, PK 1.0, 0/10M orphans, `worker_logs.jsonl` + `_full_report.md` in each
**Amends:** [ADR 0019](0019-rag-population-scoped-to-consumers.md) (embedder lifecycle) · [ADR 0030](0030-single-job-relational-generation.md) (launch topology) · [ADR 0033](0033-pool-ladder-integrity-at-scale.md) (prose ceiling) · the WS6 uniqueness modes ([design](../designs/2026-07-27-ws6-pipeline-shape.md))
**Keeps:** [ADR 0020](0020-freetext-pools-as-persisted-artifact.md) · [ADR 0023](0023-source-domain-pool-rejection.md) · [ADR 0031](0031-joint-fk-key-draws.md) · [ADR 0032](0032-relationships-as-config.md) — every fidelity, privacy and referential-integrity guarantee is unchanged

## Context

The R6 pair is the first cold + warm twin at 10M rows per table, and its
headline is clean and reproducible: PK uniqueness 1.0, 0 orphans by an
independent full-table join, `dlq_by_rule={}`, 67/67 + 44/45 stats
columns `ok`, `copy_fraction = 0` — and 93.8 min against 92.9 min for the
2026-08-26 10M run (+1 %). There is no regression. There is a ceiling,
and the worker logs say where it is:

1. **Warming buys 8 minutes.** The warm twin ignited vLLM zero times and
   still ran 86 min. The pool branch is the only phase warming removes;
   generation (23.5 + 19.2 min) and the dedup barriers (14.0 + 12.2 min)
   are the same in both runs. The GPU was busy (embeds + pool ladders)
   for ~11 of 94 minutes and was billed 274 GPU-minutes.
2. **Generation is bound by one interpreter per worker.** The GPU tiers
   pin `no_use_multiple_sdk_containers` (RUN_PLAYBOOK §3), so each
   `n1-highmem-8` runs ONE Python SDK process with 8 harness threads
   (`--dax_workflow_worker_num_threads_per_worker=8`). Every batch of 10k
   rows takes 26–29 s under that contention and the fleet's steady state
   is ~10.5k rows/s (C_TABLE) / ~12k (A_TABLE): four interpreters for
   32 vCPUs. `TotalVcpuTime` bills the other 28.
3. **The fleet started half-size.** Both jobs launched on 2 workers and
   the autoscaler added 2 more ~4 min into the first generate stage
   (three harness boots at 15:01 / 17:08; their SDK harnesses registered
   3–5 min later). C_TABLE's first 8 minutes ran at ~2.5k rows/s.
4. **Thirty-two engine builds per table.** 4 workers × 8 threads =
   32 `GenerateRecordsDoFn` instances, each building its own engine:
   chunk-store read, FAISS index, five pool-store reads, per-column
   source-domain and k-anonymity fetches, the FK key-pool IPF fit —
   2,940 thread-seconds on C_TABLE cold (p50 75 s, max 444 s), 2,824
   warm, serialized on process-level single-flight locks for state the
   sibling threads already held. A B.2 run pays its CTGAN fit and lazy
   pool ladders the same 32 times.
5. **Three full-row shuffle barriers per table.** `EnforceUniqueness`
   chains `CombineByRowDigest → CombineByPk → CombineByIdentity`; every
   row crosses Dataflow Shuffle three times as a keyed dict —
   123 GB per job, `Will retry read due to resource exhausted` storms
   during `CombineByPk`, a harness restart inside each barrier phase
   (15:29 / 17:36). 26 of 94 minutes.
6. **The report misread two gaps.** The "27.4-min FK parent-key-cap
   computation" is the child wave waiting for the parent wave
   (`fk_key_pool_capped` fires 25 s after C_TABLE's load job triggers);
   the "1,078 s generation stall" is `_fetch_identifier_domains` paging
   944,582 distinct `A_COL_005` values through the BigQuery REST row
   iterator at ~2.9k rows/s inside `DoFn.setup()`.
7. **One ADR 0033 acceptance miss.** `A_COL_019` is mask-gated in this
   run (`gate_lengths=mask`), so the prose-only length ceiling never
   engaged: pool values reached 48 characters against a 35-character
   source (p95 42 vs 35).

## Decision

**D1 — `uniqueness_mode=exact` runs ONE full-row barrier.** Rows cross
the shuffle once, keyed by digest (`CombineByRowDigest`), packed as value
tuples in schema order (about half the bytes of a keyed dict). The PK and
identity rules are resolved from key-only collision groups —
`(pk tuple → sorted digests)` for keys shared by ≥ 2 distinct rows,
combined map-side in the same fused stage — delivered as `AsDict` side
inputs to the barrier's read stage (`ResolveUniqueness`). Semantics are
preserved: one survivor per digest with `seen − 1` `row.duplicate`
envelopes; among digest-unique rows one survivor per PK tuple
(`pk.duplicate`); among PK survivors one survivor per identity tuple
(`identity.unique`). The survivor is the smallest digest — deterministic
where the chain was arbitrary — and PK/identity envelopes carry the
dropped row's own payload. The chain stays as `exact_chained` for A/B
runs; `streaming` is untouched.

**D2 — One engine per (engine, run, landing table, reference digest) per
worker process.** `GenerateRecordsDoFn` acquires its engine from a
process-level registry: the first DoFn builds (under a per-key lock;
siblings wait for one build instead of racing eight), every sibling
shares it, holders are refcounted, and the engine — with the
`ModelClient` it was built with — is torn down by the last holder. A
lone DoFn keeps the historical `engine_setup → engine_teardown →
client_teardown` order; a failed build is never shared. `engine_shared
holders=N` is the evidence line. B.2 gets the same seam for free: one
CTGAN fit and one lazy pool ladder per process instead of eight.

**D3 — The embedder loads on first use.** `BgeEmbedder` construction
records the path and the requested device; `ensure_loaded()` (called by
`embed()`) imports the HF stack, resolves the device, loads the weights
and moves them, once, process-serialized. `demote_to_cpu()` before any
load pins the eventual load to CPU. A store-warm generate setup never
embeds, so it never imports transformers, never loads 130 MB of weights
and never opens a CUDA context beside vLLM. The cold path (bulk embed on
CUDA → demote) is byte-identical.

**D4 — Source-domain fetches use the BigQuery Storage Read API.**
`BigQuerySourceValueStore` reads its result through
`RowIterator.to_arrow()`: large results stream through the Storage API
(the `google-cloud-bigquery-storage` client is on the worker image),
small ones come from the cached first page, and any Arrow-path failure
falls back to row iteration with `source_values_arrow_fallback`. Same
SQL, same cap semantics, same process cache.

**D5 — A scale run starts at its worker ceiling.** `initial_workers` is
a Flex Template parameter and Composer `Param`, pinned by the launcher
through `WorkerOptions.num_workers` (the `disk_size_gb` channel); an
explicit Beam `--num_workers` wins. `run_e2e.sh` tiers carry it as
`job.num_workers` (`R7`: 10M rows, 4 workers from the first second).

**D6 — The multi-process SDK topology is launchable and safe, as an
experiment.** `sdk_containers=multi` (Composer `Param`, `run_e2e.sh`
`job.sdk_containers`, tier `R7m`) lifts `no_use_multiple_sdk_containers`.
The launcher detects the topology from the experiments
(`sdk_container_topology` milestone) and builds the vLLM client with
`cross_process=True`: the pull → spawn → ready window is serialized
across processes by a bound loopback port (`_PortMutex`, `spawn_lock_port`
= 8001 — SDK containers share the host network, the same fact the reuse
probe relies on), losers wait and bind to the winner's server through the
reuse probe (`vllm_spawn_lock_wait` / `vllm_spawn_lock_acquired`), and
teardown keeps the server alive (`vllm_server_kept_alive`) because a
sibling process's refcount is invisible. D3 keeps the seven non-spawning
processes off the GPU. The default stays `single` until the R7m
acceptance run reads clean.

**D7 — The fixed-width ceiling applies to mask-gated columns.**
`_FormatGate.length_blind` (prose OR collapsed-mask gate) is where
`length_ceiling` clamps candidates before the format and novelty checks
(ADR 0033 D5 covered prose only).

## Consequences

- The dedup phase shrinks from six full-row shuffle passes per table to
  two, with the same envelopes and exact counts; the `resource exhausted`
  retry storms lose their cause. Expected: ~26 → ~10 min per job, to be
  read from the next run's `dominant_stages` and
  `TotalShuffleDataProcessed` (123 GB → ~24 GB if value tuples halve the
  per-row bytes as designed, ~44 GB if they do not — the figure hatches
  the projection).
- The first generate stage stops ramping for 8 minutes: 4 workers from
  launch (D5) and one engine build per process (D2) — the C_TABLE stage
  starts at its steady state; ~6 min per job. `dofn_setup_done`
  collapses from 32 entries per table to 4, `engine_shared holders=8` ×
  4 appears.
- `_fetch_identifier_domains` on a 944k-value domain drops from ~5.5 min
  to seconds (D4) — off the critical path in this pair, on it for any
  standalone child or cold parent with a large identifier domain.
- Under `sdk_containers=multi` the fleet gets 8 interpreters per worker
  for the CPU-bound generate stages (the pair's 10.5k rows/s was ~4
  interpreters); the ceiling moves to the shuffle write and BigQuery
  load. This is the one change that is **not laptop-provable**: the
  R7m run must show exactly one `vllm_spawn_lock_acquired` per worker,
  `vllm_spawn_lock_wait` on the others, no CUDA OOM, and a higher
  `batch_done` rate before the default flips.
- What this ADR does **not** do: it does not touch the launcher's 10-min
  flex-template phase or the 5-min worker boot (a slimmer launcher image
  is the next infra step, ADR 0009 territory); it does not change the T4
  KV budget that makes a 512-value pool ladder cost ~4.4 min (a
  quantized checkpoint is a `config/models.yml` decision); it does not
  alter the FK-column marginal trade-off (`C_COL_007` warn, ADR 0031);
  it leaves the A_COL_037 clause example (28 chars on a 31-char column,
  `prompt_constraint_example_off_format` fired again) to the operator.
- Laptop-verified only. Acceptance on the next cold + warm R6/R7 pair:
  `CombineByPk` absent from `dominant_stages`; `engine_shared` present
  and `dofn_setup_done` ≤ 4 per table; `initial_workers=4` → no harness
  boots after `workers_ready`; `identifier_source_filter` for
  `A_COL_005` under 30 s; `freetext_pool_length_clamped` on
  `A_COL_019` and `len_max == 35` in the crosscheck; PK 1.0 and 0
  orphans unchanged.
