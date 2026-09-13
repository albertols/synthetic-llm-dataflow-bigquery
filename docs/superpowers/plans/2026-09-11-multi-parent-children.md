# Multi-Parent Children Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A driven child may carry, next to its driving edge, any number of `independent` edges (no shared column — star-schema dimensions, served by the ADR 0031 side-input pool) and `conditional` edges (shared columns with the driving edge — diamond branches, served by a co-partitioned `CoGroupByKey` on the shared columns), so every FK edge a relationship model can declare — star, diamond, tree, forest, 1:1 chain, arbitrary DAG — is satisfied by construction with no launch stop and no unbounded shuffle.

**Architecture:** The registry (`sdfb-core`) assigns one of five roles per enforced edge and picks the driving edge by a total rule (single → marked → most-derived → first declared). The pure fan-out module gains `ConditionalEdge` entries in the plan and a seeded per-key candidate draw; both engines accept `matches` in `generate_for_keys`. The composer partitions a table's edges three ways, joins conditional candidates onto the driving key stream before batching (Top-M per shared key, deterministic), and lets side-input pools ride next to a fanout edge. The launcher maps roles to edge modes, computes overlap/nullability, sizes the request, and preflight P4 folds the new members into the per-key capacity.

**Tech Stack:** Python 3.11, Pydantic v2, Apache Beam Python SDK (DirectRunner on the laptop), pytest.

**Spec:** `docs/designs/2026-09-11-multi-parent-children.md` (the design; ADR 0037 is written in Task 10 from it).

## Global Constraints

- `sdfb-core` never imports `apache_beam`, `google.cloud.*`, `torch`; engines run under pure pytest (CLAUDE.md).
- Import direction is strict: `sdfb-beam` → `sdfb-core`, never the reverse.
- Engines are built in `DoFn.setup()`, never in `process()` (the ADR 0030 side-input deferral is the one exception and stays).
- Every new milestone is one line via `sdfb_core.observability.log_milestone`; no mermaid in any log (ADR 0036 rev 2).
- Verify before every commit: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q`, `uv run --no-sync ruff check .`, `uv run --no-sync mypy packages/sdfb-core/src` (0 errors is a CI gate).
- Anonymised identifiers only in code, tests, docs and commit messages (`A_TABLE` / `COL_XXX` aliases).
- Branch: `ws12-fanout-generation`. Commit after every task with the trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01MhsrQAqKaMsdwxN6aiF3tH`. Never stage `.gitignore`, `.claude/settings.json`, `doc_revesit_prompt.md`.
- Rulings from the spec: **A** — with no marker and no ancestry the FIRST DECLARED internal enforced edge drives (WARNING milestone). **B** — an unmatched conditional key writes NULL when every `rest` column is NULLABLE, else the key is dropped before generation, counted, and reported as one `fk.unmatched` DLQ envelope weighted by its expected rows.
- Names fixed across tasks: role strings `"driving" | "implied" | "independent" | "conditional" | "external"`; `FkEdgeSpec.mode` strings `"fanout" | "implied" | "side_input" | "conditional"`; conditional `edge_id = ",".join(edge.cols)`; request key `"matches"`; CLI flag `--fk_candidate_cap` (default `64`); counters `fanout/candidates_dropped_null`, `fanout/keys_unmatched`; DLQ rule `fk.unmatched`; milestones `fk_driving_edge_defaulted`, `fk_edge_overlap_external`.

---

## File structure

