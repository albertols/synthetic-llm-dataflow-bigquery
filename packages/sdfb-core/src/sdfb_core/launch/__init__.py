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
"""Whole-model launches: which tables a launch generates, from which
sources, with how many rows, and in which batch jobs (ADR 0045).

Pure Python, like the rest of sdfb-core: the launcher (`sdfb_beam.cli.
run_pipeline`) and `scripts/launch/run_model.py` read it, and the Composer
Composer DAG mirrors `batches` in `composer/sdfb_batches.py`.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
