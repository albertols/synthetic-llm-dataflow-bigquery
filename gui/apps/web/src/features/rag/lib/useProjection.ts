/**
 * The cloud's off-main-thread work, in one module worker (projection.worker.ts):
 *
 * - PCA → the layout first, then the 384-d clusters and k-NN preservation;
 * - UMAP → the layout, then k-NN preservation; a finished UMAP layout is
 *   cached by the BFF (`/api/x/rag/projection`, in memory) so the next visit
 *   skips the fit;
 * - strategies → every strategy's seeds and metrics, and the k-center walk.
 *
 * Without Worker support (tests, old browsers) PCA and the strategies run
 * inline, and UMAP says it needs a worker. The worker is terminated on route
 * leave.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { Cloud } from "./useCloud";
import { knnKept, runPca, sphericalKMeans, UMAP_PARAMS, type ProjectionResult } from "./projection";
import type { WorkerRequest, WorkerResponse } from "./projectionProtocol";
import { computeStrategies, type StrategyJobResult } from "./strategyJob";

export type ProjectionMethod = "pca" | "umap";

export type LayoutEntry =
  | { status: "running"; progress: number | null }
  | {
      status: "done";
      result: ProjectionResult;
      /** k-NN preservation; undefined while it is being computed. */
      trust?: number | null;
      /** Spherical k-means labels (PCA job only); undefined while computing. */
      clusters?: Int32Array;
      /** UMAP: served from the BFF cache instead of fitted here. */
      fromCache?: boolean;
      /** UMAP: the cache write failed (the layout is still shown). */
      cacheNote?: string;
    }
  | { status: "error"; message: string };

export type StrategiesEntry =
  { status: "running" } | { status: "done"; data: StrategyJobResult } | { status: "error"; message: string };

export const UMAP_PARAM_KEY = `umap-js 1.4.0; nNeighbors=${UMAP_PARAMS.nNeighbors}; minDist=${UMAP_PARAMS.minDist}; seed=${UMAP_PARAMS.seed}`;

/** A request without its id (distributes over the union). */
type Job = WorkerRequest extends infer R ? (R extends WorkerRequest ? Omit<R, "id"> : never) : never;
type Listener = (message: WorkerResponse) => void;

const TERMINAL = new Set<WorkerResponse["type"]>(["pca-extras", "umap-extras", "strategies", "error"]);

function projectionUrl(cloud: Cloud, digest: string, embedder: string): string {
  const params = new URLSearchParams({
    digest,
    embedder,
    space: cloud.space,
    ids: cloud.idsHash,
    params: UMAP_PARAM_KEY,
  });
  return `/api/x/rag/projection?${params.toString()}`;
}

interface CachedLayout {
  hit: boolean;
  n?: number;
  coords?: number[];
  frame?: ProjectionResult["frame"];
  trust?: number | null;
}

async function readCache(url: string, n: number): Promise<{ result: ProjectionResult; trust: number | null } | null> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    if (!response.ok) return null;
    const body = (await response.json()) as CachedLayout;
    if (!body.hit || body.n !== n || !body.coords || body.coords.length !== n * 3 || !body.frame) return null;
    return {
      result: { method: "umap", coords: Float32Array.from(body.coords), frame: body.frame },
      trust: body.trust ?? null,
    };
  } catch {
    return null;
  }
}

async function writeCache(
  cloud: Cloud,
  digest: string,
  embedder: string,
  result: ProjectionResult,
  trust: number | null,
) {
  const response = await fetch("/api/x/rag/projection", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      digest,
      embedder,
      space: cloud.space,
      ids: cloud.idsHash,
      params: UMAP_PARAM_KEY,
      n: cloud.n,
      coords: Array.from(result.coords, (v) => Math.round(v * 1e5) / 1e5),
      frame: result.frame,
      trust,
    }),
  });
  if (!response.ok) throw new Error(`cache write refused (${response.status})`);
}