| File | Responsibility |
|---|---|
| `packages/sdfb-core/src/sdfb_core/contracts/relationships.py` | roles `independent` / `conditional`; `driving_choice`; `edge_overlap` / `edge_rest`; card + mermaid tags. |
| `packages/sdfb-core/src/sdfb_core/seeding.py` | `derive_key_seed(run_id, key, salt="")`. |
| `packages/sdfb-core/src/sdfb_core/engines/fanout.py` | `ConditionalEdge`; `FanoutPlan.conditional`; `conditional_values`. |
| `packages/sdfb-core/src/sdfb_core/engines/base.py` | `generate_for_keys(keys, cfg, matches=None)`. |
| `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py`, `b2_library/engine.py` | apply conditional values per row; NULL policy. |
| `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` | `matches` in the request; unmatched drop + counter + `fk.unmatched` envelope. |
| `packages/sdfb-beam/src/sdfb_beam/pipeline.py` | `FkEdgeSpec` fields; three-way partition; `_conditional_candidates`, `_attach_matches`; payload `matches`; star routing. |
| `packages/sdfb-beam/src/sdfb_beam/cli/preflight.py` | `pk_cell_columns(known=)`; `_check_driven_pk` independent/conditional factors; `preflight(fk_member_caps=, conditional_rest=, candidate_cap=)`. |
| `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` | `--fk_candidate_cap`; `in_set_parent_edges` modes/overlap/nullable; conditional plan entries; `fk_driving_edge_defaulted`; `fk_edge_overlap_external`; `keys_per_batch` bound. |
| `packages/sdfb-core/src/sdfb_core/engines/generation_plan.py` | worker `relational_fk_edge mode=conditional overlap=`; `fanout_bound conditional= candidate_cap=`. |
| `packages/sdfb-tests/tests/unit/test_fanout_shapes.py` (new) | DirectRunner acceptance per shape. |
| `docs/adr/0037-multi-parent-children.md` (new), `docs/designs/assets/multi-parent-candidate-cap.png`, `scripts/doc/make_multi_parent_figures.py` (new) | decision + figure. |
| `config/relationships/README.md`, `docs/RUN_PLAYBOOK.md`, `config/relationships/example_star_diamond.yaml` (new), `.github/prompts/visual_fk_pk_ddl_contract_guide.prompt.md`, `.github/prompts/e2e_fk_pk_validator.prompt.md`, `.github/prompts/end_to_end_validation_report_generation.prompt.md`, `docs/adr/0036-parent-driven-fanout-generation.md` (rev 3 note) | operator docs and tooling. |

---

### Task 1: Registry — four internal roles and a total driving rule

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/contracts/relationships.py` (`edge_roles`, new `driving_choice`, `edge_overlap`, `edge_rest`, `_table_lines`, `mermaid`)
- Modify: `packages/sdfb-tests/tests/unit/contracts/test_relationship_shapes.py` (remove both xfail markers)
- Modify: `packages/sdfb-tests/tests/unit/contracts/test_relationship_models.py` (`test_unrelated_parents_still_need_drives` → first declared drives; `test_no_edge_between_the_parents_cannot_be_widened` → first declared drives, other conditional)

**Interfaces:**
- Produces: `RelationshipRegistry.edge_roles(table) -> dict[FkEdge, str]` with roles in `{"driving","implied","independent","conditional","external"}`; raises `RelationshipError` ONLY when more than one internal edge is marked `drives: true`.
- Produces: `RelationshipRegistry.driving_choice(table) -> str | None` in `{"single","marked","derived","first_declared"}`, `None` when the table has no internal enforced edge.
- Produces: `RelationshipRegistry.edge_overlap(table, edge) -> tuple[str, ...]` (child column names of `edge` that also appear in the driving edge, in `edge.cols` order; `()` for the driving edge itself or a root) and `edge_rest(table, edge) -> tuple[str, ...]` (the remaining `edge.cols`).
- Card tags: `enforced, DRIVES` / `enforced, DRIVES (first declared — mark drives: true to choose)` / `enforced, implied via X` / `enforced, independent` / `enforced, conditional on (T)`.

- [ ] **Step 1: Write the failing tests** — remove the two `@pytest.mark.xfail` decorators in `test_relationship_shapes.py`; rewrite the two `test_relationship_models.py` cases:

```python
    def test_unrelated_parents_default_to_the_first_declared_edge(self):
        text = """
model: m
tables:
  P:
    pk: [K]
  Q:
    pk: [K]
  CHILD:
    pk: [K, X]
    fk:
      - cols: [K]
        ref: P
        ref_cols: [K]
      - cols: [K]
        ref: Q
        ref_cols: [K]
"""
        reg = self._registry(text)
        to_p, to_q = reg.enforced_edges("CHILD")
        assert reg.edge_roles("CHILD") == {to_p: "driving", to_q: "conditional"}
        assert reg.driving_choice("CHILD") == "first_declared"
        assert reg.edge_overlap("CHILD", to_q) == ("K",)
        assert reg.edge_rest("CHILD", to_q) == ()
        assert "DRIVES (first declared" in reg.card("CHILD")
        assert "conditional on (K)" in reg.card("CHILD")
