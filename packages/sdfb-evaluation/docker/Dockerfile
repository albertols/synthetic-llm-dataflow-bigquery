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

# The evaluator's STANDALONE CPU image: the Dataflow flex template LAUNCHER
# and, via --sdk_container_image, the WORKER harness.
#
# This repository's deployment does not build it. The deployment uses one
# image for generation and evaluation, docker/Dockerfile at the repository
# root, which carries this package's source and runs it on the generator's
# environment. This file is for the package copied out as a unit of its own:
# a small image with the evaluator on its own lock and nothing else.
#
# No script builds it. From the repository root (the build context):
#
#   docker build -f packages/sdfb-evaluation/docker/Dockerfile \
#     --build-arg SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG=<the coordinate it is pushed to> \
#     -t <the same coordinate> .
#
# then deploy/build_flex_template.sh builds a template from the pushed image.
# Here the template's entry is the evaluator itself (run_evaluation.py below),
# not the shared image's dispatcher.
#
# Python 3.11 and Beam 2.74.0 must match packages/sdfb-evaluation/pyproject.toml
# and uv.lock; the unit test tests/unit/test_launch_surfaces.py checks both.
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

# Worker harness image. The flex template's --image sets only the LAUNCHER;
# workers boot whatever image the job names. The image carries its own pushed
# coordinate (the build argument of the command in the header) and the driver
# applies it as sdk_container_image when the launch gave none; an explicit
# value wins. Declared late so it never invalidates the dependency layers above.
ARG SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG=""
ENV SDFB_EVAL_SDK_CONTAINER_IMAGE=$SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG

# Dispatch entrypoint: Dataflow appends the Beam boot flags to the image's
# ENTRYPOINT for workers and runs it bare for the launcher, so one script
# picks the binary (see docker/entrypoint.sh).
COPY packages/sdfb-evaluation/docker/entrypoint.sh /opt/sdfb/entrypoint.sh
RUN chmod +x /opt/sdfb/entrypoint.sh
ENTRYPOINT ["/opt/sdfb/entrypoint.sh"]
