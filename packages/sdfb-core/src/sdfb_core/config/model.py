#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""The generator's configuration: one typed, frozen model.

Every setting the generator reads at launch lives here exactly once, with its
type and its default. Flags keep their CLI names inside their section; the
other fields are the constants the GUI already shows as knobs. The prose that
explains each key lives in `catalogue.yaml`, matched 1:1 by a test (ADR 0043).

Flag fields mirror argparse exactly — same type, choices and default,
including the string encodings argparse holds (`"true"`, `""`, `"8192"`) —
because the launcher rebuilds today's namespace from this model and nothing
downstream may notice. YAML-native values (`true`, `4096`, `[a, b]`) are
coerced to those encodings on the way in. This module imports nothing from
sdfb_core: everything else may import it.

Design: docs/DESIGN.md §3 Serving; §4 Relational generation; §5 Fidelity;
§8 Configuration
(ADR 0014, 0028, 0030, 0031, 0033, 0037, 0038, 0043).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field,
                      model_validator)

CONFIG_VERSION = 1

KIND_FLAG = "flag"
KIND_CONSTANT = "constant"
KIND_IDENTITY = "identity"


def _flag(default: Any, **constraints: Any) -> Any:
  return Field(
      default=default, json_schema_extra={"kind": KIND_FLAG}, **constraints)


def _const(default: Any, **constraints: Any) -> Any:
  return Field(
      default=default, json_schema_extra={"kind": KIND_CONSTANT}, **constraints)


def _identity() -> Any:
  return Field(default=None, json_schema_extra={"kind": KIND_IDENTITY})


def _true_or(false_as: str) -> BeforeValidator:
  """A string-valued CLI bool: YAML true/false become what argparse holds."""

  def coerce(value: Any) -> Any:
    if isinstance(value, bool):
      return "true" if value else false_as
    return value

  return BeforeValidator(coerce)


def _numeric_text(value: Any) -> Any:
  """A string-valued CLI number: YAML ints become digits, null the off-state."""
  if isinstance(value, bool):
    raise ValueError("expected a number or a string, got a boolean")
  if isinstance(value, int):
    return str(value)
  if value is None:
    return ""
  return value


def _csv_text(value: Any) -> Any:
  if isinstance(value, (list, tuple)):
    return ",".join(str(v) for v in value)
  return value


_ROW_DOCS_DEFAULT = 1024


def _row_docs(value: Any) -> Any:
  """`--rag_row_docs_limit`: empty means the default, as on the CLI."""
  if isinstance(value, str) and not value.strip():
    return _ROW_DOCS_DEFAULT
  return value


CliBoolFalse = Annotated[str, _true_or("false")]
CliBoolEmpty = Annotated[str, _true_or("")]
NumericText = Annotated[str, BeforeValidator(_numeric_text)]
CsvText = Annotated[str, BeforeValidator(_csv_text)]
UniquenessMode = Literal["exact", "exact_chained", "streaming"]


class _Section(BaseModel):
  model_config = ConfigDict(frozen=True, extra="forbid")


class IoSettings(_Section):
  """Where the launch reads from and writes to."""
  ddl_uri: str = _flag("")
  reference_table: str | None = _flag(None)
  landing_table: str | None = _flag(None)
  dlq_table: str | None = _flag(None)
  create_if_not_exists: CliBoolFalse = _flag("false")
  validation_runs_table: str = _flag("")
  rag_chunks_table: str = _flag("")
  freetext_pools_table: str = _flag("")
  fk_fanout_stats_table: str = _flag("")
  source_stats_table: str = _flag("")
  source_stats_json: str = _flag("")


class LaunchSettings(_Section):
  """Launch identity: set by flags only, never by a file."""
  run_id: str | None = _identity()


class SamplingSettings(_Section):
  """The reference sample and the source-stats profiler."""
  reference_rows_limit: int = _flag(10_000)
  source_stats: Literal["off", "sample", "exact"] = _flag("sample")
  # Literal source values land in stats ONLY for enum-routed columns (the T4
  # exemplar-leak lesson): above this distinct count, a column gets shapes /
  # entropy / lengths — never values. Mirrors the engines'
  # _FREE_TEXT_MAX_CATEGORIES so "enum-routed" means the same thing everywhere.
  top_values_max_distinct: int = _const(50, ge=1)
  top_values_top_k: int = _const(8, ge=1)
  shape_mix_top_k: int = _const(8, ge=1)
  length_percentiles: tuple[float, float, float] = _const((0.05, 0.50, 0.95))
  # Row-level null co-occurrence: per-row is-null bitstrings, top-K patterns.
  # Skipped above the column cap — the bitstring alphabet grows with width and
  # the pass is O(rows x cols).
  null_pattern_max_cols: int = _const(64, ge=1)
  null_pattern_top_k: int = _const(8, ge=1)

  @model_validator(mode="after")
  def _percentiles_ascending(self) -> SamplingSettings:
    low, mid, high = self.length_percentiles
    if not 0.0 < low < mid < high < 1.0:
      raise ValueError("length_percentiles must be three ascending values "
                       "strictly between 0 and 1")
    return self


