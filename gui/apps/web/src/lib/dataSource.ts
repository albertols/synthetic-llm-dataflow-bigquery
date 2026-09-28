/**
 * Where the data comes from. The BFF reports it on `/api/health`
 * (task G0b wires `useDataSource` to that endpoint with TanStack Query);
 * until then the app runs in mock mode.
 */
export type DataSource = { mode: "mock" } | { mode: "bigquery"; project: string };

const MOCK: DataSource = { mode: "mock" };

export function useDataSource(): DataSource {
  return MOCK;
}
