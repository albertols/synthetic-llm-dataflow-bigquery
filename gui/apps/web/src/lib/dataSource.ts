/**
 * Where the data comes from, as the BFF reports it on `/api/health`
 * (never BigQuery-backed, so it answers at once). While the first health
 * check is in flight the source is "connecting"; if it fails, "offline".
 */
import { useHealth } from "./api";

export type DataSource =
  | { mode: "mock" }
  | { mode: "bigquery"; project: string; maxBytesBilled?: number }
  | { mode: "connecting" }
  | { mode: "offline" };

export function useDataSource(): DataSource {
  const { data, isError } = useHealth();
  if (isError) return { mode: "offline" };
  if (!data) return { mode: "connecting" };
  const health = data.data;
  return health.mode === "bigquery"
    ? { mode: "bigquery", project: health.project ?? "?", maxBytesBilled: health.max_bytes_billed }
    : { mode: "mock" };
}
