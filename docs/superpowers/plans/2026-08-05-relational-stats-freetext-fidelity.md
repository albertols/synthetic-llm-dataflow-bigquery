# Relational contract + source stats + free-text fidelity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved 2026-08-05 spec: empty/NULL parity and shape-mix fidelity for free-text generation, a shape-preserving expander that breaks the pool-size diversity ceiling, per-column `llm_prompt_constraint`, a `RelationalContract` parsed from BQ description JSON with driver preflight, FK pools for parent-first multi-table generation, and a persisted `source_table_stats` artifact.

**Architecture:** All parsing/profiling/stats are pure `sdfb-core` (laptop-testable, no Beam); driver-side wiring (preflight, FK pools, stats persistence) lives in `sdfb-beam` CLI/io; every engine-visible change lands in BOTH `b1_rag` and `b2_library` or in the shared modules (`engines/text_shapes.py`, `engines/generation_plan.py`). No DAG-shape change, no new GroupByKey, no new LLM calls on any path.

**Tech Stack:** Python 3.11, pydantic, numpy, pytest (laptop suite: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q`), ruff.

## Global Constraints

- Both-engines rule: any `ColumnProfile`/sampling change lands in `b1_rag` AND `b2_library` (or in a shared module) in the same task.
- `sdfb-core` never imports Beam/GCP; `sdfb-beam` may import `sdfb-core`, never the reverse.
- vLLM prefix-cache contract (ADR 0018): pool prompts stay byte-identical across attempts; per-column *constant* additions only.
- No `STREAMING_INSERTS`; BQ writes are load jobs (pools-store `write_rows` pattern).
- Baseline before any commit: 748 tests green, ruff clean; suite must stay green after every task.
- Branch: `ws8-fidelity-relational` off `ws6-pipeline-shape` (no worktree: the laptop `.venv` cannot be rebuilt behind the corp index; `--no-sync` reuses the root venv).
- Contract JSON marker key: `"sdfb"`; column constraint key: `"llm_prompt_constraint"` (spec defaults, approved).

---

## Phase 1 — contracts + stats core (pure sdfb-core)

### Task 1: Embedded-JSON extractor for descriptions

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/contracts/description_json.py`
- Test: `packages/sdfb-tests/tests/unit/contracts/test_description_json.py`

**Interfaces:**
- Produces: `extract_embedded_json(text: str, marker_key: str) -> dict | None` and `DescriptionJsonError(ValueError)`. Returns the first balanced `{...}` object in `text` that parses as JSON **and** contains `marker_key`. Returns `None` when no candidate contains the marker. Raises `DescriptionJsonError` when a brace-balanced candidate *contains the marker substring* but fails to parse (a half-written contract must be loud).

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from sdfb_core.contracts.description_json import DescriptionJsonError, extract_embedded_json

def test_prose_only_returns_none():
    assert extract_embedded_json("Landing table for FX ops.", "sdfb") is None

def test_pure_json_object():
    assert extract_embedded_json('{"sdfb": 1, "pk": ["A"]}', "sdfb") == {"sdfb": 1, "pk": ["A"]}

def test_json_embedded_in_prose():
    text = 'FX ops table. {"sdfb": 1, "pk": ["A", "B"]} Owned by team X.'
    assert extract_embedded_json(text, "sdfb") == {"sdfb": 1, "pk": ["A", "B"]}

def test_nested_braces_stay_balanced():
    text = 'x {"sdfb": 1, "fk": [{"cols": ["A"], "ref": "d.t", "ref_cols": ["A"]}]} y'
    assert extract_embedded_json(text, "sdfb")["fk"][0]["cols"] == ["A"]

def test_object_without_marker_is_ignored():
    assert extract_embedded_json('{"note": "not ours"}', "sdfb") is None

def test_malformed_with_marker_raises():
    with pytest.raises(DescriptionJsonError):
        extract_embedded_json('{"sdfb": 1, "pk": [BROKEN}', "sdfb")

def test_empty_and_none_safe():
    assert extract_embedded_json("", "sdfb") is None
```

- [ ] **Step 2: Run to verify failure** — `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/contracts/test_description_json.py -q` → import error.

- [ ] **Step 3: Implement**

```python
"""Tolerant extraction of a JSON contract embedded in a BQ description.

Descriptions are shared, human-edited real estate: prose before/after the
object is expected. The scanner walks brace-balanced candidates (string- and
escape-aware) and returns the first that parses AND carries the marker key.
A candidate that merely *mentions* the marker but does not parse raises —
a half-written contract silently dropped is worse than a loud stop.
"""
from __future__ import annotations
import json

class DescriptionJsonError(ValueError):
    """A description contains a marked-but-unparseable JSON object."""

def _candidates(text: str):
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0: yield text[start : i + 1]

def extract_embedded_json(text: str, marker_key: str) -> dict | None:
    for cand in _candidates(text or ""):
        if f'"{marker_key}"' not in cand:
            continue
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError as exc:
            raise DescriptionJsonError(
                f"description contains a '{marker_key}'-marked JSON object "
                f"that does not parse: {exc}"
            ) from exc
        if isinstance(obj, dict) and marker_key in obj:
            return obj
    return None

__all__ = ["DescriptionJsonError", "extract_embedded_json"]
```

- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** — `feat(contracts): embedded-JSON extractor for BQ descriptions`

### Task 2: `RelationalContract` + `llm_prompt_constraint` models

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/contracts/relational.py`
- Test: `packages/sdfb-tests/tests/unit/contracts/test_relational_contract.py`

**Interfaces:**
- Consumes: `extract_embedded_json`, `DescriptionJsonError` (Task 1).
- Produces:
  - `class ForeignKey(BaseModel)`: `cols: tuple[str, ...]`, `ref: str`, `ref_cols: tuple[str, ...]` — validator: both tuples non-empty and same length; `ref` contains at least one dot.
  - `class RelationalContract(BaseModel)`: `sdfb: int`, `pk: tuple[str, ...] = ()`, `fk: tuple[ForeignKey, ...] = ()`, `identity: tuple[str, ...] = ()`.
  - `parse_relational_contract(description: str) -> RelationalContract | None` — None when no marked object; raises `DescriptionJsonError` on marked-but-invalid (both unparseable JSON and schema-invalid contract).
  - `parse_llm_prompt_constraint(description: str) -> str` — `""` when absent; single-line-normalized, capped at `_MAX_CONSTRAINT_CHARS = 500`.

- [ ] **Step 1: Failing tests**

```python
import pytest
from sdfb_core.contracts.description_json import DescriptionJsonError
from sdfb_core.contracts.relational import (
    RelationalContract, parse_llm_prompt_constraint, parse_relational_contract,
)

def test_absent_contract_is_none():
    assert parse_relational_contract("just prose") is None

def test_full_contract_parses():
    c = parse_relational_contract(
        'Ops table {"sdfb": 1, "pk": ["A", "B"], '
        '"fk": [{"cols": ["A"], "ref": "ds.parent", "ref_cols": ["A"]}], '
        '"identity": ["C"]}'
    )
    assert c.pk == ("A", "B")
    assert c.fk[0].ref == "ds.parent"
    assert c.identity == ("C",)

def test_marked_but_invalid_schema_raises():
    with pytest.raises(DescriptionJsonError):
        parse_relational_contract('{"sdfb": 1, "fk": [{"cols": [], "ref": "x.y", "ref_cols": []}]}')

def test_fk_arity_mismatch_raises():
    with pytest.raises(DescriptionJsonError):
        parse_relational_contract('{"sdfb": 1, "fk": [{"cols": ["A"], "ref": "d.t", "ref_cols": ["A", "B"]}]}')

def test_constraint_absent_is_empty():
    assert parse_llm_prompt_constraint("plain column description") == ""

def test_constraint_parsed_normalized_capped():
    desc = 'Doc. {"llm_prompt_constraint": "ISO-4217 currency\\ncodes only"}'
    assert parse_llm_prompt_constraint(desc) == "ISO-4217 currency codes only"
    long = '{"llm_prompt_constraint": "' + "x" * 900 + '"}'
    assert len(parse_llm_prompt_constraint(long)) == 500
```

- [ ] **Step 2: Run → fail (module missing).**
- [ ] **Step 3: Implement** — pydantic models as in Interfaces; `parse_relational_contract` wraps `extract_embedded_json(description, "sdfb")`, converts `pydantic.ValidationError` into `DescriptionJsonError` (same loud-failure contract); `parse_llm_prompt_constraint` uses `extract_embedded_json(description, "llm_prompt_constraint")`, then `" ".join(value.split())[:500]`; non-str values → `""`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(contracts): RelationalContract + llm_prompt_constraint from description JSON`

### Task 3: `build_shape_mix` + `mutate_digit_runs` in shared text_shapes

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/text_shapes.py`
- Test: `packages/sdfb-tests/tests/unit/engines/test_shape_mix.py`

**Interfaces:**
- Consumes: existing `RelaxedShapes` type, `_CHAR_CLASSES`, `sample_relaxed_identifier`, `relaxed_shapes_pattern` (all already in `text_shapes.py`).
- Produces:
  - `build_shape_mix(values: Iterable[str], top_k: int = 8) -> RelaxedShapes | None` — like `build_relaxed_shapes` but bucketed by **exact shape mask** (digit→`9`, upper→`A`, lower→`a`, space→literal, everything else literal), not by length; keeps the `top_k` heaviest masks; single-value buckets are KEPT (weight 1) because a mask, unlike an all-literal length bucket, still generalizes. Spaces are allowed (this feeds free-text columns; the prose guard stays only in `build_relaxed_shapes`). Template entries: literal char if the position is constant across the bucket, else the narrowest `_CHAR_CLASSES` class, else the observed charset.
  - `shape_mix_is_identifier_like(shapes: RelaxedShapes) -> bool` — True when NO template position can emit a space AND ≥ half the positions (mass-weighted) are class positions (digits/letters), i.e. the column is code-like, safe for unbounded expansion.
  - `mutate_digit_runs(value: str, pick: Callable[[int], int]) -> str` — replaces every maximal run of ≥2 digits with fresh digits of the same length (first digit of a run keeps its zero/nonzero-ness so `0001…` prefixes survive); non-digits untouched.

- [ ] **Step 1: Failing tests**

```python
from sdfb_core.engines.text_shapes import (
    build_shape_mix, mutate_digit_runs, sample_relaxed_identifier,
    shape_mix_is_identifier_like,
)
import random

def test_shape_mix_groups_by_mask_and_weights():
    vals = ["AB12", "CD34", "EF56", "12-99", "34-56"]
    shapes = build_shape_mix(vals)
    weights = sorted(w for w, _ in shapes)
    assert weights == [2, 3]  # 3x 'AA99', 2x '99-99'

def test_shape_mix_keeps_singletons():
    shapes = build_shape_mix(["AB12", "9-9x"])
    assert len(shapes) == 2

def test_shape_mix_samples_in_observed_shapes():
    vals = ["U825577", "U824712", "PVAE064", "PIDC703"]
    shapes = build_shape_mix(vals)
    rng = random.Random(7)
    for _ in range(50):
        v = sample_relaxed_identifier(shapes, rng.randrange)
        assert len(v) == 7

def test_identifier_like_detection():
    assert shape_mix_is_identifier_like(build_shape_mix(["AB1234", "CD5678"]))
    assert not shape_mix_is_identifier_like(build_shape_mix(["SEG.DE CAMBIO 12", "ABONO CANON A 34"]))

def test_mutate_digit_runs_preserves_shape_and_prefix_zero():
    rng = random.Random(3)
    v = mutate_digit_runs("TRF.EX-095019853 A 17", rng.randrange)
    assert len(v) == len("TRF.EX-095019853 A 17")
    assert v.startswith("TRF.EX-0")  # leading zero of the run preserved
    assert v != "TRF.EX-095019853 A 17"
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** in `text_shapes.py` (append; export in `__all__`). Mask function local `_mask(v)`: digit→"9", upper→"A", lower→"a", else literal. Group values by mask; take `top_k` heaviest groups; per group build the per-position template exactly like `build_relaxed_shapes`'s inner loop (literal / class / observed-set fallback); weight = group size. `mutate_digit_runs`: regex `re.compile(r"\d{2,}")`, replacement keeps `run[0]` if `run[0] == "0"` else draws first digit from `1-9`, rest from `0-9`.
- [ ] **Step 4: Run → PASS. Also run the full text_shapes test file.**
- [ ] **Step 5: Commit** — `feat(shapes): exact shape-mix builder + digit-run mutator (shared)`

### Task 4: `source_table_stats` pure profiler

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/stats/__init__.py`, `packages/sdfb-core/src/sdfb_core/stats/source_stats.py`
- Test: `packages/sdfb-tests/tests/unit/stats/test_source_stats.py`

**Interfaces:**
- Consumes: `TableSchema`/`FieldSchema` (`sdfb_core.contracts.schema`), `build_shape_mix` (Task 3), `RelationalContract` (Task 2).
- Produces: `profile_source_table(table_schema, reference_rows: list[dict], contract: RelationalContract | None = None, generation_plan: dict[str, str] | None = None) -> dict[str, dict]` — per-column stats dict with keys exactly: `type, in_source_schema, sample_rows, null_fraction, empty_fraction, zero_fraction, distinct, distinct_ratio, is_constant, is_pk, is_fk, identity_col, min, max, len_p05, len_p50, len_p95, mean_len, shape_mix, temporal_day_granularity, generation_plan`. `shape_mix` is `[[mask, round(mass, 4)], …]` top-8 by mass (mask = the `9/A/a` string). `stats_rows(table_fqn, reference_digest, run_id, stats) -> list[dict]` flattens to BQ rows (one per column, stats JSON-encoded in a `stats` STRING field + the headline numerics as real columns: `null_fraction, empty_fraction, distinct, distinct_ratio, is_pk, is_fk, generation_plan`).

- [ ] **Step 1: Failing tests** (build a 6-column fixture: INT64 constant-zero, STRING 90%-empty freetext, STRING categorical, DATE, STRING pk, FLOAT with min/max; assert `empty_fraction == 0.9`, `zero_fraction == 1.0` on the INT64, `is_pk` honored from a contract, `shape_mix` non-empty on the freetext column, `len(stats_rows(...)) == 6`).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Pure stdlib + Task 3 import. Numeric min/max only for numeric BQ types; percentiles by sorted index (`lengths[int(p * (len-1))]`); `temporal_day_granularity`: all parsed datetimes have zero time-of-day; `generation_plan` label passed in (engines own routing — stats do not re-derive it; `None` → `""`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `feat(stats): source_table_stats profiler (pure core)`

---

## Phase 2 — empty parity + shape fidelity + expander (both engines)

### Task 5: `empty_fraction` in both profilers; empties leave the pools

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/profile.py` (ColumnProfile `:88-129`, `_profile_one` `:148`, `_profile_string` `:350`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/fidelity.py` (ColumnProfile `:112-153`, `profile_column`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_profile_empty_parity.py`, `packages/sdfb-tests/tests/unit/engines/test_b2_empty_parity.py`

**Interfaces:**
- Produces (both ColumnProfile dataclasses): `empty_fraction: float = 0.0` (fraction of ALL sampled rows whose value is a string that is empty after `.strip()`), `shape_mix: RelaxedShapes | None = None` (set for FREE_TEXT-routed string columns from the non-empty distinct values, via Task 3).
- Behavioral change (both `_profile_string` / string branch of `profile_column`): empty-after-strip strings are **excluded** from `distinct` / `text_examples` / `text_pool` / `observed_values` and from classification counts, and counted into `empty_fraction` instead. A column that is `""`-only routes CONSTANT with `constant_value=""`.

- [ ] **Step 1: Failing tests** — b1: profile a column of 90×`""` + 10 distinct long strings; assert `empty_fraction == 0.9`, `kind is FREE_TEXT` (10 distinct > threshold path unchanged for the non-empty part — use 60 distinct values to clear `_FREE_TEXT_MAX_CATEGORIES=50`), `"" not in prof.text_examples`, `prof.shape_mix is not None`. b2 mirror on `profile_column`. Also: all-empty column → CONSTANT `""`; empties counted in `empty_fraction` not `null_fraction`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** In `_profile_one`: compute `empties = [v for v in non_null if isinstance(v, str) and not v.strip()]`, `empty_fraction = len(empties)/n`, pass `non_empty = [v for v in non_null if not (isinstance(v, str) and not v.strip())]` down the string path; thread `empty_fraction=` into every `ColumnProfile(...)` construction in the string path (temporal/identifier/freetext/categorical returns). Same structure in b2 `profile_column`. Set `shape_mix=build_shape_mix(distinct)` on the two FREE_TEXT non-identifier returns.
- [ ] **Step 4: Full suite → green (existing profile tests must not regress; empties previously counted as categories may shift a fixture — fix fixtures only if their intent was not empty-handling).**
- [ ] **Step 5: Commit** — `feat(profiles): empty_fraction + shape_mix on both engines' ColumnProfile`

### Task 6: emit empties + shape-mix draws + expander in B.1 sampling

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_sample_free_text` `:400-437`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (GenerationContext: add `freetext_expansion: str = "identifiers"`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_freetext_sampling_fidelity.py`

**Interfaces:**
- Consumes: `prof.empty_fraction`, `prof.shape_mix` (Task 5), `shape_mix_is_identifier_like`, `sample_relaxed_identifier`, `mutate_digit_runs` (Task 3).
- Produces: sampling semantics, one draw per row: `r = rng.random()`; `r < null_frac` → `None`; `r < null_frac + empty_frac` → `""`; else a value. Value route: identifier_shape → unchanged; else if `freetext_expansion != "off"` and `shape_mix` present and (`identifiers` mode → `shape_mix_is_identifier_like` true; `all` mode → always): draw via `sample_relaxed_identifier(prof.shape_mix, rng.randrange)`, retry up to 3× while the draw is in `observed` (novelty guard, `observed = set(prof.observed_values)` hoisted per column), else pool draw + (in `all` mode) `mutate_digit_runs`; else pool draw unchanged.

- [ ] **Step 1: Failing tests** — with a stub profile (`empty_fraction=0.5, null_fraction=0.2, nullable=True`, pool of 3): over n=4000 draws, `None` fraction ≈ 0.2 ± 0.03, `""` fraction ≈ 0.5 ± 0.03. With `shape_mix` from 4 `U999999`-style values and `freetext_expansion="identifiers"`: distinct count over 4000 draws > 500 (ceiling broken) and every value matches `^[A-Z]\d{6}$`; with `"off"`: distinct ≤ pool size. Novelty: no drawn value ∈ observed set (for the identifier route).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** exactly per Interfaces (keep the dedicated freetext RNG; identifier_shape branch gains the empty mask too).
- [ ] **Step 4: Run → PASS; full suite green.**
- [ ] **Step 5: Commit** — `feat(b1): empty parity + shape-mix expansion in free-text sampling`

### Task 7: same semantics in B.2

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py` (`FreeTextHook.sample` `:194-245`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_freetext_fidelity.py`

**Interfaces:**
- Consumes: same shared helpers; `cfg.engine_specific.get("freetext_expansion", "identifiers")` (B.2 reads GenerationConfig, not ctx).
- Produces: numpy-mask equivalent of Task 6: `u = rng.random(n)`; `None` where `u < null_frac`; `""` where `null_frac <= u < null_frac + empty_frac`; remaining rows drawn via the same route logic (expander loop in plain Python over the remaining indices — pool sizes are small; vectorizing the blend stays as-is).

- [ ] **Steps 1-5:** mirror Task 6's tests against `FreeTextHook.sample` (numpy RNG: `np.random.default_rng(7)`); implement; suite green; commit — `feat(b2): empty parity + shape-mix expansion in FreeTextHook`.

### Task 8: pattern-guidance union from shape_mix + CLI flag plumbing

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (pattern seam `:728-748` — prefer `shape_mix` over `build_relaxed_shapes` when present)
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (PipelineConfig: `freetext_expansion: str = "identifiers"`; thread into GenerationContext where `pool_pattern_guidance` already flows)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (add `--freetext_expansion`, choices `off|identifiers|all`, default `identifiers`; pass through to PipelineConfig and, for B.2, into `engine_specific`)
- Test: extend `packages/sdfb-tests/tests/unit/pools/test_pool_branch.py`-adjacent DAG test asserting config threading, plus a unit test that `relaxed_shapes_pattern(build_shape_mix(...))` accepts observed values and rejects an out-of-charset value.

- [ ] **Steps:** failing test on config threading (build_pipeline with `freetext_expansion="off"` → engines see it) → implement → suite green → commit — `feat(dag): --freetext_expansion flag; pattern guidance prefers shape_mix`.

### Task 9: widen `generation_plan` to per-column detail

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/generation_plan.py` (`build_plan` `:40-59`)
- Modify: both emitters (`b1_rag/engine.py:269-297`, `b2_library/engine.py:113-129`) — emit `plan` (labels, unchanged key) plus new `columns_detail` JSON.
- Test: update `packages/sdfb-tests/tests/unit/rag/test_generation_plan.py` and `.../engines/test_b2_generation_plan.py`.

**Interfaces:**
- Produces: `build_plan(profiles)` unchanged; NEW `build_plan_detail(profiles) -> dict[str, dict]`: per column `{"kind": label, "null_fraction": round(f,4), "empty_fraction": round(f,4), "shapes": len(prof.shape_mix or ()), "constraint": bool(getattr(prof, "llm_prompt_constraint", ""))}`. Emitters log it as `columns_detail=json.dumps(...)` beside the existing fields.

- [ ] **Steps:** failing tests → implement → green → commit — `feat(observability): per-column detail in generation_plan`.

---

## Phase 3 — llm_prompt_constraint + thresholds catalog

### Task 10: constraint flows profile → prompt (both engines) behind `--prompt_constraints`

**Files:**
- Modify: both ColumnProfiles (+ `llm_prompt_constraint: str = ""`), both profilers to fill it via `parse_llm_prompt_constraint(col.description)` (b1 `_profile_string`; b2 string branch — both already receive the `FieldSchema`).
- Modify: `b1_rag/engine.py` `_build_pool_prompt(column, per_call, seed_examples, constraint: str = "")` (`:992-1003`) — when `constraint`: append `f" Column constraint: {constraint}."` before the JSON-return sentence; both call sites (`_infer_free_text_pool`, `_rotating_prompt` `:687-709`) pass `prof.llm_prompt_constraint if ctx.prompt_constraints else ""`.
- Modify: `b2_library/freetext.py` `_generate_pool` (`:296-308`) — same sentence, gated on `cfg.engine_specific.get("prompt_constraints", True)`.
- Modify: `engines/base.py` GenerationContext `prompt_constraints: bool = True`; `pipeline.py` PipelineConfig + threading; `run_pipeline.py` `--prompt_constraints` `on|off` default `on`.
- Test: `packages/sdfb-tests/tests/unit/rag/test_prompt_constraint.py` (+ b2 twin).

- [ ] **Step 1: Failing tests** — profile a column whose `FieldSchema.description` carries `{"llm_prompt_constraint": "uppercase SWIFT-style refs"}` → `prof.llm_prompt_constraint == "uppercase SWIFT-style refs"`; `_build_pool_prompt("C", 32, ["x"], constraint="abc")` contains `"Column constraint: abc."` and without it is byte-identical to today's string (regression-pin the current text); prompt for two different attempts with a constraint stays byte-identical (prefix-cache pin).
- [ ] **Steps 2-5:** implement → green → commit — `feat(freetext): per-column llm_prompt_constraint from DDL descriptions`.

### Task 11: thresholds catalog rules (post-run scope) + probe wiring

**Files:**
- Modify: `config/thresholds.yml` — three new rules with `scope: post_run`: `freetext.empty_parity` (MAJOR, `max_abs_delta: 0.10`), `freetext.distinct_floor` (MAJOR, `min_ratio_of_source: 0.5`, applies where source `distinct_ratio > 0.5`), `freetext.copy_fraction` (BLOCKER, `max: 0.0`, applies where source `distinct > 100`).
- Modify: `scripts/e2e/e2e_gcp_probe.py` — evaluate the three rules from metrics it already computes (`null_fraction`/`distinct_ratio`/`copy_ratio_nonsentinel`; add `empty_fraction` to its per-column aggregates) and print a PASS/FAIL line per rule.
- Modify: `sdfb_core/validation/thresholds.py` loader only if the schema requires registering new ids.
- Test: unit test that the loader accepts the new catalog; probe function-level test with canned metrics dicts.

- [ ] **Steps:** failing loader/probe tests → implement → green → commit — `feat(validation): post-run freetext parity/diversity/copy rules`.

---

## Phase 4 — relational wiring, FK pools, stats persistence, orchestrator (sdfb-beam)

### Task 12: extractor + TableSchema carry the contract

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/ddl/extractor.py` — `_get_primary_keys` (`:176-199`): try `parse_relational_contract(description)` FIRST (its `pk`), then table_constraints, then the legacy `PRIMARY KEY:` split. Add `"relational"` key to `_build_table_info` output: the contract's `model_dump()` or `None` (parse errors propagate — extraction is driver-side).
- Modify: `packages/sdfb-core/src/sdfb_core/contracts/schema.py` — `TableSchema` gains `description: str = ""` and `relational: dict | None = None`; the `_ddl.json` loader fills both from `table_info`.
- Test: extend `packages/sdfb-tests/tests/unit/ddl/` extractor tests with a fake `bigquery.Table` whose description embeds a contract; loader test on an updated fixture copy (add `fixtures/ddl/orders_with_contract_ddl.json` — do not mutate the existing fixtures other tests pin).

- [ ] **Steps:** failing tests → implement → green → commit — `feat(ddl): relational contract extracted into _ddl.json + TableSchema`.

### Task 13: driver preflight P1–P5 + contract-derived defaults

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/cli/preflight.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` `main()` — call `preflight(...)` right after `resolve_table_schema`; default `args.pk_cols`/`args.identity_cols` from the contract when the flags are empty; `relational_contract_loaded` / `_absent` / `_overridden` milestones.
- Test: `packages/sdfb-tests/tests/unit/cli/test_preflight.py`

**Interfaces:**
- Produces: `preflight(table_schema, pk_cols: tuple, identity_cols: tuple, fk_parents_resolved: dict[str, bool] | None, reference_rows: list[dict]) -> PreflightResult` where `PreflightResult` = dataclass `(pk_cols, identity_cols, contract, warnings: list[str])`. Raises `SystemExit` with the exact failing check name for P1 (invalid marked contract — surfaced from `DescriptionJsonError`), P2 (unknown columns listed), P3 (unresolved FK parents listed). P5 (PK not unique in sample) is a warning + `preflight_pk_not_unique_in_sample` milestone, never fatal.

- [ ] **Steps:** failing tests per check (P1 invalid JSON → SystemExit message contains column snippet; P2 unknown pk col; P3 unresolved parent; P5 dup PK tuple in sample → warning captured) → implement → green → commit — `feat(cli): relational preflight before graph build`.

### Task 14: FK pools — parent PK values into both engines

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/io/fk_pools.py` — `load_fk_pools(fks: tuple[ForeignKey, ...], landing_dataset: str, limit: int = 100_000) -> dict[str, tuple]`: per FK, driver-side `SELECT DISTINCT ref_cols FROM landing_dataset.ref_table LIMIT limit` (BQ client injected for tests), keyed by the CHILD column name(s); composite FKs load tuples and every child col maps to the aligned position.
- Modify: `engines/base.py` GenerationContext `fk_pools: dict[str, tuple] = field(default_factory=dict)`; `pipeline.py` PipelineConfig `fk_pools` + threading.
- Modify: both engines' sampler construction: a column with `ctx.fk_pools[name]` non-empty routes to the categorical/empirical sampler over exactly those values, uniform weights, regardless of profiled kind (fidelity to the parent beats the child's marginal in v1 — the referential contract is the point).
- Test: `packages/sdfb-tests/tests/unit/engines/test_fk_pools.py` — engine-level: generate 500 rows with `fk_pools={"CUSTOMER_ID": (1, 2, 3)}` → every landed value ∈ {1,2,3}, both engines. io-level: fake BQ client returns rows; composite key alignment asserted.

- [ ] **Steps:** failing tests → implement → green → commit — `feat(engines): FK columns sample from parent PK pools`.

### Task 15: stats persistence + milestones

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/io/stats_store.py` — `BigQuerySourceStatsStore(table)` with `exists(table_fqn, reference_digest) -> bool` and `write_rows(rows) -> None` (blocking load job; `__getstate__` drops the lazy client — copy the pools-store pattern `sdfb_beam/pools/store.py:52-132` verbatim including the 0dfb1a3 pickle fix).
- Create: `config/bq_schema/synthetic_rag/source_table_stats.schema.json` (fields: `table_fqn STRING, reference_digest STRING, run_id STRING, column STRING, generation_plan STRING, null_fraction FLOAT, empty_fraction FLOAT, distinct INT64, distinct_ratio FLOAT, is_pk BOOL, is_fk BOOL, stats STRING, computed_at TIMESTAMP`).
- Modify: `run_pipeline.py` — `--source_stats` `off|sample` default `sample`, `--source_stats_table` default `""`; after `load_reference_rows`: compute `profile_source_table(...)`, log compact `source_table_stats` milestone (top-line per column: kind, null%, empty%, distinct), write JSON next to run artifacts (existing GCS helper in `sdfb_beam/gcs.py`) and, when a table is configured and `not exists(digest)`, `write_rows`. (`exact` tier deferred — noted in ROADMAP, not YAGNI'd into v1.)
- Test: `packages/sdfb-tests/tests/unit/io/test_stats_store.py` with a fake client; CLI-level test that `--source_stats=off` skips computation.

- [ ] **Steps:** failing tests → implement → green → commit — `feat(stats): persist source_table_stats to BQ + GCS, surfaced as milestone`.

### Task 16: table-set orchestrator (parent-first)

**Files:**
- Create: `scripts/run_tableset.py` — input: JSON file `{"tables": ["p.d.child", "p.d.parent", …], "landing_dataset": "p.synthetic_data", …shared run_pipeline args}`. Steps: extract/parse contracts per table (reuse extractor), `topo_sort(tables, fk_edges)` (pure function, raises on cycles), then per table in order: assemble the `run_pipeline.py` argv (child tables get `--fk_parent_landing=<landing_dataset>` so the driver loads FK pools), `--dry-run` prints commands instead of executing (subprocess).
- Test: `packages/sdfb-tests/tests/unit/cli/test_run_tableset.py` — topo order on a 3-table chain + diamond; cycle raises; dry-run argv snapshot contains fk flag for children only.

- [ ] **Steps:** failing tests → implement → green → commit — `feat(scripts): parent-first table-set orchestrator (dry-run capable)`.

---

## Phase 5 — record + docs

### Task 17: ADR + roadmap + spec status

**Files:**
- Create: `docs/adr/0021-relational-contract-in-descriptions.md` (decision: description-JSON contract + parent-first sequential multi-table; consequences: enterprise TF `jsonencode`, 16K cap, CLI-override rule).
- Modify: `docs/ROADMAP.md` M2 — mark "Multi-table mode" as in progress, link ADR 0021 + the spec; note `--source_stats=exact` as deferred.
- Modify: spec status banner → `ACCEPTED (defaults confirmed 2026-08-05)`.

- [ ] **Steps:** write → `uv run --no-sync ruff check .` + full suite one last time → commit — `docs: ADR 0021 + roadmap for relational/stats/fidelity cycle`.

---

## Self-review notes

- Spec coverage: A1/A2→T1,2,12; A3→T13; A4→T14,16; B1/B2→T4,15 (exact tier explicitly deferred, recorded in T17); C1→T5,6,7; C2→T3,6,7,8; C3→T3,6,7 (+B.2 pool size left, recorded); C4→T11; C5→T2,10. Evolution figure: deferred with the exact-tier note (T17 roadmap line).
- Type consistency: `RelaxedShapes` reused for `shape_mix`; `ForeignKey` defined T2, consumed T13/T14/T16; `empty_fraction` name identical across profilers, stats, plan detail, thresholds.
- Placeholders: none — every step carries code or exact field lists.
