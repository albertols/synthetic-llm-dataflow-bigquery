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

# Synthetic Platform GUI (ADR 0042): the Fastify BFF serving the built SPA.
# This repository does not deploy it; build and run it privately:
#
#   docker build -f gui/Dockerfile -t synthetic-platform gui
#   docker run --rm -p 127.0.0.1:8787:8787 synthetic-platform                  # mock data
#   docker run --rm -p 127.0.0.1:8787:8787 -e DATA_SOURCE=bigquery \
#     -e GCP_PROJECT=my-project -v "$HOME/.config/gcloud:/home/node/.config/gcloud:ro" \
#     synthetic-platform                                                       # live, read-only
#
# Inside the container the BFF binds 0.0.0.0 so the port can be published;
# publish it on 127.0.0.1 only. Credentials stay in the server process.

FROM node:22-slim AS build
WORKDIR /gui
COPY package.json package-lock.json .npmrc ./
COPY apps/web/package.json apps/web/
COPY apps/server/package.json apps/server/
COPY packages/contracts/package.json packages/contracts/
COPY packages/stats/package.json packages/stats/
COPY packages/mock/package.json packages/mock/
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build -w @synthetic-platform/web

FROM node:22-slim AS runtime
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8787 \
    DATA_SOURCE=mock
WORKDIR /gui
COPY package.json package-lock.json .npmrc ./
COPY apps/web/package.json apps/web/
COPY apps/server/package.json apps/server/
COPY packages/contracts/package.json packages/contracts/
COPY packages/stats/package.json packages/stats/
COPY packages/mock/package.json packages/mock/
RUN npm ci --omit=dev --workspace @synthetic-platform/server --no-audit --no-fund \
    && npm cache clean --force
COPY --from=build /gui/apps/server/src apps/server/src
COPY --from=build /gui/apps/server/tsconfig.json apps/server/
COPY --from=build /gui/tsconfig.base.json ./
COPY --from=build /gui/packages packages
COPY --from=build /gui/apps/web/dist apps/web/dist
USER node
EXPOSE 8787
CMD ["npm", "start", "-w", "@synthetic-platform/server"]
