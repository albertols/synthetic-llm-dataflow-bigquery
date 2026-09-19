#!/usr/bin/env bash
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

# Shared helpers. Source AFTER env.sh, forwarding "$@" so --dry-run is seen:
#   source "${SCRIPT_DIR}/lib/common.sh" "$@"
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
for _arg in "$@"; do
  [[ "${_arg}" == "--dry-run" ]] && DRY_RUN=1
done

log() { printf '[%s] %s\n' "$(basename "${BASH_SOURCE[1]:-script}")" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Mutating command: print in dry-run, execute otherwise.
run() {
  if [[ "${DRY_RUN}" == "1" ]]; then echo "+ $*"; else "$@"; fi
}

# Existence probe: in dry-run pretend ABSENT (return 1) so create paths print.
probe() {
  if [[ "${DRY_RUN}" == "1" ]]; then return 1; else "$@" >/dev/null 2>&1; fi
}

require_env() {
  local v
  for v in "$@"; do
    [[ -n "${!v:-}" ]] || die "${v} is not set — export it or edit env.sh (see public_cloud/deploy/gcp/README.md)"
  done
}