class GenerationSettings(_Section):
  """What is generated and how close it stays to the reference."""
  engine: str = _flag("b1_rag")
  num_rows: int | None = _flag(None)
  similarity: float = _flag(0.5)
  seed: NumericText = _flag("")
  batch_size: int = _flag(16)
  # Interim temporal-age policy (2026-07-23): the jitter floor is clamped to
  # now - 10y; fully-historical columns keep their observed range. Subject to
  # per-column DDL-JSON functional descriptions later (audit fields, FK
  # referential integrity, M2+) — see b2_library/fidelity.py for the full
  # rationale.
  temporal_max_age_years_b1: int = _const(10, ge=1)
  # Interim temporal-age policy (2026-07-23): generated dates/datetimes/
  # timestamps must not be older than this. The jitter floor becomes
  # max(observed_min, now - 10y); columns whose ENTIRE observed range is older
  # keep it unchanged (fabricated recent dates would be worse than old truth).
  # EXPLICITLY interim: per-column behavior will later come from the DDL-JSON
  # description (audit fields that must keep fixed/consistent values, FK
  # referential integrity across linked tables, M2+) and must override this
  # blanket constant — keep the policy a single constant + clamp, not a
  # config surface, until that metadata exists.
  temporal_max_age_years_b2: int = _const(10, ge=1)


