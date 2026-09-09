# ADR 0035 — PK capacity counts FK-bound and categorical members; the FK key sample is sized by the child's PK

**Status:** ACCEPTED (2026-09-10) — laptop-verified (DirectRunner + unit tests); Dataflow acceptance pending the next three-table launch
**Evidence:** `2026-09-09_09_00_54-16364509521974163594` (`integration_tests/…/worker_logs.jsonl`) — the first `B_TABLE → C_TABLE → A_TABLE` launch, 10M rows/table: B_TABLE clean in 2h50m, C_TABLE's BLOCKER gate raised at 3h16m with `blocker_count=8789594 observed=0.879 > gate=0.2`, A_TABLE cancelled with it (`WORK_PROGRESS_UPDATE_LEASE_ALREADY_CANCELLED`)
**Amends:** [ADR 0028](0028-constraint-router-relational-plan.md) (P4 capacity check) · [ADR 0030](0030-single-job-relational-generation.md) (the flat 100k side-input cap) · [ADR 0031](0031-joint-fk-key-draws.md) D6 (the cap is announced — now also sized)
**Keeps:** [ADR 0031](0031-joint-fk-key-draws.md) joint draws and IPF weighting · [ADR 0034](0034-generation-throughput-single-barrier-shared-engines.md) single dedup barrier — the barrier did exactly its job; this ADR stops the launch that would feed it 8.8M duplicates
**Figure:** `scripts/doc/make_pk_capacity_figures.py` → `docs/designs/assets/pk-capacity-random-draws.png`

## Context

C_TABLE's PK is `(D_COL_001, C_COL_002, D_COL_018)`. Two earlier launches
of the `C_TABLE → A_TABLE` pair (2026-09-07, 2026-09-08) had B_TABLE
disabled in the model, so `D_COL_001` generated from its clause pattern
`^(E2F[13][0-9A-F]{20}|2301[0-9A-F]{20})$` — capacity ~10³⁶, no gate
trip. Enabling B_TABLE made the edge `D_COL_001 → B_TABLE` enforced, and
three things that were each correct on their own combined into a
collapse:

1. **The FK override.** `_bind_fk_key_pools` rewrites an FK member's
   profile to CATEGORICAL over the parent keys the child sees (ADR
   0031). The pattern route is gone; the column now draws from a set.
2. **The flat 100k cap.** `_edge_key_pools` samples at most 100 000
   parent key tuples into the side input (ADR 0030). B_TABLE landed 10M
   distinct keys; C_TABLE saw 1% of them (`fk_key_pool_capped` fired).
3. **The two categoricals.** `C_COL_002` and `D_COL_018` are small
   enums drawn at their empirical frequencies. Together they multiply
   the FK's 100k by ~12.

The PK tuple therefore had ~1.2M possible values for 10M rows, drawn at
random with no collision rejection. The uniqueness barrier (ADR 0034)
kept 1 210 406 rows and diverted 8 789 594 as `pk.duplicate`. Preflight
P4 (ADR 0028/0030) did not stop it because `_pk_capacity_factor` only
bounded STRING members with prompt constraints: an FK-bound member and a
plain categorical were both "unbounded", so P4 returned early.

![PK capacity under random draws](../designs/assets/pk-capacity-random-draws.png)

*Left — a PK tuple assembled from random draws is balls into bins:
capacity equal to `num_rows` still loses 36.8% of rows, and the run sat
at capacity/rows = 0.12 (measured 87.9%, predicted 87.9%). Right —
C_TABLE's capacity is (keys the child sees) × 12: 1.2M at the flat cap,
12M at this ADR's ceiling, 120M with the whole parent; the largest
gate-safe run under the ceiling is ~5.6M rows and even the whole parent
leaves 4% duplicates at 10M. `sdfb_core/engines/pk_capacity.py::expected_duplicate_share`,
`::fk_key_sample_cap`.*

## Decision

**D1 — P4 bounds FK-bound and categorical PK members.**
`preflight._pk_capacity_factor` now follows the engine's own routing:
an enforced FK edge whose columns sit in the PK counts ONCE, as the
number of parent key tuples the child will see (the sized sample cap,
bounded by the parent's row count when the parent generates in the same
job); an unconstrained CATEGORICAL member counts its observed domain, a
CONSTANT counts 1. Non-STRING members with cosmetic clauses stay
unbounded (the 2026-08-22 A_TABLE false stop, ADR 0030). The hard rule
`product < num_rows` is unchanged.

