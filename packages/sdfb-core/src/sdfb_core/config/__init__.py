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
"""Generator configuration (ADR 0043): model, catalogue, resolution.

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from sdfb_core.config.model import (ALL_KEYS, CONFIG_VERSION, CONSTANT_KEYS,
                                    DEFAULT_CONFIG, FLAG_KEYS, IDENTITY_KEYS,
                                    REQUIRED_FLAGS, SECTION_TYPES, SECTIONS,
                                    GeneratorConfig, value_of)
from sdfb_core.config.resolve import (ConfigError, ConfigResolution, Override,
                                      missing_required, namespace_values,
                                      parse_config_text, resolve)

__all__ = [
    "ALL_KEYS", "CONFIG_VERSION", "CONSTANT_KEYS", "DEFAULT_CONFIG",
    "FLAG_KEYS", "IDENTITY_KEYS", "REQUIRED_FLAGS", "SECTIONS", "SECTION_TYPES",
    "ConfigError", "ConfigResolution", "GeneratorConfig", "Override",
    "missing_required", "namespace_values", "parse_config_text", "resolve",
    "value_of"
]
