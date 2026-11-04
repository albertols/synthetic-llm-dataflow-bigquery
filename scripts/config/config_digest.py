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
"""Print the config digest of a generator config file (ADR 0044).

The digest the generation record writes (`validation_runs.config_digest`):
check that a config.yaml downloaded from the GUI replays the recorded run.

    uv run --no-sync python scripts/config/config_digest.py downloaded.yaml

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sdfb_core.config.resolve import ConfigError, parse_config_text, resolve
from sdfb_core.record import config_digest


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("path", type=Path)
  args = parser.parse_args(argv)
  try:
    text = args.path.read_text(encoding="utf-8")
    config = resolve({}, parse_config_text(text, source=str(args.path))).config
  except (OSError, ConfigError) as e:
    print(f"config_digest: {e}", file=sys.stderr)
    return 2
  print(config_digest(config))
  return 0


if __name__ == "__main__":
  sys.exit(main())
