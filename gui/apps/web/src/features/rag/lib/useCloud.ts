/**
 * The vectors of one space, fetched as binary envelopes (`/api/rag/chunks`,
 * Float32) and stacked into one buffer with row views, norms and a hash of
 * the chunk ids in plot order (the projection cache's guard).
 *
 * The stacked cloud is itself a cached query (keyed by the chunk queries), so
 * its identity is stable across renders and a projection never re-runs for
 * the same points; the parts share their cache entries with `useRagChunks`.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import type { ChunkMeta, RagSet } from "@contracts/api";
import { sha256Hex } from "@synthetic-platform/stats";

import { api, queryKeys } from "@/lib/api";

import { spaceQueries, tableName, type SpaceId } from "./selection";
import { norms, rowsOf } from "./vectors";

export interface Cloud {
  /** Identity of the plotted set (space + chunk queries). */
  key: string;
  space: SpaceId;
  meta: ChunkMeta[];
  /** Per point: the table its chunk came from (short name). */
  tables: string[];
  vectors: Float32Array;
  rows: Float32Array[];
  norms: Float64Array;
  dim: number;
  n: number;
  /** SHA-256 of the chunk ids in plot order. */
  idsHash: string;
}

export interface CloudPart {
  meta: ChunkMeta[];
  vectors: Float32Array;
  dim: number;
}

export function buildCloud(key: string, space: SpaceId, parts: readonly CloudPart[]): Cloud {
  const dims = new Set(parts.filter((p) => p.meta.length).map((p) => p.dim));
  if (dims.size > 1) throw new Error(`the chunks mix vector spaces (${[...dims].join(" vs ")} dimensions)`);
  const dim = [...dims][0] ?? parts[0]?.dim ?? 0;
  const meta = parts.flatMap((p) => p.meta);
  const vectors = new Float32Array(meta.length * dim);
  let offset = 0;
  for (const part of parts) {
    vectors.set(part.vectors.subarray(0, part.meta.length * dim), offset);
    offset += part.meta.length * dim;
  }
  const rows = rowsOf(vectors, dim) as Float32Array[];
  return {
    key,
    space,
    meta,
    tables: meta.map((m) => tableName(m.source_fqn)),
    vectors,
    rows,
    norms: norms(rows),
    dim,
    n: meta.length,
    idsHash: sha256Hex(meta.map((m) => m.chunk_id).join("\n")),
  };
}

export interface CloudData {
  cloud: Cloud;
  bytesEstimate: number | null;
  dataSource: "mock" | "bigquery" | null;
}

export function useCloud(space: SpaceId | null, set: RagSet | null, sets: readonly RagSet[]) {
  const queryClient = useQueryClient();
  const queries = useMemo(() => (space && set ? spaceQueries(space, set, sets) : []), [space, set, sets]);
  return useQuery({
    queryKey: ["rag", "cloud", space, queries] as const,
    enabled: !!space && queries.length > 0,
    staleTime: 30 * 60_000,
    structuralSharing: false,
    queryFn: async (): Promise<CloudData> => {
      const parts = await Promise.all(
        queries.map((q) =>
          queryClient.fetchQuery({
            queryKey: queryKeys.ragChunks(q),
            queryFn: ({ signal }) => api.ragChunks(q, signal),
            staleTime: 30 * 60_000,
          }),
        ),
      );
      const bytes = parts.map((p) => p.bytesEstimate).filter((b): b is number => typeof b === "number");
      return {
        cloud: buildCloud(
          JSON.stringify([space, queries]),
          space!,
          parts.map((p) => p.data),
        ),
        bytesEstimate: bytes.length ? bytes.reduce((a, b) => a + b, 0) : null,
        dataSource: parts[0]?.dataSource ?? null,
      };
    },
  });
}
