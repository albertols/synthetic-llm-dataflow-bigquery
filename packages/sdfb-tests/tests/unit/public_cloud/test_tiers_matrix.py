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
"""Drift guard: every tiers.yaml parameter must exist in the mainline Flex
Template metadata, and all required template params must be provided.
Catches the 'template gained a param, personal layer did not' bug class
(vllm_max_model_len, 2026-07-14)."""
from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
GCP_DIR = REPO_ROOT / "public_cloud" / "deploy" / "gcp"
METADATA = REPO_ROOT / "docker" / "flex_template_metadata.json"

spec = importlib.util.spec_from_file_location(
    "render_tier", GCP_DIR / "lib" / "render_tier.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

VARS = {"PROJECT_ID": "p1", "MODELS_BUCKET": "mb", "DATAFLOW_BUCKET": "db"}
TIERS = ["S0", "R1p", "R2p", "R3p", "N4", "P6", "P7"]
TABLES = ["citibike", "hacker_news"]

meta = json.loads(METADATA.read_text())
TEMPLATE_PARAMS = {p["name"] for p in meta["parameters"]}
REQUIRED = {p["name"] for p in meta["parameters"] if not p.get("isOptional")}
RUNTIME_ADDED = {"run_id"}  # run_e2e.sh generates and appends run_id


def test_every_tier_param_exists_in_template_metadata():
  for tier, table in itertools.product(TIERS, TABLES):
    params, _ = rt.render(GCP_DIR / "tiers.yaml", tier, table, VARS)
    unknown = set(params) - TEMPLATE_PARAMS
    assert not unknown, f"{tier}/{table}: params not in flex_template_metadata.json: {unknown}"


def test_every_required_template_param_is_provided():
  for tier, table in itertools.product(TIERS, TABLES):
    params, _ = rt.render(GCP_DIR / "tiers.yaml", tier, table, VARS)
    missing = REQUIRED - set(params) - RUNTIME_ADDED
    assert not missing, f"{tier}/{table}: required template params missing: {missing}"