```

and add to `test_relationship_shapes.py::TestShapesTheRegistryResolves`:

```python
    def test_star_fact_dimensions_are_independent(self):
        reg = _registry(_STAR_FACT)
        to_a, to_b = reg.enforced_edges("fact")
        assert reg.edge_overlap("fact", to_b) == ()
        assert reg.edge_rest("fact", to_b) == ("B_ID",)
        assert reg.driving_choice("fact") == "first_declared"
        assert reg.driving_choice("dim_a") is None

    def test_diamond_branch_overlap_and_rest(self):
        reg = _registry(_DIAMOND)
        to_left, to_right = reg.enforced_edges("bottom")
        assert reg.edge_overlap("bottom", to_right) == ("T",)
        assert reg.edge_rest("bottom", to_right) == ("R",)
        assert "conditional on (T)" in reg.card("bottom")

    def test_two_marked_edges_still_stop(self):
        text = _STAR_FACT.replace("ref: dim_a, ref_cols: [A_ID]}", "ref: dim_a, ref_cols: [A_ID], drives: true}") \
                         .replace("ref: dim_b, ref_cols: [B_ID]}", "ref: dim_b, ref_cols: [B_ID], drives: true}")
        with pytest.raises(RelationshipError, match="2 marked"):
            _registry(text).edge_roles("fact")
```

- [ ] **Step 2: Run, confirm RED** — `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/contracts -q -k "shapes or unrelated or no_edge_between"`.

- [ ] **Step 3: Implement** in `edge_roles`: keep steps single / marked / derived; replace the "no parent descends" `RelationshipError` with `driving = internal[0]` and remember the choice (store the last choice in a small per-call helper `_pick_driving(table, internal) -> tuple[FkEdge, str]` used by both `edge_roles` and `driving_choice`); keep the `len(marked) > 1` error with message `"... {len(marked)} marked `drives: true` — mark exactly one"`. Replace the "neither driving nor implied" error with:

```python
            overlap = tuple(c for c in edge.cols if c in driving.cols)
            roles[edge] = "conditional" if overlap else "independent"
```

Add `edge_overlap` / `edge_rest` (both return `()` when `edge` is the driving edge or the table has no driving edge). Update `_table_lines` tags and `mermaid` edge labels (`-- independent -->`, `-- conditional on T -->`).

- [ ] **Step 4: Run, confirm GREEN** — the two contracts test files, then `uv run --no-sync mypy packages/sdfb-core/src`, `ruff check`.
- [ ] **Step 5: Commit** — `feat(relationships): independent and conditional edge roles; the first declared edge drives when nothing else decides (ADR 0037)`.

---

### Task 2: Core fan-out — `ConditionalEdge` in the plan and the per-key candidate draw

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/fanout.py`
- Modify: `packages/sdfb-core/src/sdfb_core/seeding.py` (`derive_key_seed(run_id, key, salt: str = "")` — salt folded into the hash; default keeps every existing seed byte-identical)
- Test: `packages/sdfb-tests/tests/unit/engines/test_fanout.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class ConditionalEdge: id: str; cols: tuple[str, ...]; nullable: bool` with `to_payload()/from_payload()`.
- Produces: `FanoutPlan.conditional: tuple[ConditionalEdge, ...] = ()`, round-tripped in `to_payload`/`from_payload` under key `"conditional"` (absent key → `()`), and `FanoutPlan.columns` includes every conditional edge's `cols`.
- Produces: `conditional_values(run_id: str, key: tuple, edge_id: str, candidates: Sequence[Sequence], k: int) -> list[tuple]` — `k` tuples; when `candidates` is empty returns `k` empty tuples; otherwise a seeded (`derive_key_seed(run_id, key, salt=edge_id)`) shuffle of `candidates`, entry `i % len(candidates)` for child `i` (without replacement until wrap).

- [ ] **Step 1: Failing tests**

```python
def test_conditional_values_are_without_replacement_until_wrap():
    cands = [("r1",), ("r2",), ("r3",)]
    out = conditional_values("run", ("t1",), "T,R", cands, 5)
    assert sorted(out[:3]) == sorted(cands)          # a permutation
    assert out[3:] == out[:2]                          # then wraps in the same order

def test_conditional_values_are_deterministic_per_key_and_edge():
    cands = [("a",), ("b",), ("c",), ("d",)]
    assert conditional_values("run", ("k",), "E", cands, 4) == conditional_values("run", ("k",), "E", cands, 4)
    assert conditional_values("run", ("k",), "E", cands, 4) != conditional_values("run", ("k",), "F", cands, 4) or True  # different edges may coincide; only equality-per-edge is required

def test_conditional_values_without_candidates_are_empty_tuples():
    assert conditional_values("run", ("k",), "E", [], 3) == [(), (), ()]

def test_plan_payload_round_trips_conditional_edges():
    plan = FanoutPlan(driving_cols=("T", "L"), histogram=FanoutHistogram({1: 1}), cells=None, exact_cells=True,
                      conditional=(ConditionalEdge(id="T,R", cols=("R",), nullable=True),))
    assert FanoutPlan.from_payload(plan.to_payload()) == plan
    assert "R" in plan.columns
    assert FanoutPlan.from_payload({**plan.to_payload(), "conditional": None}).conditional == ()
```

