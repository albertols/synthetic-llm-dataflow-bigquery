/**
 * The index, as the code builds it: `IndexFlatIP` over L2-normalised vectors
 * (inner product ≡ cosine), n × d × 4 bytes, rebuilt in RAM by every engine
 * build and never persisted; the row index holds only the first 1,024
 * fingerprint-ordered reference rows (MAX_ROW_DOC_ROWS). The norms are
 * checked live on the loaded vectors.
 */
import { ArrowRight, Database, MemoryStick, Search, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import { Formula } from "@/components/Formula";
import { InfoHint } from "@/components/InfoHint";
import { SourceLink } from "@/components/SourceLink";
import { StatTile } from "@/components/StatTile";
import { Badge } from "@/components/ui/badge";
import { formatBytes, formatCount, formatFixed, formatNumber } from "@/lib/format";

import { normRange } from "../lib/vectors";
import { MAX_ROW_DOC_ROWS, MAX_VALUES_PER_COLUMN, REFERENCE_ROWS_LIMIT, TOP_K, type RagModel } from "../useRagModel";

const BYTES_PER_FLOAT = 4;

export function FaissPanel({ model }: { model: RagModel }) {
  const { set, cloud } = model;
  if (!set) return null;
  const dim = set.dim ?? cloud?.dim ?? 384;
  const n = Math.min(set.row_docs, MAX_ROW_DOC_ROWS);
  const capBytes = MAX_ROW_DOC_ROWS * dim * BYTES_PER_FLOAT;
  const norms = cloud ? normRange(cloud.rows) : null;
  const excludedRows = Math.max(REFERENCE_ROWS_LIMIT - MAX_ROW_DOC_ROWS, 0);
  const share = MAX_ROW_DOC_ROWS / REFERENCE_ROWS_LIMIT;
  return (
    <div className="grid gap-5">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="grid content-start gap-3 rounded-lg border border-border bg-surface-1 p-4">
          <div className="flex items-center gap-1">
            <h3 className="text-sm font-semibold text-text-1">Inner product = cosine, on the unit sphere</h3>
            <InfoHint concept="rag:faiss-flatip" />
          </div>
          <Formula
            className="relative"
            tex="\begin{aligned} \langle u, v\rangle &= \lVert u\rVert\,\lVert v\rVert\cos\theta \\ &= \cos\theta \quad \text{when } \lVert u\rVert = \lVert v\rVert = 1 \end{aligned}"
          />
          <Formula
            className="relative"
            tex="\text{search}(q) = \operatorname{top}_k\left(Xq\right),\quad X \in \mathbb{R}^{n \times d}"
          />
          <p className="text-sm text-text-2">
            Every stored vector is L2-normalised, so the inner-product index ranks by cosine with no approximation.
            {norms ? (
              <>
                {" "}
                Checked on the {formatCount(cloud!.n)} vectors loaded above: ‖v‖ runs from{" "}
                <span className="font-mono text-text-1">{formatFixed(norms.min, 5)}</span> to{" "}
                <span className="font-mono text-text-1">{formatFixed(norms.max, 5)}</span> (float32 rounding).
              </>
            ) : null}
          </p>
          <p className="text-xs text-text-3">
            <SourceLink path="packages/sdfb-core/src/sdfb_core/rag/index.py">
              build_index · _FaissFlatIPIndex · _PyExactIPIndex
            </SourceLink>
          </p>
        </div>
        <div className="grid grid-cols-2 content-start gap-3">
          <StatTile
            label="Row index size"
            concept="rag:row-doc-cap"
            value={n}
            format={(v) => formatCount(v)}
            footnote={`vectors × ${dim} dims (cap ${formatCount(MAX_ROW_DOC_ROWS)})`}
          />
          <StatTile
            label="Memory at the cap"
            concept="rag:faiss-memory"
            value={capBytes}
            format={(v) => `${formatNumber(v / 2 ** 20, 2)} MiB`}
            footnote={`${formatCount(MAX_ROW_DOC_ROWS)} × ${dim} × 4 B = ${formatCount(capBytes)} B (${formatBytes(capBytes)})`}
          />
          <StatTile
            label="Work per query"
            value={n * dim}
            format={(v) => formatCount(v)}
            footnote="multiply-adds: one matrix–vector product, then a top-k"
          />
          <StatTile
            label="Persisted"
            value="never"
            footnote="the vectors are (rag_chunks); the index is rebuilt in RAM"
          />
        </div>
      </div>

      <div className="grid gap-2">
        <div className="flex items-center gap-1">
          <h3 className="text-sm font-semibold text-text-1">One engine build, start to finish</h3>
          <InfoHint concept="rag:index-lifecycle" />
        </div>
        <ol className="grid gap-2 md:grid-cols-[repeat(4,minmax(0,1fr))]" aria-label="Index lifecycle">
          <Step icon={<Database aria-hidden="true" />} title="rag_chunks (BigQuery)" badge="persisted">
            One row per chunk, keyed by reference digest + embedder id/version. The engine reads the 1,024 row vectors
            back all-or-nothing, dimension-checked (<span className="font-mono">b1_chunks_reused</span>), or embeds them
            itself.
          </Step>
          <Step icon={<MemoryStick aria-hidden="true" />} title="IndexFlatIP (RAM)" badge="rebuilt">
            The vectors, stacked: n × {dim} float32. No training, no graph, nothing to tune; single-threaded search (
            <span className="font-mono">omp_set_num_threads(1)</span>) so answers reproduce.
          </Step>
          <Step icon={<Search aria-hidden="true" />} title={`One centroid question → ${TOP_K}`} badge="exact">
            The query is the mean of the vectors (a point inside the sphere); its {TOP_K} nearest are the most typical
            items. No user query exists here.
          </Step>
          <Step icon={<Trash2 aria-hidden="true" />} title="Released" badge="never saved">
            Nothing of the index outlives the build. The next engine build stacks the vectors again — a fraction of a
            second at 1,024 × {dim}.
          </Step>
        </ol>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="grid content-start gap-2 rounded-lg border border-border bg-surface-1 p-4">
          <h3 className="text-sm font-semibold text-text-1">Two indexes, one of them live</h3>
          <div className="relative min-w-0 overflow-x-auto" role="region" aria-label="The two indexes" tabIndex={0}>
            <table className="w-full min-w-[28rem] text-left text-xs">
              <thead className="text-text-3">
                <tr>
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Index
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Holds
                  </th>
                  <th scope="col" className="py-1.5 font-medium">
                    Its answer
                  </th>
                </tr>
              </thead>
              <tbody className="text-text-2">
                <tr className="border-t border-border align-top">
                  <th scope="row" className="py-2 pr-3 font-medium text-text-1">
                    Column index
                    <Badge variant="cpu" className="ml-1.5">
                      live
                    </Badge>
                  </th>
                  <td className="py-2 pr-3">
                    One free-text column&apos;s distinct value vectors (≤ {formatCount(MAX_VALUES_PER_COLUMN)})
                  </td>
                  <td className="py-2">
                    The {TOP_K} seeds the LLM sees, under <span className="font-mono">centroid</span>, for a column with
                    more than {TOP_K} distinct values. k-center needs distances, not an index.
                  </td>
                </tr>
                <tr className="border-t border-border align-top">
                  <th scope="row" className="py-2 pr-3 font-medium text-text-1">
                    Row index
                    <Badge variant="outline" className="ml-1.5">
                      safety net
                    </Badge>
                  </th>
                  <td className="py-2 pr-3">The first {formatCount(MAX_ROW_DOC_ROWS)} row documents</td>
                  <td className="py-2">
                    {TOP_K} exemplar rows: a fallback seed source (rung 3) that cannot fire as the engine is wired — the
                    same 1,024-row prefix already fed the column path.
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="grid content-start gap-3 rounded-lg border border-border bg-surface-1 p-4">
          <div className="flex items-center gap-1">
            <h3 className="text-sm font-semibold text-text-1">Why 1,024 row documents</h3>
            <InfoHint concept="rag:row-doc-cap" />
          </div>
          <SampleStrip share={share} />
          <p className="text-sm text-text-2">
            The only reader of row vectors reads exactly the first {formatCount(MAX_ROW_DOC_ROWS)} rows of the
            fingerprint-ordered reference sample, all-or-nothing, so the writer embeds exactly those (one constant for
            both, ADR 0019). A fingerprint-ordered prefix is a uniform sample of the sample.
          </p>
          <ul className="grid gap-1 text-xs text-text-2">
            <li>
              <strong className="text-text-1">Not row documents:</strong> rows {formatCount(MAX_ROW_DOC_ROWS + 1)}–
              {formatCount(REFERENCE_ROWS_LIMIT)} of a {formatCount(REFERENCE_ROWS_LIMIT)}-row sample (
              {formatCount(excludedRows)} rows, at the default <span className="font-mono">--reference_rows_limit</span>
              ).
            </li>
            <li>
              <strong className="text-text-1">Still embedded:</strong> their free-text values — value chunks are the
              distinct values of the whole sample, up to {formatCount(MAX_VALUES_PER_COLUMN)} per column (this set:{" "}
              {formatCount(set.value_chunks)} across {set.columns.length || "no"} column
              {set.columns.length === 1 ? "" : "s"}).
            </li>
            <li>
              <strong className="text-text-1">Never value chunks:</strong> repeats of a value, values past the 1,024th
              distinct one, identifier-shaped and non-free-text columns.
            </li>
          </ul>
          <p className="text-xs text-text-3">
            <SourceLink path="packages/sdfb-core/src/sdfb_core/rag/chunking.py" line={52}>
              chunking.py — MAX_ROW_DOC_ROWS
            </SourceLink>
          </p>
        </div>
      </div>
    </div>
  );
}

function Step({
  icon,
  title,
  badge,
  children,
}: {
  icon: ReactNode;
  title: string;
  badge: string;
  children: ReactNode;
}) {
  return (
    <li className="relative grid content-start gap-1.5 rounded-lg border border-border bg-surface-1 p-3 text-xs text-text-2 [&_svg]:size-4">
      <div className="flex items-center gap-2 text-sm font-medium text-text-1">
        <span className="text-accent-text">{icon}</span>
        {title}
      </div>
      <Badge variant="outline" className="justify-self-start">
        {badge}
      </Badge>
      <p className="leading-relaxed">{children}</p>
      <ArrowRight
        aria-hidden="true"
        className="absolute top-1/2 -right-2.5 hidden size-4 -translate-y-1/2 text-text-3 md:block [li:last-child>&]:hidden"
      />
    </li>
  );
}

/** The reference sample as a bar: the indexed prefix in accent, the rest muted. */
function SampleStrip({ share }: { share: number }) {
  const pct = Math.max(0, Math.min(share, 1)) * 100;
  return (
    <figure className="grid gap-1">
      <div
        role="img"
        aria-label={`The first ${formatCount(MAX_ROW_DOC_ROWS)} of ${formatCount(REFERENCE_ROWS_LIMIT)} sample rows (${pct.toFixed(1)} %) become row documents`}
        className="flex h-5 overflow-hidden rounded-sm border border-border"
      >
        <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
        <div className="h-full flex-1 bg-surface-3" />
      </div>
      <figcaption className="flex justify-between text-xs text-text-3 tabular-nums">
        <span>row 1</span>
        <span>
          {formatCount(MAX_ROW_DOC_ROWS)} indexed ({pct.toFixed(1)} %)
        </span>
        <span>{formatCount(REFERENCE_ROWS_LIMIT)}</span>
      </figcaption>
    </figure>
  );
}