class FreeTextSettings(_Section):
  """Free-text pools: how they are built, routed and expanded."""
  freetext_expansion: Literal["off", "identifiers",
                              "all"] = _flag("identifiers")
  pool_seed_strategy: Literal["centroid", "kcenter",
                              "kcenter_rotate"] = _flag("centroid")
  prompt_constraints: Literal["on", "off"] = _flag("on")
  prompt_debug: Literal["off", "redacted", "full"] = _flag("off")
  pool_pattern_guidance: CliBoolEmpty = _flag("")
  build_pool_layer: CliBoolEmpty = _flag("")
  # The bounded-pool ceiling shared by the engines' free-text ladders and
  # the launcher's PK-capacity preflight (ADR 0028 P4): a constrained
  # column with no samplable pattern can never exceed this many distinct
  # values, so a PK routed there caps at it — the 2026-08-21 run DLQ'd
  # 999 488 of 1M rows exactly this way.
  free_text_pool_max: int = _const(512, ge=1)
  pool_values_per_call: int = _const(32, ge=1)
  # Server-side parallel sampling (2026-07-25 perf fix): each pool HTTP round
  # trip requests this many INDEPENDENT array-completions (`n=`) and de-dupes
  # across them, multiplying per-call novel yield ~4x. Safe because pool
  # requests are UNSEEDED — the 2026-07-16 collapse was n=32 single-value
  # choices under a pinned seed, a different shape entirely. The prompt stays
  # byte-identical across attempts (stable prefix ⇒ vLLM APC / LMCache-ready).
  pool_parallel_choices: int = _const(4, ge=1)
  # Stagnation exit (2026-07-23 E2E): 2 of 3 pool columns rode the full
  # 2*ceil(target/32)=32-call budget (~30 min of T4 setup) while marginal
  # novel yield had collapsed to cross-call duplicates and prompt echoes.
  # Once every escalation level has run, _POOL_STAGNATION_WINDOW consecutive
  # attempts each adding fewer than _POOL_STAGNATION_MIN_NOVEL novel values
  # end the ladder — more retries at the ceiling level cannot outrun the
  # yield decay.
  pool_stagnation_window: int = _const(3, ge=1)
  # None = max(1, pool_values_per_call // 8), the master derivation.
  pool_stagnation_min_novel: int | None = _const(None, ge=1)
  # Format-collapse exit (ADR 0033, 2026-08-25/26 R6 A_COL_037): 385/393
  # and 386/393 parsed values were format-rejected — 28-char values into a
  # fixed 31-char bucket, the model echoing its clause's own off-format
  # example — over three ~150 s T4 rounds before the stagnation window
  # closed. A full-yield round with ZERO in-format values is a structural
  # mismatch temperature cannot fix; two in a row end the ladder.
  pool_format_collapse_rounds: int = _const(2, ge=1)
  # Ladders for different columns are independent — run them on a bounded
  # thread pool. vLLM continuous-batches concurrent requests on the server
  # side; the client (openai/httpx) is thread-safe; embedder work is NOT
  # (HF "Already borrowed") and therefore finishes before any thread spawns.
  pool_build_max_workers: int = _const(4, ge=1)
  # Above this many distinct values the filter set stops being a cheap
  # worker-side frozenset; the store reports "unavailable" and the engine
  # degrades loudly to sample-only rejection. 1M strings ~= tens of MB on an
  # n1-highmem-8 — far below worker RAM, far above every observed E2E column
  # (max seen: 146k distinct, B_TABLE COL_038).
  source_domain_cap: int = _const(1_000_000, ge=1)
  # A string column with more unique non-null values than this *fraction* of
  # its non-null count, AND a mean rendered length above the threshold, is
  # treated as FREE_TEXT rather than CATEGORICAL. Tuned so low-cardinality
  # enums (country, tier) stay categorical while names / descriptions /
  # free-form notes go to the LLM pool.
  free_text_unique_ratio: float = _const(0.9, gt=0.0, le=1.0)
  free_text_min_mean_len: int = _const(20, ge=0)
  # Above this absolute distinct count a string column is high-cardinality and
  # treated as free text even if short (e.g. emails, ids-as-strings).
  free_text_max_categories: int = _const(50, ge=1)

  @property
  def stagnation_min_novel(self) -> int:
    """`pool_stagnation_min_novel`, or the derived default when unset."""
    if self.pool_stagnation_min_novel is not None:
      return self.pool_stagnation_min_novel
    return max(1, self.pool_values_per_call // 8)


class RagSettings(_Section):
  """B.1 retrieval: the embedder, the chunk store and the exemplar pick."""
  embedder_uri: str = _flag("")
  build_rag_layer: CliBoolEmpty = _flag("")
  rag_row_docs_limit: Annotated[int, BeforeValidator(_row_docs)] = _flag(
      _ROW_DOCS_DEFAULT, ge=1)
  # free_text_col chunks dedupe to distinct (column, value); a pathological
  # near-unique column stays bounded here (consumers pick top-k=8 exemplars).
  max_free_text_values_per_column: int = _const(1024, ge=1)
  # Top-k exemplars retrieved to condition the LLM's free-text inference.
  rag_top_k: int = _const(8, ge=1)
  bge_max_length: int = _const(512, ge=1)
  # bge-small-en-v1.5 is ~130 MB of fp32 weights; activations and CUDA context
  # push the real footprint higher. Require this much FREE VRAM before "auto"
  # picks CUDA. The 2026-07-26 E2E asked for 2 MiB with 2.81 MiB free and
  # OOMed: cuda.is_available() answers "does a GPU exist", never "is there
  # room", and on a DoFn.setup() RETRY the module-level vLLM server reuse
  # (ADR 0014) means the card is already spoken for.
  embedder_min_free_vram: int = _const(512 * 1024 * 1024, ge=0)


class GuardrailSettings(_Section):
  """The gate: environment tier, uniqueness and the landing write mode."""
  env: str = _flag("dev")
  uniqueness_mode: UniquenessMode = _flag("exact")
  driven_uniqueness_mode: UniquenessMode = _flag("streaming")
  write_disposition: Literal["append", "overwrite"] = _flag("append")
  thresholds_uri: str = _flag("config/thresholds.yml")
  # Absolute tolerance between the SOURCE key-repeat share and the one the
  # landing table lands with (ADR 0038 §5).
  #
  # The copy is faithful BY CONSTRUCTION — every parent key draws its child
  # count from the same measured histogram — so the only spread is
  # sampling: the per-key draw converges at O(1/sqrt(keys)) (well under
  # half a point at the 52k keys of the 2026-09-12 launch), plus the
  # derived row count's rounding and, on an orphan-heavy source, the fact
  # that the child copies the MATCHED share's distribution rather than the
  # whole source's. Five points is loose enough to survive all three and
  # tight enough that a structural break is unmissable: a fan-out capped
  # to one child lands ~0.00 and a doubled one ~0.75, both far outside.
  repeat_share_tolerance: float = _const(0.05, ge=0.0, le=1.0)


class RelationalSettings(_Section):
  """Keys, edges and the FK key samples."""
  generate_fk_relationships: CliBoolFalse = _flag("true")
  relationships_uri: str = _flag("config/relationships")
  multi_table_mode: Literal["single_job",
                            "sequential_jobs"] = _flag("single_job")
  on_model_conflict: Literal["adjust", "stop"] = _flag("adjust")
  fk_candidate_cap: int = _flag(64, ge=1)
  identity_cols: CsvText = _flag("")
  pk_cols: CsvText = _flag("")
  fk_parent_landing: str = _flag("")
  # The ADR 0030 side-input cap: a child samples at most this many parent
  # key tuples unless its PK needs more. Mirrored by `io/fk_pools._DEFAULT_LIMIT`.
  fk_key_sample_floor: int = _const(100_000, ge=1)
  # Largest side input this sizing will ask for. 1M single-column string
  # keys is ~40 MB pickled / ~130 MB live per SDK process; beyond it the
  # scaling answer is the co-partitioned join (ADR 0031 escape hatch).
  fk_key_sample_ceiling: int = _const(1_000_000, ge=1)
  # Capacity / num_rows target: at 10x, expected duplicates are ~4.8%.
  fk_key_sample_margin: int = _const(10, ge=1)
  # ADR 0037 §7 — a fan-out request carries `keys_per_batch * M` candidate
  # tuples per conditional edge; `keys_per_batch` is lowered so the whole
  # request stays under this many values, whatever the cap is set to.
  max_conditional_values_per_request: int = _const(100_000, ge=1)

  @model_validator(mode="after")
  def _floor_below_ceiling(self) -> RelationalSettings:
    if self.fk_key_sample_floor > self.fk_key_sample_ceiling:
      raise ValueError(
          "fk_key_sample_floor must not exceed fk_key_sample_ceiling")
    return self


class ServingSettings(_Section):
  """The model, the vLLM server and the worker fleet."""
  client_type: Literal["vllm", "mlx", "fake"] = _flag("vllm")
  model_uri: str | None = _flag(None)
  vllm_dtype: Literal["auto", "float16", "bfloat16"] = _flag("auto")
  vllm_max_model_len: NumericText = _flag("8192")
  autoscaling: Literal["auto", "throughput", "fixed"] = _flag("auto")
  initial_workers: NumericText = _flag("")


SECTION_TYPES: dict[str, type[_Section]] = {
    "io": IoSettings,
    "launch": LaunchSettings,
    "sampling": SamplingSettings,
    "generation": GenerationSettings,
    "free_text": FreeTextSettings,
    "rag": RagSettings,
    "guardrails": GuardrailSettings,
    "relational": RelationalSettings,
    "serving": ServingSettings,
}
SECTIONS: tuple[str, ...] = tuple(SECTION_TYPES)


class GeneratorConfig(BaseModel):
  """The whole generator configuration of one launch."""
  model_config = ConfigDict(frozen=True, extra="forbid")

  config_version: int = CONFIG_VERSION
  io: IoSettings = Field(default_factory=IoSettings)
  launch: LaunchSettings = Field(default_factory=LaunchSettings)
  sampling: SamplingSettings = Field(default_factory=SamplingSettings)
  generation: GenerationSettings = Field(default_factory=GenerationSettings)
  free_text: FreeTextSettings = Field(default_factory=FreeTextSettings)
  rag: RagSettings = Field(default_factory=RagSettings)
  guardrails: GuardrailSettings = Field(default_factory=GuardrailSettings)
  relational: RelationalSettings = Field(default_factory=RelationalSettings)
  serving: ServingSettings = Field(default_factory=ServingSettings)


def _keys(kind: str) -> dict[str, str]:
  out: dict[str, str] = {}
  for section, cls in SECTION_TYPES.items():
    for name, info in cls.model_fields.items():
      extra = info.json_schema_extra
      if isinstance(extra, dict) and extra.get("kind") == kind:
        out[name] = f"{section}.{name}"
  return out


FLAG_KEYS: dict[str, str] = _keys(KIND_FLAG)
IDENTITY_KEYS: dict[str, str] = _keys(KIND_IDENTITY)
CONSTANT_KEYS: tuple[str, ...] = tuple(_keys(KIND_CONSTANT).values())
ALL_KEYS: tuple[str, ...] = (
    tuple(FLAG_KEYS.values()) + tuple(IDENTITY_KEYS.values()) + CONSTANT_KEYS)
REQUIRED_FLAGS: tuple[str,
                      ...] = ("reference_table", "landing_table", "dlq_table",
                              "num_rows", "run_id", "model_uri")

DEFAULT_CONFIG = GeneratorConfig()


def value_of(config: GeneratorConfig, dotted: str) -> Any:
  """The effective value of one dotted key (derived defaults resolved)."""
  section, key = dotted.split(".", 1)
  if dotted == "free_text.pool_stagnation_min_novel":
    return config.free_text.stagnation_min_novel
  return getattr(getattr(config, section), key)
