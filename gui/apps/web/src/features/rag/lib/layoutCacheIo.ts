/**
 * Reading and writing the BFF's UMAP layout cache (the codec is layoutCache.ts,
 * which stays free of browser-only APIs so the BFF's route test can use it).
 */
import type { ProjectionResult } from "./projection";
import { layoutCacheBody, LAYOUT_CACHE_PATH, parseCachedLayout, type LayoutKey } from "./layoutCache";

/**
 * The cached layout, or null. Best effort: any failure is a miss (the page fits
 * UMAP itself), noted on the console in development so a broken cache is visible.
 */
export async function readCache(url: string, n: number) {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    if (!response.ok) {
      devNote(`answered ${response.status}`);
      return null;
    }
    return parseCachedLayout(await response.json(), n);
  } catch (error) {
    devNote(String(error));
    return null;
  }
}

function devNote(reason: string) {
  if (import.meta.env.DEV)
    console.info(`[rag] UMAP layout cache read failed (${reason}); fitting in the browser instead`);
}

export async function writeCache(
  cloud: LayoutKey,
  digest: string,
  embedder: string,
  result: ProjectionResult,
  trust: number | null,
) {
  const response = await fetch(LAYOUT_CACHE_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(layoutCacheBody(cloud, digest, embedder, result, trust)),
  });
  if (!response.ok) throw new Error(`cache write refused (${response.status})`);
}
