import { Orbit } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";

/** Placeholder until the RAG tab lands (task G3). */
export function RagPage() {
  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="RAG"
        title="The retrieval layer, in 384 dimensions"
        description="How reference rows become GReaT text, 384-d vectors and a FAISS index, and which exemplars each retrieval strategy hands the model."
      />
      <EmptyState
        icon={Orbit}
        title="The embedding explorer is on its way"
        description={
          <ul className="mt-1 grid list-disc gap-1 pl-5 text-left">
            <li>
              A 3-D point cloud of the row documents (PCA or UMAP), with a 2-D fallback when WebGL is unavailable.
            </li>
            <li>The GReaT + embedder lab: a row, its serialisation, its hashed buckets and its cosine neighbours.</li>
            <li>
              The FAISS panel and a retrieval simulator: centroid, k-center and k-center-rotate, exact ports of the
              pipeline.
            </li>
            <li>Free-text pools: targets, stagnation and reuse at scale.</li>
          </ul>
        }
      />
    </div>
  );
}
