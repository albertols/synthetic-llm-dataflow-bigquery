#!/bin/sh
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

# Dispatch entrypoint for the evaluator image (a copy of the generator's
# docker/entrypoint.sh: this package stands alone, so it carries its own;
# the generator's ADR 0009 records why).
#
# The same image serves two Dataflow roles that need DIFFERENT entrypoints:
#
#   * Flex Template LAUNCHER  -> /opt/google/dataflow/python_template_launcher
#       Invoked with template flags or none; never with Beam FnAPI boot flags.
#
#   * Runner v2 WORKER harness -> /opt/apache/beam/boot
#       Dataflow runs the image ENTRYPOINT and APPENDS the Beam FnAPI boot flags
#       (--id, --logging_endpoint, --control_endpoint, --artifact_endpoint,
#       --provision_endpoint). It does NOT override the entrypoint.
#
# Discriminator: only the worker boot passes those flags, so if any is seen,
# exec boot; otherwise exec the launcher.

for arg in "$@"; do
  case "$arg" in
    --id=* | --logging_endpoint=* | --control_endpoint=* | \
    --artifact_endpoint=* | --provision_endpoint=*)
      exec /opt/apache/beam/boot "$@"
      ;;
  esac
done

exec /opt/google/dataflow/python_template_launcher "$@"
