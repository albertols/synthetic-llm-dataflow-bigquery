/**
 * The provenance of every synced figure (`/assets/provenance.json`, written
 * by `npm run assets:sync`): source path, SHA-256, size, generating script and
 * owning document. Figures are shown only with it.
 */
import { useQuery } from "@tanstack/react-query";

export type ProvenanceEntry = {
  file: string;
  /** Repo-relative path the figure was copied from. */
  source: string;
  sha256: string;
  bytes: number;
  /** The script (or .drawio source) that generates it; null when the repository has none. */
  generator: string | null;
  /** The document that owns it. */
  documentedIn: string | null;
  attribution?: string;
};

export type ProvenanceFile = { note: string; assets: ProvenanceEntry[] };

export const PROVENANCE_URL = "/assets/provenance.json";

export function useProvenance() {
  return useQuery({
    queryKey: ["intro", "provenance"],
    queryFn: async ({ signal }) => {
      const response = await fetch(PROVENANCE_URL, { signal });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const file = (await response.json()) as ProvenanceFile;
      return new Map(file.assets.map((entry) => [entry.file, entry]));
    },
    staleTime: Infinity,
  });
}

export function assetUrl(file: string): string {
  return `/assets/${file}`;
}
