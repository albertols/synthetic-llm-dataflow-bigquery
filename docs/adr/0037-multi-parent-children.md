# ADR 0037 — Multi-parent children: every declared FK edge gets a role and a DAG path

**Status:** ACCEPTED (laptop, 2026-09-11) — DirectRunner + unit tests on `ws12-fanout-generation`; Dataflow acceptance rides with the M4 relational launch
**Design:** [`2026-09-11-multi-parent-children.md`](../designs/2026-09-11-multi-parent-children.md) — the argument, the mechanism in detail, and the scale analysis. This ADR records the decision only.
**Evidence:** the three 2026-09-11 preflight stops on the five-table expansion of the anonymised core-accounts model (`ef66717`, `ca8f948`, `8573665`) and the registry shape sweep written afterwards (`packages/sdfb-tests/tests/unit/contracts/test_relationship_shapes.py`)
**Amends:** [ADR 0036](0036-parent-driven-fanout-generation.md) — D1's "a driven child has no side input at all" and D4's "anything that is neither driving nor implied is a `RelationshipError`" (see ADR 0036 **Rev 3**)
**Keeps:** ADR 0030's single-job DAG and generation waves · ADR 0031's side-input joint key pools and the `fk.orphan` gate (they ARE the independent path) · ADR 0032's `config/relationships/*.yaml` as the single source of truth (no new file format, no new YAML key) · ADR 0035's PK-capacity preflight (P4 gains two factors, not a new check) · ADR 0036's driving-edge guarantees, fan-out histogram, cell draw and seeding, unchanged

## Context

ADR 0036 gave a driven child exactly ONE parent: the driving edge. Every
other in-job enforced edge had to be **implied** — a subset of the
driving edge's columns, carried by the driving parent from that other
parent — or the launch stopped, because before ADR 0036 the engine wrote
each edge's FK columns in turn and the last one silently won.

That was the right stop for the shapes ADR 0036 saw. It is the wrong
stop for the shapes an operator actually declares. On 2026-09-11 the
five-table expansion of the anonymised core-accounts model (`A_TABLE`,
`C_TABLE`, `E_TABLE`, `F_TABLE` enabled; hub `B_TABLE` and `D_TABLE`
detached) stopped at preflight three times in one evening, each on a
shape ADR 0036 never saw:

- a root whose FK member points at a **disabled** parent — P4 counted a
  capacity factor for an edge the launch never draws (fixed `ef66717`);
- a pattern member whose space is ~3.6e27 strings — the duplicate-share
  formula cancelled to a false 100% stop (fixed `ca8f948`, ADR 0035);
- a true **1:1 child** whose PK *is* its driving edge — no cell table to
  check, so max fan-out 1 must pass and anything above 1 is a PK fault
  (fixed `8573665`).

The shape sweep written after those fixes
(`test_relationship_shapes.py`) pins what the registry resolves — hub
with many children, chain, tree, forest, 1:1 chains, child → parent and
grandparent — and names the two it cannot:

| shape | registry before this ADR | why |
|---|---|---|
| **star-schema fact**: child → `dim_a`, child → `dim_b`, no ancestry between them | stops | the second edge is neither driving nor implied |
| **true diamond**: `left`/`right` both under `top`, `bottom` → both | stops | the second edge shares `T` with the driving edge and adds `R` |

Both are ordinary warehouse shapes. Refusing them means the operator
must either detach a real parent or hand-mark an edge that has no
correct mark. The two edges do not need a stop — they need a role and a
mechanism.

## Decision

**D1 — five roles, assigned by the registry, one per enforced edge.**
`RelationshipRegistry.edge_roles(table)`
(`sdfb_core/contracts/relationships.py`) returns one of
`driving | implied | independent | conditional | external` for every
enforced edge of a table. `driving` and `implied` keep exactly the
meaning ADR 0036 D4 gave them; `external` (parent outside the launch) is
unchanged. The two new roles are decided by **column overlap with the
driving edge**, by child column name:

