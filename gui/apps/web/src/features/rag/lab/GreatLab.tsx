/**
 * The GReaT + embedder lab: one reference row, all the way to a vector.
 *
 *   row (its clauses, editable) → serialize_row → "col is value, …"
 *     → str.split() tokens → SHA-256 → signed bucket per token
 *     → 16 × 24 grid of the 384 dims → cosine to the table's other rows
 *
 * Every step runs the exact TS ports (serializeGreat, hashingTokens,
 * hashingBucket, hashingEmbed) in the browser. Next to it, bge-small: its
 * stored vector for the same row when the sample was embedded with bge
 * (matched by row_digest), and what the code does differently from the model
 * card (mean pooling, the 512-token limit).
 */
import type { EChartsOption } from "echarts";
import { RotateCcw } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";

import type { ChunkMeta, RagSet } from "@contracts/api";
import { knobs } from "@contracts/generated/knobs";
import {
  expectedCollisions,
  hashingBucket,
  hashingEmbed,
  hashingTokens,
  serializeGreat,
} from "@synthetic-platform/stats";

import { Callout } from "@/components/Callout";
import { ChartFrame } from "@/components/ChartFrame";
import { DataTableFallback } from "@/components/DataTableFallback";
import { InfoHint } from "@/components/InfoHint";
import { SourceLink } from "@/components/SourceLink";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useRagChunks } from "@/lib/api";
import { formatCount, formatFixed, formatNumber } from "@/lib/format";
import { readToken, useTheme } from "@/lib/theme";

import { learnColumns, parseGreat } from "../lib/greatParse";
import { hashingNoise } from "../lib/metrics";
import { embedderParam } from "../lib/selection";
import { cosineWith, dot, maxAbsDiff, norm, rowsOf } from "../lib/vectors";
import { MAX_ROW_DOC_ROWS, type RagModel } from "../useRagModel";
import { BucketHeatmap } from "./BucketHeatmap";

const BGE_MAX_TOKENS = knobs.knobs.find((k) => k.id === "bge_max_length")?.value ?? 512;
const POOLING = knobs.annotations.find((a) => a.id === "bge-pooling");

function useRowDocs(set: RagSet | null) {
  return useRagChunks(
    set
      ? {
          digest: set.reference_digest,
          kind: "row_doc",
          embedder: embedderParam(set),
          source_fqn: set.source_fqn,
          limit: MAX_ROW_DOC_ROWS,
        }
      : undefined,
  );
}