/** The same jobs, inline (no Worker): answers through the listener like the worker does. */
function runInline(job: Job, listener: Listener) {
  setTimeout(() => {
    try {
      if (job.kind === "umap") {
        listener({ id: 0, type: "error", message: "UMAP needs Web Workers, which this browser does not offer." });
      } else if (job.kind === "pca") {
        const result = runPca(job.vectors, job.dim);
        listener({ id: 0, type: "pca", coords: result.coords, frame: result.frame, pca: result.pca! });
        listener({
          id: 0,
          type: "pca-extras",
          clusters: sphericalKMeans(job.vectors, job.dim),
          trust: knnKept(job.vectors, job.dim, result.coords),
        });
      } else {
        listener({
          id: 0,
          type: "strategies",
          ...computeStrategies(job.vectors, job.dim, job.k, job.attempt, job.walk),
        });
      }
    } catch (error) {
      listener({ id: 0, type: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }, 0);
}

export interface StrategyArgs {
  k: number;
  attempt: number;
  walk: "kcenter" | "kcenter_rotate";
}

export function useProjection(
  cloud: Cloud | null,
  method: ProjectionMethod,
  digest: string,
  embedder: string,
  strategyArgs: StrategyArgs | null,
) {
  const [layouts, setLayouts] = useState<Record<string, LayoutEntry>>({});
  const [strategyRuns, setStrategyRuns] = useState<Record<string, StrategiesEntry>>({});
  const worker = useRef<Worker | null>(null);
  const listeners = useRef(new Map<number, Listener>());
  const nextId = useRef(1);
  const started = useRef(new Set<string>());

  // Route leave: stop the worker. Forget what was started, so a remount
  // (StrictMode's double mount included) starts its jobs again.
  useEffect(() => {
    const jobs = listeners.current;
    const begun = started.current;
    return () => {
      worker.current?.terminate();
      worker.current = null;
      jobs.clear();
      begun.clear();
    };
  }, []);

  const run = useCallback((job: Job, listener: Listener) => {
    if (typeof Worker === "undefined") return runInline(job, listener);
    if (!worker.current) {
      worker.current = new Worker(new URL("./projection.worker.ts", import.meta.url), { type: "module" });
      worker.current.onmessage = (event: MessageEvent<WorkerResponse>) => {
        const message = event.data;
        listeners.current.get(message.id)?.(message);
        if (TERMINAL.has(message.type)) listeners.current.delete(message.id);
      };
    }
    const id = nextId.current++;
    listeners.current.set(id, listener);
    // A copy goes to the worker: the cached cloud keeps its buffer.
    worker.current.postMessage({ ...job, id, vectors: job.vectors.slice() });
  }, []);

  const pcaKey = cloud ? `${cloud.key}|pca` : "";
  const umapKey = cloud ? `${cloud.key}|umap` : "";
  const strategyKey =
    cloud && strategyArgs ? `${cloud.key}|${strategyArgs.k}|${strategyArgs.attempt}|${strategyArgs.walk}` : "";

  useEffect(() => {
    if (!cloud || started.current.has(pcaKey)) return;
    started.current.add(pcaKey);
    run({ kind: "pca", vectors: cloud.vectors, dim: cloud.dim }, (message) => {
      setLayouts((prev) => {
        const current = prev[pcaKey];
        if (message.type === "pca")
          return {
            ...prev,
            [pcaKey]: {
              status: "done",
              result: { method: "pca", coords: message.coords, frame: message.frame, pca: message.pca },
            },
          };
        if (message.type === "pca-extras" && current?.status === "done")
          return { ...prev, [pcaKey]: { ...current, clusters: message.clusters, trust: message.trust } };
        if (message.type === "error") return { ...prev, [pcaKey]: { status: "error", message: message.message } };
        return prev;
      });
    });
  }, [cloud, pcaKey, run]);

  useEffect(() => {
    if (!cloud || !strategyArgs || started.current.has(strategyKey)) return;
    started.current.add(strategyKey);
    run({ kind: "strategies", vectors: cloud.vectors, dim: cloud.dim, ...strategyArgs }, (message) => {
      if (message.type === "strategies")
        setStrategyRuns((prev) => ({
          ...prev,
          [strategyKey]: { status: "done", data: { rows: message.rows, walk: message.walk } },
        }));
      else if (message.type === "error")
        setStrategyRuns((prev) => ({ ...prev, [strategyKey]: { status: "error", message: message.message } }));
    });
  }, [cloud, strategyArgs, strategyKey, run]);

  useEffect(() => {
    if (!cloud || method !== "umap" || started.current.has(umapKey)) return;
    started.current.add(umapKey);
    const url = projectionUrl(cloud, digest, embedder);
    void (async () => {
      const cached = await readCache(url, cloud.n);
      if (cached) {
        setLayouts((prev) => ({
          ...prev,
          [umapKey]: { status: "done", result: cached.result, trust: cached.trust, fromCache: true },
        }));
        return;
      }
      let layout: ProjectionResult | null = null;
      run({ kind: "umap", vectors: cloud.vectors, dim: cloud.dim }, (message) => {
        if (message.type === "progress") {
          const share = message.total ? message.done / message.total : 0;
          setLayouts((prev) => ({ ...prev, [umapKey]: { status: "running", progress: share } }));
        } else if (message.type === "umap") {
          layout = { method: "umap", coords: message.coords, frame: message.frame };
          const result = layout;
          setLayouts((prev) => ({ ...prev, [umapKey]: { status: "done", result } }));
        } else if (message.type === "umap-extras" && layout) {
          const result = layout;
          setLayouts((prev) => ({ ...prev, [umapKey]: { status: "done", result, trust: message.trust } }));
          writeCache(cloud, digest, embedder, result, message.trust).catch((error: unknown) => {
            const cacheNote = error instanceof Error ? error.message : String(error);
            setLayouts((prev) => ({ ...prev, [umapKey]: { status: "done", result, trust: message.trust, cacheNote } }));
          });
        } else if (message.type === "error") {
          setLayouts((prev) => ({ ...prev, [umapKey]: { status: "error", message: message.message } }));
        }
      });
    })();
  }, [cloud, method, umapKey, digest, embedder, run]);

  const pca: LayoutEntry | null = cloud ? (layouts[pcaKey] ?? { status: "running", progress: null }) : null;
  const umap: LayoutEntry | null =
    cloud && method === "umap" ? (layouts[umapKey] ?? { status: "running", progress: null }) : null;
  const strategies: StrategiesEntry | null =
    cloud && strategyArgs ? (strategyRuns[strategyKey] ?? { status: "running" }) : null;
  return { pca, umap, active: method === "umap" ? umap : pca, strategies };
}