| role | when | DAG path | integrity |
|---|---|---|---|
| `independent` | no column shared with the driving edge (a star-schema dimension) | the ADR 0030/0031 sampled key pool as a side input, unchanged | by construction from the pool; the `fk.orphan` gate measures it |
| `conditional` | at least one column shared with the driving edge, and possibly more (a diamond branch) | a co-partitioned `CoGroupByKey` on the shared columns, before batching | by construction; unmatched keys follow D5 |

`edge_overlap(table, edge)` is the shared child columns in `edge.cols`
order; `edge_rest(table, edge)` is the remainder. An empty `rest` is a
pure **existence filter** (`(K)->P` driving and `(K)->Q` conditional:
the child's `K` must exist in `Q` too). Every internal edge now has a
role, so the only stop `edge_roles` still raises is D2's rule 5. The
card tags them `[enforced, independent]` and
`[enforced, conditional on (T)]`; `card.py --mermaid` labels them on the
edge.

**D2 — the driving rule is total; with no marker and no ancestry, the
first declared edge drives (ruling A).** In order:

1. one internal enforced edge → it drives (`driving_choice = "single"`);
2. exactly one edge marked `drives: true` → it (`"marked"`);
3. no marker: the parent that descends from every other candidate parent
   drives, and its edge to the other parent is widened with the child's
   pins (`"derived"`, ADR 0036 rev 2 — unchanged);
4. **no marker and no ancestry between the parents: the FIRST DECLARED
   internal enforced edge drives** (`"first_declared"`, ruling A,
   2026-09-11). The launcher logs `fk_driving_edge_defaulted table=
   edge= hint='mark drives: true to choose'` at WARNING and the card
   tags it `DRIVES (first declared — mark drives: true to choose)`;
5. more than one `drives: true` is still a `RelationshipError` — the one
   stop left.

Toggling `enabled` stays enough to launch; `drives: true` remains the
override. `driving_choice(table)` exposes which rule fired, so a report
never has to re-derive it. A duplicate `(cols, ref, ref_cols)` entry on
one table is a parse error ("declared twice") — two identical edges
would make "the first declared edge" a question of which copy.

**D3 — a conditional edge is a co-partitioned join on the shared
columns, capped at Top-M candidates per shared value.** The driving key
stream is keyed by the overlap columns; the conditional parent's valid
rows are projected to `(join_key, rest_value)` and reduced to at most
`M` candidates per join key by a deterministic
`blake2b(run_id, rest_value)` order (`Top.SmallestPerKey`), so a hot
shared key never carries an unbounded list. One `CoGroupByKey` per
conditional edge attaches `matches = {edge_id: [candidates per key]}` to
the existing Reshuffle → BatchElements → request payload
(`edge_id = ",".join(edge.cols)`). Parent rows whose join key holds a
NULL are dropped and counted (`fanout/candidates_dropped_null`). Inside
the engine, `sdfb_core.engines.fanout.conditional_values` shuffles the
key's candidate list once with `derive_key_seed(run_id, key, edge_id)`
and hands child `i` the `i % len`-th entry — without replacement until
the fan-out exceeds the candidate count, then wrapping — and writes it
on `rest`'s child columns after the pool draws and before the
driving/cell overrides. The shared columns always come from the driving
key, so the diamond's `T` is one value satisfying both parents by
construction. The join is keyed on narrow tuples, never on rows: no new
side input grows with the driving parent.

**D4 — `--fk_candidate_cap` (default 64) is the Top-M bound, and it is
a flag because the source's tail decides.**

![Candidate cap vs wrapping](../designs/assets/multi-parent-candidate-cap.png)

*Raising the cap buys back only the wrapping the cap itself caused.*
Formally, a shared value wraps iff its fan-out `k` exceeds
`min(c, M)`, where `c` is the distinct candidates the conditional parent
holds for that value; the wrapping attributable to `M` is confined to
`c ≥ k > M` — the orange wedge — while `k > c` is forced by the source
and identical at every cap (concept figure, seeded; the mechanism is
`_conditional_candidates` in `sdfb_beam/pipeline.py` and
`conditional_values` in `sdfb_core/engines/fanout.py`). 64 is the
default because it bounds a request at `keys_per_batch × M × |rest|`
values while leaving the overwhelming majority of shared values
unwrapped; raise it when a branch's within-key variety matters more than
the shuffle, lower it when a request gets too wide. `keys_per_batch` is
lowered at launch so a request never exceeds 100k candidate values.

**D5 — an unmatched conditional key writes NULL when it can, and is
dropped, counted and reported when it cannot (ruling B).** A driving key
whose shared value has no candidate in the conditional parent is not a
silent defect:

- when **every** `rest` column is NULLABLE in the landing schema, the
  engine writes NULL there — the tuple is then legitimately parentless
  and the ADR 0031 orphan query excludes it, as it already excludes
  every NULL tuple;
- otherwise `GenerateRecordsDoFn` removes the key **before** generation
  (no GPU spend on a row that cannot be valid), increments
  `fanout/keys_unmatched`, logs `batch_unmatched batch_id= keys_dropped=`
  and emits one DLQ envelope per dropped key with
  `rule_id="fk.unmatched"`, `error_type="referential_integrity"`,
  weighted by that key's expected rows (`n / len(keys)`) so the BLOCKER
  gate sees the rows that were lost rather than one envelope per batch.

A `rest` that is empty (a pure existence filter) is never nullable: an
unmatched key is always dropped and counted. `fk.unmatched` is a
distinct rule from `fk.orphan` on purpose — an orphan is a generator
regression and never expected; an unmatched key means the SOURCE lacks
that branch, which a report should read as an input fact.

**D6 — preflight P4 gains two per-key factors; nothing else about it
changes.** `_check_driven_pk` (`sdfb_beam/cli/preflight.py`) keeps its
shape (exact members only, ADR 0035/0036): an **independent** edge whose
columns sit in the child's PK contributes its pool cap as a per-key
factor, and a **conditional** edge whose `rest` sits in the PK
contributes at most `M`, so the check becomes
`max_k ≤ n_cells × Π pool_caps × Π M` or the launch stops naming the
edge (or `--fk_candidate_cap`). Members outside the PK never enter P4;
an inexact PK still skips it and streaming uniqueness measures
`pk.duplicate`.

**D7 — every role and every path is one milestone line.** Launcher:
`fk_edge_role edge= role= overlap=` per enforced edge (the `overlap=`
field is new), `fk_driving_edge_defaulted table= edge= hint=` (WARNING)
when rule 4 fired, `fk_edge_overlap_external table= edge= overlap=`
(WARNING) for the limitation in Consequences. Worker:
`relational_fk_edge mode=side_input|conditional overlap=` per edge and
`fanout_bound … conditional=<n> candidate_cap=` once per engine build.
Counters `fanout/candidates_dropped_null` and `fanout/keys_unmatched`;
DLQ rule `fk.unmatched`. No mermaid in any log (ADR 0036 rev 2).

## Alternatives considered

- **Serve a diamond branch from a side-input pool** (the ADR 0031 path,
  as for an independent edge). Rejected: the pool is drawn as a WHOLE
  tuple, so the branch would write its own value into the shared column
  `T` and then the driving key would overwrite it (or not, depending on
  ordering) — precisely the "last edge silently wins" corruption ADR
  0036 D4 stopped the launch to prevent. A shared column has one value;
  only a join conditioned on that value can pick a `rest` that is
  consistent with it.
- **Join rows after generation** (generate the child, then repair the
  branch columns with a join against the parent). Rejected: it shuffles
  ROWS, not keys — a child-sized shuffle at 100M rows, exactly the cost
  ADR 0034's single-barrier work removed — and a repair that changes a
  landed column after the PK cells were drawn re-opens the uniqueness
  question ADR 0036 D3 closed. The join in D3 moves narrow key tuples
  only, before batching.
- **Keep refusing multi-parent children** (ADR 0036 D1/D4 as written).
  Rejected by the evidence: the star fact and the true diamond are
  ordinary warehouse shapes, and the only workarounds are to detach a
  real parent (losing its integrity entirely) or to mark an edge
  `drives: true` when no mark is correct. A stop is right when the
  alternative is silent corruption; it is wrong when a correct mechanism
  exists.
- **Weight the conditional draw by the child's marginal** (IPF, as ADR
  0031 does for the independent pool). Deferred, not rejected:
  candidates are uniform within a match set today. The weighting would
  have to be fitted per shared value, and no evidence yet says the
  within-key marginal matters.