export function GreatLab({ model }: { model: RagModel }) {
  const { set, sets } = model;
  const rowDocs = useRowDocs(set);
  const bgeSet = useMemo(
    () =>
      set
        ? (sets.find(
            (s) =>
              s.reference_digest === set.reference_digest &&
              s.source_fqn === set.source_fqn &&
              s.embedder_id !== "hashing-384" &&
              s.row_docs > 0,
          ) ?? null)
        : null,
    [set, sets],
  );
  const bgeDocs = useRowDocs(bgeSet && bgeSet !== set ? bgeSet : null);
  const [rowIndex, setRowIndex] = useState(0);
  const [rowDraft, setRowDraft] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [hoverDim, setHoverDim] = useState<number | null>(null);
  const [hoverToken, setHoverToken] = useState<number | null>(null);
  const [showDims, setShowDims] = useState(false);

  const docs = rowDocs.data?.data;
  const texts = useMemo(() => docs?.meta.map((m) => m.chunk_text) ?? [], [docs]);
  const columns = useMemo(() => learnColumns(texts), [texts]);
  const index = Math.min(rowIndex, Math.max(texts.length - 1, 0));
  const meta: ChunkMeta | undefined = docs?.meta[index];
  const clauses = useMemo(() => (meta ? parseGreat(meta.chunk_text, columns) : []), [meta, columns]);
  const edited = Object.keys(edits).length > 0;
  const order = clauses.map((c) => c.column);
  const row = Object.fromEntries(clauses.map((c) => [c.column, edits[c.column] ?? c.value]));
  // Values are the parsed strings, which str() renders unchanged: the stored sentence comes back byte for byte.
  const sentence = meta ? serializeGreat(row, order) : "";
  const tokens = useMemo(() => hashingTokens(sentence), [sentence]);
  const buckets = useMemo(() => tokens.map((t) => hashingBucket(t, 384, 0)), [tokens]);
  const vector = useMemo(() => hashingEmbed(sentence), [sentence]);
  const dim = docs?.dim ?? 384;

  // Every row doc re-embedded here (hashing-384), for the cosine step and the parity check.
  const deferredTexts = useDeferredValue(texts);
  const allHashing = useMemo(() => {
    const out = new Float32Array(deferredTexts.length * 384);
    deferredTexts.forEach((t, i) => out.set(hashingEmbed(t), i * 384));
    return rowsOf(out, 384);
  }, [deferredTexts]);
  const parity = useMemo(() => {
    if (!docs || !set || set.embedder_id !== "hashing-384" || docs.dim !== 384 || !meta) return null;
    const stored = docs.vectors.subarray(index * 384, (index + 1) * 384);
    return maxAbsDiff(stored, hashingEmbed(meta.chunk_text));
  }, [docs, set, meta, index]);

  const cosRows = useMemo(() => {
    // hashingEmbed returns unit vectors (or e_0): the inner product is the cosine.
    return allHashing
      .map((r, i) => ({ i, cos: i === index ? Number.NaN : dot(vector, r) }))
      .filter((c) => Number.isFinite(c.cos));
  }, [allHashing, vector, index]);

  const bgeVector = useMemo(() => {
    const source = set && set.embedder_id !== "hashing-384" ? docs : bgeDocs.data?.data;
    if (!source || !meta) return null;
    const at = source.meta.findIndex((m) => m.row_digest === meta.row_digest);
    if (at < 0) return null;
    return {
      vector: source.vectors.subarray(at * source.dim, (at + 1) * source.dim),
      rows: rowsOf(source.vectors, source.dim),
      at,
    };
  }, [set, docs, bgeDocs.data, meta]);
  const bgeCos = useMemo(() => {
    if (!bgeVector) return [];
    const qn = norm(bgeVector.vector);
    return bgeVector.rows
      .map((r, i) => ({ i, cos: i === bgeVector.at ? Number.NaN : cosineWith(bgeVector.vector, qn, r, norm(r)) }))
      .filter((c) => Number.isFinite(c.cos));
  }, [bgeVector]);

  if (!set) return null;
  if (rowDocs.isPending) return <Skeleton className="h-96 w-full" />;
  if (!docs?.meta.length || !meta) {
    return (
      <Callout tone="info" title="No row documents in this set">
        The lab needs the sample&apos;s row documents; pick another reference sample.
      </Callout>
    );
  }

  const tokenBuckets = new Map<number, string[]>();
  tokens.forEach((t, i) => {
    const b = buckets[i]!.bucket;
    tokenBuckets.set(b, [...(tokenBuckets.get(b) ?? []), `${buckets[i]!.sign > 0 ? "+" : "−"}${t}`]);
  });
  const highlight = new Set<number>();
  if (hoverToken !== null && buckets[hoverToken]) highlight.add(buckets[hoverToken].bucket);
  if (hoverDim !== null) highlight.add(hoverDim);
  const collided = [...tokenBuckets.values()].filter(
    (list) => list.length > 1 && new Set(list.map((s) => s.slice(1))).size > 1,
  ).length;
  const nonZero = Array.from(vector).filter((v) => v !== 0).length;
  const noise = hashingNoise(384);
  const storedMatches = !edited && sentence === meta.chunk_text;

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-xs font-medium text-text-2">
          Row document (of {formatCount(docs.meta.length)})
          <input
            type="number"
            min={1}
            max={docs.meta.length}
            value={rowDraft ?? String(index + 1)}
            onChange={(event) => {
              // Keep what is typed; switch rows only on a valid row number (an empty field waits).
              const text = event.target.value;
              setRowDraft(text);
              const next = Number(text);
              if (text !== "" && Number.isInteger(next) && next >= 1 && next <= docs.meta.length) {
                setRowIndex(next - 1);
                setEdits({});
              }
            }}
            onBlur={() => setRowDraft(null)}
            className="h-9 w-28 rounded-md border border-control-border bg-surface-1 px-3 text-sm text-text-1 tabular-nums focus-visible:outline-2 focus-visible:outline-focus-ring"
          />
        </label>
        <Button size="sm" variant="ghost" onClick={() => setEdits({})} disabled={!edited}>
          <RotateCcw aria-hidden="true" />
          Undo my edits
        </Button>
        <p className="text-xs text-text-3">
          Edit any value: the sentence, its tokens, its buckets and its cosines follow, computed here by the exact
          ports.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="grid content-start gap-2 rounded-lg border border-border bg-surface-1 p-4">
          <div className="flex items-center gap-1">
            <h3 className="text-sm font-semibold text-text-1">1 · The row</h3>
            <InfoHint concept="rag:row-parse" />
          </div>
          <div
            className="grid max-h-80 gap-1.5 overflow-y-auto pr-1"
            role="region"
            aria-label="Row values"
            tabIndex={0}
          >
            {clauses.map((c) => (
              <label
                key={c.column}
                className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-2 text-xs"
              >
                <span className="truncate font-mono text-text-2">{c.column}</span>
                <input
                  value={edits[c.column] ?? c.value}
                  onChange={(event) => setEdits((prev) => ({ ...prev, [c.column]: event.target.value }))}
                  className="h-8 min-w-0 rounded-sm border border-control-border bg-surface-1 px-2 font-mono text-text-1 focus-visible:outline-2 focus-visible:outline-focus-ring"
                />
              </label>
            ))}
          </div>
        </div>

        <div className="grid content-start gap-4">
          <div className="grid gap-2 rounded-lg border border-border bg-surface-1 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-text-1">2 · The GReaT sentence</h3>
              <InfoHint concept="rag:great" />
              {storedMatches ? (
                <Badge variant="cpu">identical to the stored chunk_text</Badge>
              ) : (
                <Badge variant="accent">edited — differs from the stored text</Badge>
              )}
            </div>
            <p className="font-mono text-xs leading-relaxed break-words text-text-1" data-testid="great-sentence">
              {clauses.map((c, i) => (
                <span key={c.column}>
                  {i > 0 ? <span className="text-text-3">, </span> : null}
                  <span className="text-text-2">{c.column}</span>
                  <span className="text-text-3"> is </span>
                  <span className="text-text-1">{edits[c.column] ?? c.value}</span>
                </span>
              ))}
            </p>
            <p className="text-xs text-text-3">
              {formatCount(sentence.length)} characters · schema order, never shuffled (GReaT shuffles to train; the
              pipeline embeds, and a shuffled sentence would be another vector) · a missing value is written “null”.
            </p>
          </div>

          <div className="grid gap-2 rounded-lg border border-border bg-surface-1 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-text-1">3 · Tokens → signed buckets</h3>
              <InfoHint concept="rag:hashing-embedder" />
              <span className="text-xs text-text-3">
                {tokens.length} tokens → {nonZero} non-zero dims · {collided} bucket{collided === 1 ? "" : "s"} shared
                by different tokens (≈ {formatNumber(expectedCollisions(new Set(tokens).size, 384), 2)} expected)
              </span>
            </div>
            <ul className="flex max-h-44 flex-wrap gap-1 overflow-y-auto" aria-label="Tokens and their buckets">
              {tokens.map((t, i) => (
                <li key={`${t}-${i}`}>
                  <button
                    type="button"
                    onPointerEnter={() => setHoverToken(i)}
                    onPointerLeave={() => setHoverToken(null)}
                    onFocus={() => setHoverToken(i)}
                    onBlur={() => setHoverToken(null)}
                    className="inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-text-1 hover:border-control-border focus-visible:outline-2 focus-visible:outline-focus-ring"
                    aria-label={`Token ${t}: bucket ${buckets[i]!.bucket}, sign ${buckets[i]!.sign > 0 ? "plus" : "minus"}`}
                  >
                    {t}
                    <span className="text-text-3">
                      → {buckets[i]!.sign > 0 ? "+" : "−"}
                      {buckets[i]!.bucket}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="text-xs text-text-3">
              Python&apos;s <code className="font-mono">str.split()</code>: punctuation stays on its word (“Pine,” and
              “Pine” are different tokens). Bucket = first 8 bytes of SHA-256(“0\x00” + token) as a uint64, mod 384;
              sign = parity of byte 8.
            </p>
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="grid content-start gap-3 rounded-lg border border-border bg-surface-1 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-text-1">4a · hashing-384 (computed here)</h3>
            <Badge variant="outline">lexical</Badge>
            {parity !== null ? (
              <Badge variant={parity <= 1e-6 ? "cpu" : "accent"}>
                {parity <= 1e-6 ? "matches the stored vector" : "differs from the stored vector"} · max |Δ|{" "}
                {parity === 0 ? "0" : parity.toExponential(1)}
              </Badge>
            ) : null}
          </div>
          <BucketHeatmap
            vector={vector}
            label="The hashing-384 vector of this row"
            highlight={highlight}
            onHoverCell={setHoverDim}
          />
          <p className="min-h-5 text-xs text-text-2" aria-live="polite">
            {hoverDim !== null
              ? `dim ${hoverDim}: ${formatFixed(vector[hoverDim], 3)}${tokenBuckets.get(hoverDim) ? ` ← ${tokenBuckets.get(hoverDim)!.join(", ")}` : " (no token)"}`
              : hoverToken !== null && buckets[hoverToken]
                ? `“${tokens[hoverToken]}” → dim ${buckets[hoverToken].bucket} (${buckets[hoverToken].sign > 0 ? "+" : "−"}1 before the L2 division)`
                : "Point at a token or a cell."}
          </p>
          <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => setShowDims((on) => !on)}>
            {showDims ? "Hide the non-zero dims" : "View the non-zero dims"}
          </Button>
          {showDims ? (
            <DataTableFallback
              caption="Non-zero dimensions of the hashing vector"
              rows={Array.from(vector)
                .map((v, d) => ({ dim: d, value: formatFixed(v, 4), tokens: tokenBuckets.get(d)?.join(" ") ?? "" }))
                .filter((r) => r.value !== formatFixed(0, 4))}
            />
          ) : null}
        </div>

        <div className="grid content-start gap-3 rounded-lg border border-border bg-surface-1 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-text-1">4b · bge-small-en-v1.5 (stored)</h3>
            <InfoHint concept="rag:bge" />
            <Badge variant="outline">semantic</Badge>
          </div>
          {bgeVector ? (
            <BucketHeatmap vector={bgeVector.vector} label="The stored bge-small vector of this row" />
          ) : (
            <p className="rounded-md border border-dashed border-border px-3 py-4 text-xs text-text-3">
              {bgeDocs.isFetching
                ? "Loading the bge vectors…"
                : "This sample has no bge vectors for this row (only a hashing-384 set). The model cannot run in the browser, so there is nothing to compute here."}
            </p>
          )}
          <p className="text-xs text-text-2">
            WordPiece tokens → 12-layer encoder → pooled → L2-normalised: every one of the 384 dims carries signal, and
            nearness means meaning, not shared tokens. Input is cut at {formatCount(Number(BGE_MAX_TOKENS))} tokens;
            this sentence has {formatCount(tokens.length)} whitespace tokens (WordPiece yields more).
          </p>
          {POOLING ? (
            <Callout tone="docs-differ" title={POOLING.title}>
              <p>
                <strong className="text-text-1">Docs:</strong> {POOLING.docs_say}
              </p>
              <p>
                <strong className="text-text-1">Code:</strong> {POOLING.code_does}
              </p>
              <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {POOLING.evidence
                  .filter((e) => e.role === "code")
                  .map((e) => (
                    <SourceLink key={e.source} source={e.source} />
                  ))}
              </p>
            </Callout>
          ) : null}
        </div>
      </div>

      <CosineCharts
        hashing={cosRows}
        bge={bgeCos}
        noise={noise}
        texts={texts}
        onPick={(i) => {
          setRowIndex(i);
          setEdits({});
        }}
      />
      <p className="text-xs text-text-3">
        Two unrelated texts hashed into {dim} buckets sit at cosine ≈ 0 ± {formatFixed(noise, 3)} (1/√{dim}). Row
        documents of one table are not unrelated: they share every column name and every “is”, so their hashing cosines
        start well above that band — the schema, not the values, sets the floor.
      </p>
    </div>
  );
}

const BIN = 0.05;

function histogram(values: readonly number[]) {
  const counts = new Map<number, number>();
  for (const v of values) {
    const b = Math.floor(v / BIN);
    counts.set(b, (counts.get(b) ?? 0) + 1);
  }
  const keys = [...counts.keys()].sort((a, b) => a - b);
  if (!keys.length) return [];
  const out: { bin: string; from: number; rows: number }[] = [];
  for (let b = Math.min(keys[0]!, -2); b <= Math.max(keys[keys.length - 1]!, 1); b += 1)
    out.push({ bin: formatFixed(b * BIN, 2), from: b * BIN, rows: counts.get(b) ?? 0 });
  return out;
}

function cosineOption(noise: number, band: boolean): EChartsOption {
  const ink = readToken("--text-3", "#939aa7");
  return {
    grid: { left: 44, right: 12, top: 18, bottom: 36 },
    xAxis: { type: "category", name: "cosine", nameLocation: "middle", nameGap: 24, axisLabel: { interval: 3 } },
    yAxis: { type: "value", name: "rows", minInterval: 1 },
    tooltip: { trigger: "axis" },
    series: [
      {
        type: "bar",
        encode: { x: "bin", y: "rows" },
        barMaxWidth: 24,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        ...(band
          ? {
              markArea: {
                silent: true,
                itemStyle: { color: ink, opacity: 0.12 },
                label: { show: true, position: "insideTop" as const, color: ink, fontSize: 10, formatter: "±1/√384" },
                data: [
                  [
                    { xAxis: formatFixed(-Math.ceil(noise / BIN) * BIN, 2) },
                    { xAxis: formatFixed(Math.floor(noise / BIN) * BIN, 2) },
                  ],
                ],
              },
            }
          : {}),
      },
    ],
  };
}

function CosineCharts({
  hashing,
  bge,
  noise,
  texts,
  onPick,
}: {
  hashing: readonly { i: number; cos: number }[];
  bge: readonly { i: number; cos: number }[];
  noise: number;
  texts: readonly string[];
  onPick: (index: number) => void;
}) {
  const { resolved } = useTheme();
  const hashData = useMemo(() => histogram(hashing.map((c) => c.cos)), [hashing]);
  const bgeData = useMemo(() => histogram(bge.map((c) => c.cos)), [bge]);
  const option = useMemo(() => {
    void resolved;
    return cosineOption(noise, true);
  }, [noise, resolved]);
  const bgeOption = useMemo(() => {
    void resolved;
    return cosineOption(noise, false);
  }, [noise, resolved]);
  const top = [...hashing].sort((a, b) => b.cos - a.cos).slice(0, 3);
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <ChartFrame
        title="5a · Cosine to the other rows (hashing-384)"
        concept="rag:hashing-noise"
        description={`${hashing.length} rows, bins of ${BIN}; shaded: the ±${formatFixed(noise, 3)} band of unrelated texts`}
        option={option}
        data={hashData}
        columns={[
          { key: "bin", label: "cosine from" },
          { key: "rows", label: "rows", align: "right" },
        ]}
        footer={
          top.length ? (
            <div className="grid gap-1 text-xs">
              <p className="text-text-3">Nearest rows (click to load one):</p>
              <ol className="grid gap-0.5">
                {top.map((c) => (
                  <li key={c.i}>
                    <button
                      type="button"
                      onClick={() => onPick(c.i)}
                      aria-label={`Load row ${c.i + 1} (cosine ${formatFixed(c.cos, 3)})`}
                      className="flex min-h-7 w-full items-baseline gap-2 rounded-sm px-1 py-1 text-left hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-focus-ring"
                    >
                      <span className="w-12 shrink-0 font-mono text-text-1 tabular-nums">{formatFixed(c.cos, 3)}</span>
                      <span className="line-clamp-1 min-w-0 text-text-2">{texts[c.i]}</span>
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          ) : null
        }
      />
      <ChartFrame
        title="5b · Cosine to the other rows (bge-small, stored)"
        concept="rag:cosine"
        description={bge.length ? `${bge.length} rows, bins of ${BIN}` : "No bge vectors for this sample"}
        option={bgeOption}
        data={bgeData}
        empty={{ when: bge.length === 0, message: "No bge vectors for this sample." }}
        columns={[
          { key: "bin", label: "cosine from" },
          { key: "rows", label: "rows", align: "right" },
        ]}
      />
    </div>
  );
}
