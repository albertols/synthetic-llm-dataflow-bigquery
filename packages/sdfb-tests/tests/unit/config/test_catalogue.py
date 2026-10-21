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
"""The catalogue documents every setting exactly once (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import re

import pytest
from sdfb_core.config.catalogue import (CACHES, CatalogueError, load_catalogue,
                                        parse_catalogue, render_text)
from sdfb_core.config.model import ALL_KEYS, DEFAULT_CONFIG

_EXPECTED_CACHES = {
    "source_stats": {
        "sampling.top_values_max_distinct", "sampling.top_values_top_k",
        "sampling.shape_mix_top_k", "sampling.length_percentiles",
        "sampling.null_pattern_max_cols", "sampling.null_pattern_top_k"
    },
    "freetext_pools": {
        "free_text.free_text_pool_max", "free_text.pool_values_per_call",
        "free_text.pool_parallel_choices", "free_text.pool_stagnation_window",
        "free_text.pool_stagnation_min_novel",
        "free_text.pool_format_collapse_rounds", "free_text.source_domain_cap",
        "free_text.free_text_unique_ratio", "free_text.free_text_min_mean_len",
        "free_text.free_text_max_categories",
        "rag.max_free_text_values_per_column", "rag.rag_top_k"
    },
    "rag_chunks": {
        "free_text.free_text_unique_ratio", "free_text.free_text_min_mean_len",
        "free_text.free_text_max_categories",
        "rag.max_free_text_values_per_column", "rag.bge_max_length"
    },
    "fk_fanout_stats": {
        "free_text.free_text_unique_ratio", "free_text.free_text_min_mean_len",
        "free_text.free_text_max_categories"
    },
}


def test_settings_match_the_model_one_to_one():
  cat = load_catalogue()
  settable = {k for k, e in cat.entries.items() if e.status == "settable"}
  assert settable == set(ALL_KEYS)


def test_not_settable_entries():
  cat = load_catalogue()
  assert len(cat.not_settable) == 14
  assert "generation.temperature_ladder" in cat.not_settable
  assert "serving.gpu" in cat.not_settable


def test_cache_map_is_pinned():
  shaped = load_catalogue().caches_shaped_by
  assert set(shaped) == CACHES
  assert {c: set(keys) for c, keys in shaped.items()} == _EXPECTED_CACHES


@pytest.mark.parametrize("bad, message", [
    ("catalogue_version: '1'\nsettings: {a.b: {label: x}}\n", "missing"),
    ("catalogue_version: '1'\nsettings: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d, colour: red}}\n", "unknown"),
    ("catalogue_version: '1'\nsettings: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d, shapes_cache: [disk]}}\n", "shapes_cache"),
    ("catalogue_version: '1'\nfixed: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d}}\n", "why_fixed"),
    ("catalogue_version: '1'\nextra: {}\n", "top-level"),
    ("catalogue_version: '1'\nsettings: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d, adrs: [0017]}}\n", "four-digit"),
    ("catalogue_version: '1'\nsettings: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d, adrs: ['43']}}\n", "four-digit"),
    ("catalogue_version: '1'\nsettings: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d, refs: [{kind: doc, path: x.md}]}}\n",
     "label"),
    ("catalogue_version: '1'\nsettings: {a.b: {label: x, group: g, "
     "purpose: p, up: u, down: d, refs: [{kind: doc, label: L}]}}\n",
     "url or a path"),
])
def test_strict_loader(bad, message):
  # adrs: [0017] is the octal int 15 under YAML 1.1, so ids must be quoted.
  with pytest.raises(CatalogueError, match=message):
    parse_catalogue(bad)


def test_stats_only_settings_say_what_they_change():
  # Neither key reaches a prompt or the shape expander: the length hint and
  # the engines' shape mixes have their own fixed values.
  entries = load_catalogue().entries
  for key in ("sampling.shape_mix_top_k", "sampling.length_percentiles"):
    entry = entries[key]
    assert "source_table_stats" in entry.purpose, key
    prose = " ".join((entry.purpose, entry.up, entry.down))
    assert "expander" not in entry.up and "into prompts" not in prose, key


def test_placeholders_render_like_the_gui():
  assert render_text("{{value:rag.rag_row_docs_limit}}") == "1,024"
  assert render_text("{{tex:free_text.free_text_pool_max}}") == "512"
  assert render_text("{{tex:sampling.reference_rows_limit}}") == "10{,}000"
  assert render_text("{{dkw_eps:sampling.reference_rows_limit}}") == "0.0136"
  assert render_text("{{dup_share:relational.fk_key_sample_margin}}") == "4.8 %"
  assert render_text(
      "{{dup_share_tex:relational.fk_key_sample_margin}}") == "4.8\\%"
  assert render_text(r"\frac{M}{\min(M)}") == r"\frac{M}{\min(M)}"
  assert render_text("{{value:rag.rag_top_k}}", DEFAULT_CONFIG) == "8"


def test_placeholder_to_an_unknown_key_fails():
  with pytest.raises(CatalogueError, match="unknown key"):
    render_text("{{value:rag.nope}}")


def test_every_placeholder_in_the_file_resolves():
  for entry in load_catalogue().entries.values():
    for text in (entry.purpose, entry.up, entry.down, entry.tip, entry.formula):
      # Renders without CatalogueError, and no known placeholder survives.
      assert not re.search(r"\{\{(value|tex|dkw_eps|dup_share)",
                           render_text(text))
