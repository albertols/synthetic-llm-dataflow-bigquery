#!/usr/bin/env bash
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
