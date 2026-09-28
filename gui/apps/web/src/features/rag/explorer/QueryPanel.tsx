/**
 * The query box. On a hashing-384 set the text is embedded in the browser by
 * the exact TS port of HashingEmbedder and searched by cosine (the exact
 * index's order), then drawn in the cloud with lines to its top-k. bge-small
 * cannot run in the browser, so on a bge set the box searches chunk text
 * instead — and says so.
 */
import { Search, X } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { InfoHint } from "@/components/InfoHint";
import { Button } from "@/components/ui/button";
import { formatFixed } from "@/lib/format";
import { hashingEmbed, hashingTokens } from "@synthetic-platform/stats";

import type { Cloud } from "../lib/useCloud";
import { topKByCosine, type Hit } from "../lib/vectors";

export interface QueryState {
  text: string;
  /** The embedded query (hashing mode), null for a text search. */
  vector: Float64Array | null;
  tokens: number;
  hits: Hit[];
  /** Text search: how many chunks matched in all. */
  matches?: number;
}

export function runQuery(cloud: Cloud, text: string, k: number, canEmbed: boolean): QueryState {
  const trimmed = text.trim();
  if (canEmbed) {
    const vector = hashingEmbed(trimmed, { dim: cloud.dim });
    return {
      text: trimmed,
      vector,
      tokens: hashingTokens(trimmed).length,
      hits: topKByCosine(vector, cloud.rows, k, { rowNorms: cloud.norms }),
    };
  }
  const needle = trimmed.toLowerCase();
  const found: Hit[] = [];
  cloud.meta.forEach((m, index) => {
    if (m.chunk_text.toLowerCase().includes(needle)) found.push({ index, score: Number.NaN });
  });
  return { text: trimmed, vector: null, tokens: 0, hits: found.slice(0, k), matches: found.length };
}

export function QueryPanel({
  cloud,
  canEmbed,
  embedderLabel,
  k,
  query,
  onQuery,
  onSelect,
  selected,
}: {
  cloud: Cloud;
  canEmbed: boolean;
  embedderLabel: string;
  k: number;
  query: QueryState | null;
  onQuery: (query: QueryState | null) => void;
  onSelect: (index: number) => void;
  selected: number | null;
}) {
  const id = useId();
  const [text, setText] = useState(query?.text ?? "");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return onQuery(null);
    onQuery(runQuery(cloud, text, k, canEmbed));
  };
  const suggestion = suggest(cloud);
  return (
    <div className="grid gap-2">
      <form onSubmit={submit} className="grid gap-2" role="search" aria-labelledby={`${id}-label`}>
        <div className="flex items-center gap-1">
          <label id={`${id}-label`} htmlFor={`${id}-input`} className="text-sm font-medium text-text-1">
            {canEmbed ? "Query the space" : "Search the chunk text"}
          </label>
          <InfoHint concept="rag:query-box" />
        </div>
        <div className="flex gap-2">
          <input
            id={`${id}-input`}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={canEmbed ? "e.g. city is Pine" : "text to find"}
            className="h-9 min-w-0 flex-1 rounded-md border border-control-border bg-surface-1 px-3 text-sm text-text-1 placeholder:text-text-3 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring"
            autoComplete="off"
            spellCheck={false}
          />
          <Button type="submit" size="md" variant="primary">
            <Search aria-hidden="true" />
            {canEmbed ? "Embed" : "Find"}
          </Button>
          {query ? (
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label="Clear the query"
              onClick={() => {
                setText("");
                onQuery(null);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          ) : null}
        </div>
        <p className="text-xs text-text-3">
          {canEmbed ? (
            <>
              Embedded here by the exact port of HashingEmbedder ({cloud.dim} signed buckets), then ranked by cosine
              against all {cloud.n.toLocaleString("en-US")} vectors.
            </>
          ) : (
            <>
              {embedderLabel} runs on the worker&apos;s GPU, not in the browser, so this box matches text. Pick the
              hashing-384 embedder to embed a query.
            </>
          )}
          {suggestion && !query ? (
            <>
              {" "}
              Try{" "}
              <button
                type="button"
                className="rounded-sm font-mono text-link hover:underline focus-visible:outline-2 focus-visible:outline-focus-ring"
                onClick={() => {
                  setText(suggestion);
                  onQuery(runQuery(cloud, suggestion, k, canEmbed));
                }}
              >
                {suggestion}
              </button>
              .
            </>
          ) : null}
        </p>
      </form>
      {query ? (
        <div className="grid gap-1" aria-live="polite">
          <p className="text-xs text-text-2">
            {query.vector
              ? `${query.tokens} token${query.tokens === 1 ? "" : "s"} → top ${query.hits.length} by cosine:`
              : `${(query.matches ?? 0).toLocaleString("en-US")} chunk${query.matches === 1 ? "" : "s"} contain it; the first ${query.hits.length}:`}
          </p>
          {query.hits.length ? (
            <ol className="grid gap-1" aria-label="Query results">
              {query.hits.map((hit, rank) => (
                <li key={hit.index}>
                  <button
                    type="button"
                    aria-current={selected === hit.index ? "true" : undefined}
                    onClick={() => onSelect(hit.index)}
                    className="flex w-full min-h-7 items-baseline gap-2 rounded-sm px-1.5 py-1 text-left text-xs hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-focus-ring aria-[current=true]:bg-surface-3"
                  >
                    <span className="w-5 shrink-0 text-text-3 tabular-nums">{rank + 1}</span>
                    {Number.isFinite(hit.score) ? (
                      <span className="w-12 shrink-0 font-mono text-text-1 tabular-nums">
                        {formatFixed(hit.score, 3)}
                      </span>
                    ) : null}
                    <span className="line-clamp-1 min-w-0 text-text-2">{cloud.meta[hit.index]?.chunk_text}</span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-xs text-text-3">Nothing matched. Try a value that appears in the table.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** A query that surely lands near something: the last clause of a row doc, or a value chunk's text. */
function suggest(cloud: Cloud): string | null {
  const text = cloud.meta[Math.floor(cloud.n / 2)]?.chunk_text;
  if (!text) return null;
  const clauses = text.split(", ");
  const clause = clauses.length > 2 ? clauses[Math.min(2, clauses.length - 1)]! : text;
  return clause.length > 40 ? clause.slice(0, 40) : clause;
}
