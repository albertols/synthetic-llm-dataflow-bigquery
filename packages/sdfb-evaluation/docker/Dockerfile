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

# The evaluator's CPU image: the Dataflow flex template LAUNCHER and, via
# --sdk_container_image, the WORKER harness. Build context is the repository
# root (see deploy/build_flex_template.sh). Python 3.11 and Beam 2.74.0 must
# match packages/sdfb-evaluation/pyproject.toml and uv.lock; the unit test
# tests/unit/test_launch_surfaces.py checks both.
FROM apache/beam_python3.11_sdk:2.74.0

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/

WORKDIR /template

# Dependencies first (this layer survives source changes), then the source.
COPY packages/sdfb-evaluation/pyproject.toml packages/sdfb-evaluation/uv.lock packages/sdfb-evaluation/.python-version packages/sdfb-evaluation/README.md ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python
COPY packages/sdfb-evaluation/src ./src
RUN uv sync --frozen --no-dev --python /usr/local/bin/python

COPY --from=gcr.io/dataflow-templates-base/python311-template-launcher-base /opt/google/dataflow/python_template_launcher /opt/google/dataflow/python_template_launcher

# Two execution contexts import sdfb_evaluation, both from /template/.venv:
#   - LAUNCHER (the flex entry): honors PYTHONPATH, so the venv
#     site-packages and the editable src dir go on it directly.
#   - WORKER: Beam >= 2.44 builds its OWN --system-site-packages venv at
#     startup and DISCARDS the container's venv and PYTHONPATH (Google "Build
#     custom container images for Dataflow"). It does inherit the GLOBAL site
#     dir and processes its .pth files, so a bridge .pth there runs
#     site.addsitedir() on our venv, activating the dependencies and the
#     editable sdfb_evaluation finder. Same mechanism as the generator's GPU
#     image (docker/Dockerfile at the repository root).
ENV PYTHONPATH="/template/.venv/lib/python3.11/site-packages:/template/src"
RUN SITE="$(python -c 'import site; print(site.getsitepackages()[0])')" \
    && printf "import site; site.addsitedir('/template/.venv/lib/python3.11/site-packages')\n" \
        > "${SITE}/zz_sdfb_bridge.pth" \
    && echo "Wrote worker bridge .pth -> ${SITE}/zz_sdfb_bridge.pth"

# Flex Template launcher contract. The entry submits the job and returns;
# it does not wait for it and never applies --fail_on.
ENV FLEX_TEMPLATE_PYTHON_PY_FILE="/template/src/sdfb_evaluation/cli/run_evaluation.py"
