/**
 * The RAG tab's worker: PCA (then clusters and k-NN preservation), UMAP
 * (then k-NN preservation) and the retrieval simulator's strategies, off the
 * main thread so the cloud keeps orbiting on a laptop. umap-js is imported
 * here only, so it never lands in a page chunk.
 */
import { mulberry32 } from "@synthetic-platform/stats";
import { UMAP } from "umap-js";

import { knnKept, normalizeCoords, runPca, sphericalKMeans, UMAP_PARAMS } from "./projection";
import type { WorkerRequest, WorkerResponse } from "./projectionProtocol";
import { computeStrategies } from "./strategyJob";

interface WorkerScope {
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null;
  postMessage(message: WorkerResponse, transfer?: Transferable[]): void;
}

const scope = self as unknown as WorkerScope;

function post(message: WorkerResponse, transfer: Transferable[] = []) {
  scope.postMessage(message, transfer);
}

function runUmap(id: number, vectors: Float32Array, dim: number) {
  const n = Math.floor(vectors.length / dim);
  const data: number[][] = new Array<number[]>(n);
  for (let i = 0; i < n; i += 1) data[i] = Array.from(vectors.subarray(i * dim, (i + 1) * dim));
  const umap = new UMAP({
    nComponents: 3,
    nNeighbors: Math.min(UMAP_PARAMS.nNeighbors, Math.max(2, n - 1)),
    minDist: UMAP_PARAMS.minDist,
    random: mulberry32(UMAP_PARAMS.seed),
  });
  const epochs = umap.initializeFit(data);
  for (let epoch = 0; epoch < epochs; epoch += 1) {
    umap.step();
    if (epoch % 25 === 0) post({ id, type: "progress", done: epoch, total: epochs });
  }
  const raw = new Float32Array(n * 3);
  umap.getEmbedding().forEach((point, i) => {
    raw[i * 3] = point[0] ?? 0;
    raw[i * 3 + 1] = point[1] ?? 0;
    raw[i * 3 + 2] = point[2] ?? 0;
  });
  const { coords, frame } = normalizeCoords(raw);
  post({ id, type: "umap", coords: coords.slice(), frame });
  post({ id, type: "umap-extras", trust: knnKept(vectors, dim, coords) });
}

scope.onmessage = (event) => {
  const request = event.data;
  try {
    if (request.kind === "pca") {
      const result = runPca(request.vectors, request.dim);
      post({ id: request.id, type: "pca", coords: result.coords.slice(), frame: result.frame, pca: result.pca! });
      const clusters = sphericalKMeans(request.vectors, request.dim);
      const trust = knnKept(request.vectors, request.dim, result.coords);
      post({ id: request.id, type: "pca-extras", clusters, trust }, [clusters.buffer]);
    } else if (request.kind === "umap") runUmap(request.id, request.vectors, request.dim);
    else {
      const result = computeStrategies(request.vectors, request.dim, request.k, request.attempt, request.walk);
      post({ id: request.id, type: "strategies", ...result }, [result.walk.frontier.buffer]);
    }
  } catch (error) {
    post({ id: request.id, type: "error", message: error instanceof Error ? error.message : String(error) });
  }
};
