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
"""B.2: one all-literal sentinel mask must not sink the template rescue.

2026-10-28 / 2026-11-12 b2_library runs on the corp relationship model:
TABLE_C.COL_CODE is a CHAR(8)-style two-letter code (57 distinct
``AA`` + six spaces) plus one NUL-padded "no value" sentinel
(``\\x00\\x00`` + six spaces). B.2's shape mix is built from the deduped
pool, so each distinct value weighs 1 and the mass-weighted class-position
mean is (57 * 2 + 1 * 0) / 58 = 1.97 < 2.0: ``shape_mix_can_template`` is
False. The LLM echoed the 32 shown exemplars on all 3 attempts, the
copy-saturation rescue hit the same gate, and strict mode raised
``FreeTextEmptyYieldError`` on every batch (BlockerGate 1.0, job FAILED).

The fix templates from the class-bearing masks only, as the LAST resort
(where the strict raise used to fire), so every value is still rejected
against the observed pool and the source domain.
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=missing-class-docstring,protected-access

from __future__ import annotations

import logging
import re
import string

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationConfig
from sdfb_core.engines.b2_library.fidelity import ColumnKind, profile_table
from sdfb_core.engines.b2_library.freetext import (
    FreeTextHook,
    _class_bearing_mix,
)
from sdfb_core.engines.text_shapes import (
    build_shape_mix,
    shape_mix_can_template,
)

_PAD = " " * 6
_SENTINEL = "\x00\x00" + _PAD
# 57 fictitious two-letter codes, deterministic.
_CODES = [a + b + _PAD for a in "ABCDEFG" for b in "HIJKLMNOP"][:57]
_POOL_SIZE = 32
_CODE_RE = re.compile(r"[A-Z]{2} {6}")


def _profile():
  schema = TableSchema.model_validate({
      "table_info": {
          "table_id": "demo.table_c"
      },
      "schema": [{
          "name": "COL_CODE",
          "type": "STRING",
          "mode": "NULLABLE"
      }],
      "primary_keys": None,
  })
  rows = [{"COL_CODE": v} for v in [*_CODES, _SENTINEL]]
  return profile_table(schema, rows)["COL_CODE"]


class _EchoShownClient:
  """Returns exactly the exemplars shown in the prompt, every call."""

  def __init__(self, shown):
    self._shown = list(shown)
    self.calls = 0

  def generate_json(self, *a, **k):  # pylint: disable=unused-argument
    self.calls += 1
    return [{"values": list(self._shown)}]


class TestPrecondition:

  def test_the_sentinel_drags_the_mix_below_the_template_gate(self) -> None:
    p = _profile()
    assert p.kind is ColumnKind.FREE_TEXT
    assert p.identifier_shape is None  # spaces: no identifier route
    assert len(p.text_pool) == 58
    assert shape_mix_can_template(p.shape_mix) is False
    # Without the sentinel the same codes template fine.
    assert shape_mix_can_template(build_shape_mix(_CODES)) is True


class TestEchoSaturatedStrictBuild:

  def test_builds_a_novel_in_format_pool_instead_of_raising(self,
                                                            caplog) -> None:
    p = _profile()
    client = _EchoShownClient(p.text_pool[:_POOL_SIZE])
    hook = FreeTextHook(client, pool_size=_POOL_SIZE, strict=True)
    cfg = GenerationConfig(seed=3, engine_specific={"pool_seed": 41})
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
      pool = hook._pool_for(p, cfg)
    assert len(pool) == _POOL_SIZE
    assert len(set(pool)) == _POOL_SIZE
    assert all(_CODE_RE.fullmatch(v) for v in pool), pool
    assert not set(pool) & set(p.text_pool)  # novel: no observed copy
    text = "\n".join(r.message for r in caplog.records)
    assert "name=freetext_shape_mix_literal_masks_dropped" in text
    assert "name=freetext_pool_shape_fallback" in text
    assert "name=freetext_pool_empty " not in text
    assert "column=COL_CODE" in text
    # Cached: the next batch pays no LLM calls.
    calls = client.calls
    assert hook._pool_for(p, cfg) == pool
    assert client.calls == calls

  def test_pool_is_deterministic_per_pool_seed(self) -> None:
    p = _profile()

    def build(seed: int) -> list[str]:
      hook = FreeTextHook(
          _EchoShownClient(p.text_pool[:_POOL_SIZE]),
          pool_size=_POOL_SIZE,
          strict=True)
      return hook._pool_for(
          p, GenerationConfig(seed=3, engine_specific={"pool_seed": seed}))

    assert build(41) == build(41)
    assert build(41) != build(42)

  def test_generated_rows_never_carry_the_sentinel(self) -> None:
    # The literal-only sentinel mask is dropped from the template, and
    # the observed sentinel itself is rejected as a copy.
    p = _profile()
    hook = FreeTextHook(
        _EchoShownClient(p.text_pool[:_POOL_SIZE]),
        pool_size=_POOL_SIZE,
        strict=True)
    pool = hook._pool_for(
        p, GenerationConfig(seed=3, engine_specific={"pool_seed": 41}))
    assert all("\x00" not in v for v in pool)


class TestClassBearingMix:

  def test_drops_only_the_all_literal_masks(self) -> None:
    mix = build_shape_mix([*_CODES, _SENTINEL])
    kept = _class_bearing_mix(mix)
    assert kept is not None
    assert shape_mix_can_template(kept) is True
    assert len(kept) == len(mix) - 1

  @pytest.mark.parametrize(
      "mix",
      [
          None,
          (),
          # all-literal: nothing varies, nothing to template
          build_shape_mix(["hello world foo", "another prose line"]),
      ],
      ids=["none", "empty", "all-literal"],
  )
  def test_nothing_to_rescue_is_none(self, mix) -> None:
    assert _class_bearing_mix(mix) is None

  def test_an_already_templatable_mix_needs_no_rescue(self) -> None:
    # The rescue only exists for mixes the exact gate refuses.
    assert _class_bearing_mix(build_shape_mix(_CODES)) is None

  def test_a_single_class_position_stays_below_the_gate(self) -> None:
    # The >= 2 class-position rule still holds on the kept masks.
    one_class = [c + "-" for c in string.ascii_uppercase[:10]]
    mix = build_shape_mix([*one_class, _SENTINEL])
    assert _class_bearing_mix(mix) is None
