/**
 * RAG — the b1_rag layer, from a reference row to the eight seeds a prompt
 * shows: the vector space (3-D explorer), the row-to-vector lab, the FAISS
 * index, the retrieval simulator and the free-text pools. One filter row
 * scopes them all (URL state); each section explains itself one (i) away.
 */
import { ArrowRight } from "lucide-react";

import { Callout } from "@/components/Callout";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes } from "@/lib/format";

import { ExplorerSection } from "./explorer/ExplorerSection";
import { FaissPanel } from "./faiss/FaissPanel";
import { GreatLab } from "./lab/GreatLab";
import { LazyMount } from "./LazyMount";
import { spaceLabel } from "./lib/selection";
import { PoolsPanel } from "./pools/PoolsPanel";
import { RagFilters } from "./RagFilters";
import { RetrievalSimulator } from "./retrieval/RetrievalSimulator";
import { Section } from "./Section";
import { TOP_K, useRagModel } from "./useRagModel";

const STEPS = [
  { id: "explorer", label: "Vector space" },
  { id: "lab", label: "Row → vector" },
  { id: "index", label: "FAISS index" },
  { id: "retrieval", label: `${TOP_K} seeds` },
  { id: "pools", label: "Free-text pools" },
] as const;

export function RagPage() {
  const model = useRagModel();
  const { facets, resolved, set, cloud, cloudQuery } = model;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        eyebrow="RAG"
        title="The retrieval layer, in 384 dimensions"
        concept="rag:overview"
        description="How reference rows become GReaT sentences, 384-d vectors and an exact FAISS index, and which eight real values each seed strategy lets the model see — computed in your browser by exact ports of the pipeline's code."
      />

      {facets.isPending ? (
        <Skeleton className="h-24 w-full" />
      ) : facets.error ? (
        <Callout tone="danger" title="The RAG sets could not be listed">
          {facets.error.message}. Check that the BFF is running, then reload.
        </Callout>
      ) : !resolved || !set ? (
        <EmptyState
          title="No RAG layer yet"
          description="rag_chunks has no sets. Launch a b1_rag run with --build_rag_layer to embed a reference sample, then come back."
        />
      ) : (
        <>
          <RagFilters model={model} />
          {resolved.notices.length ? (
            <Callout tone="info" title="Showing the closest match" live>
              {resolved.notices.join(" ")}
            </Callout>
          ) : null}

          <nav aria-label="On this page" className="-mt-2">
            <ol className="flex flex-wrap items-center gap-x-1 gap-y-2 text-sm">
              {STEPS.map((step, i) => (
                <li key={step.id} className="flex items-center gap-1">
                  <a
                    href={`#${step.id}`}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-text-2 hover:border-control-border hover:text-text-1 focus-visible:outline-2 focus-visible:outline-focus-ring"
                  >
                    <span className="font-mono text-xs text-accent-text">{i + 1}</span>
                    {step.label}
                  </a>
                  {i < STEPS.length - 1 ? <ArrowRight className="size-3.5 text-text-3" aria-hidden="true" /> : null}
                </li>
              ))}
            </ol>
          </nav>

          <Section
            id="explorer"
            step={1}
            title="The vector space"
            concept="rag:embedding-explorer"
            lead={
              <>
                {spaceLabel(resolved.space)} of <span className="font-mono">{set.source_fqn}</span>, embedded with{" "}
                <span className="font-mono">
                  {set.embedder_id}/{set.embedder_version}
                </span>
                {cloud ? `: ${cloud.n.toLocaleString("en-US")} vectors of ${cloud.dim} dimensions` : ""}, projected to
                three. Every distance you read in the panel is computed in the full space, never on the picture.
                {cloudQuery.data?.bytesEstimate ? ` BigQuery scan: ${formatBytes(cloudQuery.data.bytesEstimate)}.` : ""}
              </>
            }
          >
            <ExplorerSection model={model} />
          </Section>

          <Section
            id="lab"
            step={2}
            title="From a row to a vector"
            concept="rag:great"
            lead="One reference row through serialize_row, the tokenizer and the hashing embedder — every step recomputed here by the exact ports — next to what bge-small does with the same sentence."
          >
            <LazyMount label="The row-to-vector lab" minHeight={1500}>
              <GreatLab model={model} />
            </LazyMount>
          </Section>

          <Section
            id="index"
            step={3}
            title="The FAISS index"
            concept="rag:faiss-flatip"
            lead="IndexFlatIP is the vectors, stacked: exact, a fraction of a second to build, asked a handful of questions per engine build, and never saved."
          >
            <LazyMount label="The FAISS index panel" minHeight={900}>
              <FaissPanel model={model} />
            </LazyMount>
          </Section>

          <Section
            id="retrieval"
            step={4}
            title={`Picking ${TOP_K} seeds`}
            concept="rag:seed-strategy"
            lead="There is no user query: retrieval chooses which few real values stand for a whole column. Three strategies ship; two teaching contrasts show what they are not."
          >
            <LazyMount label="The retrieval simulator" minHeight={1700}>
              <RetrievalSimulator model={model} />
            </LazyMount>
          </Section>

          <Section
            id="pools"
            step={5}
            title="Free-text pools"
            concept="rag:pool-target"
            lead="What the LLM ladder produced from those seeds: a bounded, deduplicated pool per column, persisted per (reference digest, model) and drawn from uniformly by every generated row."
          >
            <LazyMount label="The free-text pools" minHeight={1200}>
              <PoolsPanel model={model} />
            </LazyMount>
          </Section>
        </>
      )}
    </div>
  );
}