**D2 — random-draw members are judged against the gate, not the
product.** Members drawn without collision rejection (FK, categorical,
`values` enums, the free-text pool cap) make the tuple a
balls-into-bins process. P4 computes the expected `pk.duplicate` share
`1 − K/N·(1 − e^(−N/K))` and stops when it exceeds the run's
`blocker_failure_ratio`, naming the largest `num_rows` that stays under
the gate. Between 1% and the gate it logs `pk_capacity_tight`
(WARNING). Pattern-routed members keep ADR 0028's emitted-set rejection
and stay on the product rule alone.

**D3 — the FK key sample is sized by the child's PK, per edge.**
`FkEdgeSpec.key_sample_cap` replaces the module-level constant in the
composer. Preflight sizes it: `ceil(MARGIN × num_rows / other_capacity)`
where `other_capacity` is the product of the PK's non-FK members,
clamped to `[FK_KEY_SAMPLE_FLOOR = 100k, FK_KEY_SAMPLE_CEILING = 1M]`
(`sdfb_core/engines/pk_capacity.py`). `MARGIN = 10` puts expected
duplicates at ~4.8%. Edges outside the child's PK, and PKs with an
unbounded member, keep the floor — the ADR 0030 memory envelope is
untouched for every launch that was already fine.

**D4 — the ceiling is a stop, not a silent clamp.** When the sized cap
hits the ceiling and the expected share is still over the gate, the P4
message says so and names the ADR 0031 co-partitioned join as the path
beyond it. For this run's shape at 10M rows that is the outcome: the
operator's choices are `--num_rows ≤ ~5.6M` for C_TABLE, an unbounded PK
member, or the join.

## Alternatives considered

- **Raise the flat cap to the parent size.** 10M single-column string
  tuples is hundreds of MB per SDK process, ×8 processes under
  `sdk_containers=multi` (ADR 0034); ADR 0031 rejected it and this ADR
  keeps that rejection — hence a ceiling, sized only when needed.
- **Draw PK tuples without replacement in the engine.** Exact
  uniqueness at capacity ≥ N, but engine instances share no state
  across 32 DoFn instances, so "without replacement" is per-instance
  only; the honest version is a deterministic row-index → tuple
  enumeration with quota-allocated categorical marginals — a generator
  redesign, deferred.
- **Refuse any PK that contains an FK member.** Over-broad: the same
  shape at 1M rows lands fine with a sized sample (833k keys).
- **Warn only.** A warning at t=0 that the gate will trip at t+3h16m is
  a stop with extra steps.

## Consequences

- The 2026-09-09 launch shape stops at second zero with the expected
  share, the capacity breakdown per member, and the largest gate-safe
  `num_rows`; the same shape at 1M rows passes with an 833k-key side input
  instead of 100k.
- `fk_key_pool_capped` now reports the edge's sized cap, which may
  exceed 100k. RUN_PLAYBOOK's milestone table names both.
- Preflight profiles the reference sample once more per table
  (`profile_columns`, driver-side, milliseconds) — only when `num_rows`
  is set.
- `pk_capacity_tight` is a new WARNING milestone; a run that logs it
  will land fewer rows than requested (1–20% `pk.duplicate`) but pass.
- Deferred: the co-partitioned join beyond the ceiling; PK-tuple
  enumeration in the engine; an external parent's true key count
  (preflight uses the loader's limit as an upper bound, never a false
  stop).

## Acceptance

- [x] `tests/unit/engines/test_pk_capacity.py` reproduces the run's
  87.9% from `(10M, 1.21M)`; sizing hits the floor, the interior and the
  ceiling on the three shapes.
- [x] `tests/unit/cli/test_preflight_relational_capacity.py::TestP4FkBoundAndCategoricalMembers`
  — the launch shape stops at 10M and passes at 1M with the sized cap;
  an unbounded sibling keeps the floor; the product rule still applies
  without a gate.
- [x] `tests/unit/test_relational_pipeline.py::test_edge_key_sample_cap_bounds_the_parent_keys_the_child_sees`
  — DirectRunner: the composer honours the per-edge cap.
- [ ] Next M4 launch of `B_TABLE → C_TABLE → A_TABLE` at C_TABLE
  `--num_rows` under the reported gate-safe bound lands with
  `pk.duplicate` within the expected share and `fk_key_pool_bound
  key_tuples=` equal to the sized cap.