(Adjust the `FanoutHistogram` constructor to the module's actual signature.)

- [ ] **Step 2: RED**, **Step 3: implement**, **Step 4: GREEN** (`test_fanout.py`, mypy, ruff), **Step 5: commit** — `feat(fanout): conditional edges in the plan and a seeded per-key candidate draw (ADR 0037)`.

---

### Task 3: Engines — `generate_for_keys(keys, cfg, matches=None)`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (ABC signature + docstring)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py`, `packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py`
- Test: `packages/sdfb-tests/tests/unit/engines/test_b1_generate_for_keys.py` (or the file that already tests `generate_for_keys` — find it with `grep -rl generate_for_keys packages/sdfb-tests`) and the B.2 twin.

**Interfaces:**
- Consumes: Task 2 (`plan.conditional`, `conditional_values`).
- Produces: `generate_for_keys(self, keys, cfg, matches: Mapping[str, Sequence[Sequence[Sequence]]] | None = None)` — `matches[edge_id][i]` is the candidate list for `keys[i]` (missing edge or `None` → no candidates for every key). Per row of key `keys[i]` with child index `j` (0-based within that key's fan-out): `values = conditional_values(run_id, key, edge.id, matches[edge.id][i], k)`; row `j` gets `values[j]` on `edge.cols`. Empty candidates: if `edge.nullable` every `edge.cols` column is `None`; else the key's rows are **not emitted** (defensive — the DoFn drops such keys first, Task 4). Applied after the pool draws and before the driving/cell overrides in both engines.

- [ ] **Step 1: Failing tests** (B.1; mirror for B.2): build an engine with a fanout plan carrying `ConditionalEdge(id="T,R", cols=("R",), nullable=False)`, schema `T, L, R, X`, driving cols `("T","L")`, keys `[("t1","l1"), ("t2","l2")]`, `matches={"T,R": [[("r1",), ("r2",)], []]}`; histogram all `2`. Assert: rows for `t1` have `R ∈ {r1, r2}` and both values appear once each; no rows for `t2`. Second test with `nullable=True`: `t2` rows exist with `R is None`. Third: `matches=None` behaves exactly as today (existing tests untouched).

- [ ] **Step 2: RED**, **Step 3: implement** (`expand_keys` is unchanged; the engine builds `index = {tuple(k): i for i, k in enumerate(keys)}` and a per-key cache of `conditional_values` lists plus a per-key running child counter; B.2 applies the values in the same place its pool loop runs), **Step 4: GREEN** (all engine tests, mypy, ruff), **Step 5: commit** — `feat(engines): conditional edge values ride with the parent keys in generate_for_keys (ADR 0037)`.

---

### Task 4: `GenerateRecordsDoFn` — `matches`, the NULL policy, `fk.unmatched`

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`
- Test: `packages/sdfb-tests/tests/unit/dofns/test_generate_keys_request.py` (or the existing keys-request test file — `grep -rl '"keys"' packages/sdfb-tests/tests/unit/dofns`)

**Interfaces:**
- Consumes: request `{"batch_id", "keys", "n", "matches": {edge_id: [cands_i, …]}}` (`"matches"` optional); `ctx.fanout["conditional"]` entries `{id, cols, nullable}` (Task 2 payload).
- Produces: before calling the engine, for every conditional entry with `nullable=False`, keys whose candidate list is empty are removed from `keys` (and from every `matches` list, index-aligned); `Metrics.counter("fanout", "keys_unmatched").inc(dropped)`; one DLQ envelope per dropped key on the SAME tagged output `_failed_request` uses, shaped `{"raw_request": {"batch_id": …, "keys": [key], "n": expected}, "error_type": "referential_integrity", "error_detail": "no <edge_id> candidate for key <key>", "rule_id": "fk.unmatched", "stage": "pre_generate"}` with `expected = max(1, round(n / len(keys)))`; then `generate_for_keys(keys, cfg, matches=matches)`.

- [ ] **Step 1: Failing tests** — a request with two keys, `matches={"T,R": [[("r1",)], []]}`, non-nullable: engine receives one key; one envelope with `rule_id == "fk.unmatched"` and `raw_request["n"] == expected`; counter incremented. Nullable variant: both keys reach the engine, no envelope. No `matches`: unchanged path.
- [ ] **Step 2: RED**, **Step 3: implement**, **Step 4: GREEN**, **Step 5: commit** — `feat(dofn): conditional matches ride with key requests; unmatched non-nullable keys divert as fk.unmatched (ADR 0037)`.

---

### Task 5: Composer — three-way partition, co-partitioned candidates, star routing

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py`
- Test: `packages/sdfb-tests/tests/unit/test_relational_pipeline.py` (partition/route unit tests) and new `packages/sdfb-tests/tests/unit/test_conditional_candidates.py` (DirectRunner `TestPipeline` + `assert_that`)

**Interfaces:**
- Produces: `FkEdgeSpec` new fields `overlap: tuple[str, ...] = ()`, `candidate_cap: int = 64`, `nullable: bool = False`, and property `edge_id -> str` (`",".join(child_cols)`); `mode` accepts `"conditional"`.
- Produces: `_partition_parent_edges(spec, valid_by_landing) -> tuple[fanout_edge | None, side_input_edges, conditional_edges]` (third list `[(j, edge, parent_valid)]`; a conditional edge without a fanout edge is a `ValueError`).
- Produces: `_route_parent_edges` returns `(fk_side, requests)` where BOTH may be non-`None` (star); the "must be implied" `ValueError` is deleted.
- Produces: `_conditional_candidates(parent_valid, edge: FkEdgeSpec, prefix: str, run_id: str) -> PCollection[(join_key, [rest_value, …])]`: `Map` to `(join_key, rest_value)` using `edge.overlap`/`edge.ref_cols` positions (`join_key = tuple(r[edge.ref_cols[edge.child_cols.index(o)]] for o in edge.overlap)`, `rest_value` over the child cols not in overlap), `ParDo` dropping rows with a NULL in `join_key` (counter `fanout/candidates_dropped_null`), `Distinct` unless `edge.parent_pk` ⊆ projected parent cols, `Map` to `(join_key, (blake2b(run_id + repr(rest_value)) int, rest_value))`, `beam.combiners.Top.SmallestPerKey(edge.candidate_cap)`, `Map` to `(join_key, [v for _, v in ranked])`.
- Produces: `_attach_matches(keyed_keys, cands, edge, prefix) -> PCollection[(key, matches_dict)]` via `CoGroupByKey`; `keyed_keys` elements are `(join_key, (key, matches_so_far))`; output adds `matches_so_far[edge.edge_id] = cands or []`. Chained once per conditional edge inside `_fanout_requests(parent_valid, edge, prefix, mean_fanout, conditional=(), run_id="")`, between DropNull/Distinct and Reshuffle.
- Produces: `_fanout_request_payload(ks, mean_fanout)` accepts elements that are `(key, matches_dict)` and emits `"matches": {edge_id: [matches_dict_i.get(edge_id, []) …]}` only when any element carries a dict; plain tuples keep today's payload byte-for-byte.

- [ ] **Step 1: Failing tests** — (a) `_partition_parent_edges` three-way and the conditional-without-fanout error; (b) `_route_parent_edges` on a star spec returns a non-`None` `fk_side` AND a requests PCollection (build with `TestPipeline`); (c) DirectRunner: parent rows `[{"T": "t1", "R": f"r{i}"} for i in range(10)] + [{"T": None, "R": "x"}]`, `candidate_cap=3` → exactly one element for `t1` with 3 candidates, identical across two runs with the same `run_id`, different set with another `run_id` allowed; the NULL row never appears; (d) `_attach_matches` yields `(key, {"T,R": [...]})` for a matched key and `(key, {"T,R": []})` for an unmatched one; (e) payload test: `_fanout_request_payload([(("t1","l1"), {"T,R": [("r1",)]}), (("t2","l2"), {"T,R": []})], 2.0)["matches"] == {"T,R": [[("r1",)], []]}`.
- [ ] **Step 2: RED**, **Step 3: implement**, **Step 4: GREEN** (`test_relational_pipeline.py`, new file, `test_fanout_three_tables.py` unchanged and green), **Step 5: commit** — `feat(pipeline): conditional candidates joined on the shared columns, side-input pools next to a driving edge (ADR 0037)`.

---

### Task 6: Launcher — modes, overlap, nullability, the flag, the milestones

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py`
- Test: `packages/sdfb-tests/tests/unit/cli/test_fk_edge_caps_wiring.py` (extend) and `packages/sdfb-tests/tests/unit/cli/test_run_pipeline_fanout.py` (or the file holding `resolve_fanout` tests — `grep -rl resolve_fanout packages/sdfb-tests`)

**Interfaces:**
- Consumes: Task 1 roles/overlap/rest/choice; Task 5 `FkEdgeSpec` fields.
- Produces: CLI `--fk_candidate_cap` (int, default 64, in `launch_config`).
- Produces: `in_set_parent_edges(registry, landing_table, *, in_set_names, key_sample_caps, edge_roles=None, keys_per_batch=100, table_schema=None, candidate_cap=64)`: mode map `{"driving": "fanout", "implied": "implied", "independent": "side_input", "conditional": "conditional"}` (external stays out — not in-set); `overlap=registry.edge_overlap(...)`; `nullable = all(col.mode == "NULLABLE" for col in rest cols of table_schema) and rest non-empty`; `candidate_cap`; `keys_per_batch = min(keys_per_batch, max(1, 100_000 // (candidate_cap * n_conditional)))` when `n_conditional > 0`.
- Produces: `conditional_plan_entries(registry, landing_table, roles, table_schema) -> list[dict]` (`{"id","cols","nullable"}` per conditional edge) merged into the fanout payload as `payload["conditional"]` in `_resolve_table_fanout` (the payload from `fanout_payload` is a dict; add the key after).
- Produces: in `_load_reference_and_preflight`, after `_resolve_table_fanout`: `if registry.driving_choice(table) == "first_declared": log_milestone("fk_driving_edge_defaulted", level=WARNING, table=, edge=, hint="mark drives: true to choose")`; for every `external` edge whose cols intersect the driving cols: `log_milestone("fk_edge_overlap_external", level=WARNING, table=, edge=, overlap=)` once per table.
- Produces: `fk_edge_role` milestone gains `overlap=` for conditional edges (preflight logs it from `edge_roles`; pass `registry.edge_overlap` results via a new `edge_overlaps: Mapping[FkEdge, tuple] | None = None` kwarg on `preflight` — Task 7 owns the `preflight` signature; here only compute and pass).

- [ ] **Step 1: Failing tests** — star model: `in_set_parent_edges` yields modes `fanout` + `side_input`; diamond: `fanout` + `conditional` with `overlap=("T",)`, `nullable` from a schema with `R` NULLABLE vs REQUIRED; `keys_per_batch` bound `100_000 // (64*1) = 1562` caps a 5000 request; `launch_config` shows `fk_candidate_cap`; `fk_driving_edge_defaulted` logged for the star, not for a single-edge child (caplog at WARNING on `sdfb.milestone`).
- [ ] **Step 2: RED**, **Step 3: implement**, **Step 4: GREEN** (all `tests/unit/cli`), **Step 5: commit** — `feat(launcher): edge modes for independent/conditional edges, --fk_candidate_cap, defaulted-driving and external-overlap milestones (ADR 0037)`.

---

### Task 7: Preflight P4 — independent and conditional members inside the PK

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/preflight.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (`resolve_fanout` passes `known=` to `pk_cell_columns`; `_load_reference_and_preflight` passes the new kwargs)
- Test: `packages/sdfb-tests/tests/unit/cli/test_preflight_fanout.py`

**Interfaces:**
- Produces: `pk_cell_columns(effective_pk, driving_cols, profiles, known: tuple[str, ...] = ())` — `known` columns (independent edge cols, conditional rest cols) are removed from `rest` before the cell/exact computation.
- Produces: `preflight(..., fk_member_caps: Mapping[tuple[str, ...], int] | None = None, conditional_rest: Mapping[str, tuple[str, ...]] | None = None, candidate_cap: int = 64, edge_overlaps: Mapping[FkEdge, tuple[str, ...]] | None = None)`; `PreflightResult.fk_key_sample_caps` is populated for independent edges whose cols ⊆ PK: `fk_key_sample_cap(derived_rows or num_rows, other=n_cells × Π other caps)` bounded by `fk_parent_rows[ref]` when known.
- Produces: `_check_driven_pk(table_schema, effective_pk, fanout, profiles, *, independent_caps, conditional_rest, candidate_cap)`: `capacity = n_cells_or_1 × Π independent caps (edges in PK) × Π candidate_cap (conditional edges whose rest ⊆ PK)`; the 1:1 branch (Task 8573665) becomes `capacity == 1`; stop when `max_k > capacity`, message naming the limiting factor (`--fk_candidate_cap` for a conditional one, "a parent that lands more keys" for an independent one).
- `fk_edge_role` logs `overlap=` when `edge_overlaps` has the edge.

- [ ] **Step 1: Failing tests** — fact PK `[A_ID, B_ID, SEQ]` with `SEQ` INT64 (inexact → passes, caps computed for `B_ID`'s edge); fact PK `[A_ID, B_ID]`, `max_k=5`, `B_ID` pool cap 3 → stop naming the edge; diamond bottom PK `[T, L, R]`, `max_k=100`, `candidate_cap=64` → stop naming `--fk_candidate_cap`; same with `max_k=50` → passes; `pk_cell_columns(("T","L","R","C"), ("T","L"), profiles, known=("R",)) == (("C",), True)` when `C` is categorical.
- [ ] **Step 2: RED**, **Step 3: implement**, **Step 4: GREEN** (`tests/unit/cli`), **Step 5: commit** — `feat(preflight): P4 counts independent pool caps and the conditional candidate cap as per-key PK factors (ADR 0037)`.

---

### Task 8: Worker plan milestones

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/generation_plan.py` (and wherever `fanout_bound` is logged — `grep -rn fanout_bound packages/sdfb-core/src`)
- Test: the existing generation-plan test file (`grep -rl relational_fk_edge packages/sdfb-tests`)

- [ ] **Step 1: Failing tests** — `relational_fk_edge` shows `mode=conditional overlap=T` and `mode=side_input` for the new modes; `fanout_bound` shows `conditional=1 candidate_cap=64` when the plan carries a conditional edge (`candidate_cap` comes from `ctx.fanout.get("candidate_cap")`, written by Task 6 next to `"conditional"`; absent → omitted).
- [ ] **Step 2: RED**, **Step 3: implement**, **Step 4: GREEN**, **Step 5: commit** — `feat(observability): worker milestones name conditional and side-input edge modes (ADR 0037)`.

---

### Task 9: DirectRunner acceptance — one test per shape

**Files:**
- Create: `packages/sdfb-tests/tests/unit/test_fanout_shapes.py` (template: `packages/sdfb-tests/tests/unit/test_fanout_three_tables.py` — same `_schema`, `_read`, `cfg` helpers, `WriteToJsonLines`, `FakeModelClient`)

**Interfaces:** consumes Tasks 2–6 (`TableSpec`, `FkEdgeSpec(mode=…, overlap=…, candidate_cap=…, nullable=…)`, `PipelineConfig.fanout` payload with `"conditional"`).

- [ ] **Step 1: Write the five tests (they are RED until Tasks 2–6 land; write them first, run them last)**:
  - `test_star_fact_two_dimensions`: `dim_a` (root, 40 rows), `dim_b` (root, 30 rows), `fact` driven by `dim_a` (`fanout` histogram `{1: 5, 3: 5}`), `B_ID` edge `side_input` → every `fact.A_ID` ∈ landed `dim_a`, every `fact.B_ID` ∈ landed `dim_b`, PK `(A_ID, B_ID, SEQ)` unique, `len(fact)` within `[len(dim_a)*1, len(dim_a)*3]`.
  - `test_diamond_two_branches_rejoin`: `top`, `left` (driven by `top`), `right` (driven by `top`), `bottom` driven by `left` with `(T,R)` conditional (`overlap=("T",)`) → every `(T,L)` in `left`, every `(T,R)` in `right`, PK unique, no `fk.unmatched` in the DLQ file.
  - `test_existence_filter_drops_unmatched_keys`: `P`, `Q` roots, `CHILD` driven by `P` with `(K)->Q` conditional, rest empty, non-nullable; `Q` holds half of `P`'s keys → every landed `CHILD.K` ∈ `Q`; DLQ has exactly `len(P) - |P∩Q|` envelopes with `rule_id == "fk.unmatched"` and `raw_request["n"] >= 1`.
  - `test_nullable_branch_lands_null`: diamond with `R` NULLABLE and `right` covering half the `T`s → rows for uncovered `T` have `R is None`, no DLQ.
  - `test_graph_six_tables`: root → mid → leaf (tree) + `dim` root + `fact` (driven by `mid`, `dim` independent) + `twin` (1:1 of `leaf`, PK == driving edge) → zero orphans on every edge, PK unique everywhere.
- [ ] **Step 2: Run** — `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/test_fanout_shapes.py -q` — GREEN.
- [ ] **Step 3: Commit** — `test(fanout): DirectRunner acceptance for star, diamond, existence filter, nullable branch and a six-table graph (ADR 0037)`.

---

### Task 10: ADR 0037, figure, operator docs, prompts, sample model

**Files:**
- Create: `docs/adr/0037-multi-parent-children.md` (Context: the three 2026-09-11 stops + the sweep; Decision: the five roles, rule 4, the conditional join, ruling B, `--fk_candidate_cap`; Alternatives: side-input pool for diamonds (overwrites the shared column), row-level join after generation (shuffles rows, not keys), refusing multi-parent children (what ADR 0036 did); Consequences; Acceptance = Task 9's tests). Register it in `docs/adr/README.md`.
- Create: `scripts/doc/make_multi_parent_figures.py` (CONCEPT block; seeded Zipf fan-out per shared value vs candidate counts; three panels for caps 16 / 64 / 256 showing the share of shared values that wrap; palette validated per the `dataviz` skill; writes `docs/designs/assets/multi-parent-candidate-cap.png`) and run it.
- Modify: `docs/adr/0036-parent-driven-fanout-generation.md` — a **Rev 3 (2026-09-11)** paragraph after Rev 2: D1's mutual exclusion and D4's "neither driving nor implied" stop are superseded by ADR 0037.
- Modify: `config/relationships/README.md` — the roles table (five roles, one line each), the driving rule (four steps + `drives: true` override), ruling B, `--fk_candidate_cap`; a star and a diamond worked example.
- Create: `config/relationships/example_star_diamond.yaml` (aliases only; a `dim_a`/`dim_b`/`fact` star and a `top`/`left`/`right`/`bottom` diamond; skipped on directory scans like every `example_*.yaml`).
- Modify: `docs/RUN_PLAYBOOK.md` — §7 rows for `fk_driving_edge_defaulted`, `fk_edge_overlap_external`, `fk_edge_role overlap=`, `relational_fk_edge mode=conditional`, `fanout_bound conditional=`, counters, `fk.unmatched`; §9b: the flag and "which parent drives".
- Modify: `.github/prompts/visual_fk_pk_ddl_contract_guide.prompt.md` — Step 2 resolves roles through `registry.edge_roles` / `driving_choice` / `edge_overlap` (never by hand) and the card legend lists the five roles; the diagram legend names `independent` and `conditional on (…)`.
- Modify: `.github/prompts/e2e_fk_pk_validator.prompt.md` — Step 1 JSON gains `"overlap"` per edge and `"driving_choice"` per table; Step 3 parses `fk.unmatched`; Step 5 says a conditional edge's orphan query is the same whole-tuple query and NULL tuples are the ruling-B rows; Step 6 maps `fk.unmatched` (expected when the source lacks a branch) vs `fk.orphan` (never expected).
- Modify: `.github/prompts/end_to_end_validation_report_generation.prompt.md` — wherever `fk.orphan` is listed as a DLQ rule, add `fk.unmatched` with one line of meaning.

- [ ] **Step 1: Write everything above**; run the figure script; `ruff check .`.
- [ ] **Step 2: Commit** — `docs(adr): ADR 0037 multi-parent children — roles, driving rule, conditional join, candidate cap; playbook, README, prompts, sample model`.

---

### Task 11: Final verification and push

- [ ] `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` — all green; `ruff check .`; `mypy packages/sdfb-core/src` — 0 errors.
- [ ] `uv run --no-sync python3 scripts/relationships/card.py --relationships-uri config/relationships/example_star_diamond.yaml --all` renders both roles.
- [ ] `git push origin ws12-fanout-generation`; update PR #18's description with one paragraph on ADR 0037.

## Self-review

- Spec coverage: §2 roles → T1; §3 rule → T1/T6 (milestone); §4 conditional path → T2 (draw), T3 (engine), T4 (NULL policy + DLQ), T5 (join/Top-M/payload), T6 (nullable/overlap/flag); §5 star → T5/T6; §6 P4 → T7; §7 scale → T5 (Top-M, Distinct skip) + T6 (`keys_per_batch` bound); §8 observability → T6/T8; §9 external overlap → T6; §10 acceptance → T9 + T1; §11 figure → T10.
- Type consistency: `edge_id = ",".join(cols)` everywhere (T2 `ConditionalEdge.id`, T4 request key, T5 `FkEdgeSpec.edge_id`, T6 plan entries); role strings and mode strings fixed in Global Constraints.
