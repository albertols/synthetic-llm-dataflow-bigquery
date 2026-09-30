/** Messages between the page and projection.worker.ts. A job may answer twice: first the layout, then its extras. */
import type { Frame, PcaModel } from "./projection";
import type { StrategyJobResult } from "./strategyJob";

export type WorkerRequest =
  | { id: number; kind: "pca"; vectors: Float32Array; dim: number }
  | { id: number; kind: "umap"; vectors: Float32Array; dim: number }
  | {
      id: number;
      kind: "strategies";
      vectors: Float32Array;
      dim: number;
      k: number;
      attempt: number;
      walk: "kcenter" | "kcenter_rotate";
    };

export type WorkerResponse =
  | { id: number; type: "progress"; done: number; total: number }
  | { id: number; type: "pca"; coords: Float32Array; frame: Frame; pca: PcaModel }
  | { id: number; type: "pca-extras"; clusters: Int32Array; trust: number | null }
  | { id: number; type: "umap"; coords: Float32Array; frame: Frame }
  | { id: number; type: "umap-extras"; trust: number | null }
  | ({ id: number; type: "strategies" } & StrategyJobResult)
  | { id: number; type: "error"; message: string };