## Consequences

- **ADR 0036 D1's mutual exclusion is gone.** A driven child may carry
  side-input pools next to its driving edge; what remains on that path
  is disjoint from the driving columns by construction, because an
  overlapping edge is now `conditional`. `_route_parent_edges` no longer
  raises "a driven child's other in-job edges must be implied".
- **No model file changes.** No new YAML key, no new flag in
  `config/relationships/*.yaml`: roles are derived from `cols` and the
  model's own DAG. A model that stopped yesterday launches today.
- **One new CLI flag**, `--fk_candidate_cap` (default 64), and one new
  DLQ rule, `fk.unmatched`. `validation_runs.dlq_by_rule` gains a key
  that older runs do not have.
- **Cost per conditional edge**: one `Distinct` (skipped when the
  projection holds the parent's declared PK) plus one Top-M combine on
  the parent side, and one `CoGroupByKey` on the driving keys. Per
  request, `keys_per_batch × M × |rest|` values. The independent and
  driving paths are unchanged.
- **Limitations, named (design §9), not defects:**
  - *external parent with a column overlap* — the driver-side pool is
    drawn as a whole tuple and the driving edge overwrites the shared
    columns (B.2's existing rule). Logged once as
    `fk_edge_overlap_external` (WARNING); resolving it needs the parent
    inside the launch. Enable the parent.
  - *uniform candidates* — no weighting within a match set (see
    Alternatives).
  - *no correlation between branches* — a diamond's `L` and `R` are
    drawn independently given `T`, so the source's joint `(L, R)`
    distribution is not reproduced. Each branch's own marginal is.

## Acceptance

- [x] Registry: the shape sweep
  `packages/sdfb-tests/tests/unit/contracts/test_relationship_shapes.py`
  passes with both `xfail` markers removed (star fact and true diamond
  resolve — 10 passed, 0 xfail markers left on `ws12-fanout-generation`),
  and `test_relationship_models.py`'s two unmarked-parent cases now
  assert "first declared drives, the other is conditional" instead of a
  stop (43 passed).
- [ ] DirectRunner, fake client, whole-tuple checks — the five shapes in
  `packages/sdfb-tests/tests/unit/test_fanout_shapes.py`:
  - **star** — `dim_a`, `dim_b`, `fact`: every `fact` row's key tuple
    exists in each dimension, PK unique, row count within the histogram;
  - **diamond** — `top`, `left`, `right`, `bottom`: every `(T,L)` in
    `left`, every `(T,R)` in `right`, and the two `T`s are one value;
  - **existence filter** — `(K)->P` driving, `(K)->Q` conditional with
    empty rest: every landed `K` is in `Q`, and the keys that are not
    reach the DLQ as `fk.unmatched` with the expected-row weight;
  - **nullable branch** — `R` NULLABLE and half the `T`s absent from
    `right`: those rows land with `R = NULL` and no DLQ envelope;
  - **graph** — six tables mixing a tree, a star fact, a diamond and a
    1:1 chain: zero orphans on every enforced edge, `generation_waves`
    order respected.
- [ ] M4/Dataflow: a relational launch whose model declares a star or a
  diamond — `fk_edge_role … role=independent|conditional overlap=` and
  `relational_fk_edge mode=conditional` read as this ADR predicts, the
  RUN_PLAYBOOK §8.4 whole-tuple orphan query returns 0 on every enforced
  edge including the conditional ones, and `fk.unmatched` in
  `validation_runs.dlq_by_rule` is either 0 or explained by a branch the
  source genuinely lacks.
