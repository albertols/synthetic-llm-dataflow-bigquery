/**
 * The BFF's UMAP layout cache (`/api/x/rag/projection`, apps/server
 * routes/rag.extra.ts): the query that names a layout, the body that stores
 * one and the reader that checks what comes back. Coordinates travel as
 * base64 of n × 3 little-endian Float32, so the largest layout (3,000 points)
 * is ~48 KB of body, inside the BFF's 64 KB limit.
 */
import { base64ToFloat32, float32ToBase64 } from "./float32Base64";
import { UMAP_PARAMS, type ProjectionResult } from "./projection";

export const LAYOUT_CACHE_PATH = "/api/x/rag/projection";
export const UMAP_PARAM_KEY = `umap-js 1.4.0; nNeighbors=${UMAP_PARAMS.nNeighbors}; minDist=${UMAP_PARAMS.minDist}; seed=${UMAP_PARAMS.seed}`;

/**
 * What names a layout: a `Cloud`'s space, ids hash and point count. Structural, so
 * this file imports nothing of the page (the BFF's route test encodes bodies with it).
 */
export type LayoutKey = { space: string; idsHash: string; n: number };

export function projectionUrl(cloud: LayoutKey, digest: string, embedder: string): string {
  const params = new URLSearchParams({
    digest,
    embedder,
    space: cloud.space,
    ids: cloud.idsHash,
    params: UMAP_PARAM_KEY,
  });
  return `${LAYOUT_CACHE_PATH}?${params.toString()}`;
}

export function layoutCacheBody(
  cloud: LayoutKey,
  digest: string,
  embedder: string,
  result: Pick<ProjectionResult, "coords" | "frame">,
  trust: number | null,
) {
  return {
    digest,
    embedder,
    space: cloud.space,
    ids: cloud.idsHash,
    params: UMAP_PARAM_KEY,
    n: cloud.n,
    coords: float32ToBase64(result.coords),
    frame: result.frame,
    trust,
  };
}

interface CachedLayout {
  hit: boolean;
  n?: number;
  coords?: string;
  frame?: ProjectionResult["frame"];
  trust?: number | null;
}

/** A cache answer as a layout, or null (a miss, or anything that does not hold exactly n × 3 values). */
export function parseCachedLayout(body: unknown, n: number): { result: ProjectionResult; trust: number | null } | null {
  const layout = body as CachedLayout;
  if (!layout?.hit || layout.n !== n || typeof layout.coords !== "string" || !layout.frame) return null;
  const coords = base64ToFloat32(layout.coords);
  if (!coords || coords.length !== n * 3) return null;
  return { result: { method: "umap", coords, frame: layout.frame }, trust: layout.trust ?? null };
}
