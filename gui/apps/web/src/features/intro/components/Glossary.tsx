/**
 * Glossary: a search over every concept in the registry — the same entries
 * the (i) hints open, from every tab's concept file and the metric catalogue.
 * The query and namespace live in the URL (`?q=…&ns=…`), so a search is a link.
 */
import { getRouteApi } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { InfoHint } from "@/components/InfoHint";
import { LevelChip } from "@/components/LevelChip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useConcepts } from "@/lib/concepts";
import { formatCount } from "@/lib/format";

import { indexConcepts, namespaceLabel, namespaceOf, searchConcepts } from "../content/glossary";
import { IntroSection } from "./primitives";

const route = getRouteApi("/");
const PAGE = 12;
const URL_DELAY_MS = 300;

export function Glossary() {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const { concepts, complete } = useConcepts();
  const [query, setQuery] = useState(search.q ?? "");
  const [namespace, setNamespace] = useState(search.ns ?? "all");
  const [expanded, setExpanded] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  /** Mirrors the query into the URL, debounced, without a history entry per keystroke. */
  const writeUrl = (q: string, ns: string) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      void navigate({
        search: (prev) => ({ ...prev, q: q.trim() || undefined, ns: ns === "all" ? undefined : ns }),
        replace: true,
        resetScroll: false,
      });
    }, URL_DELAY_MS);
  };

  const index = useMemo(() => indexConcepts(concepts), [concepts]);
  const namespaces = useMemo(() => {
    const counts = new Map<string, number>();
    for (const concept of concepts) counts.set(namespaceOf(concept), (counts.get(namespaceOf(concept)) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [concepts]);
  const scope = namespace !== "all" && !namespaces.some(([ns]) => ns === namespace) && complete ? "all" : namespace;
  const results = useMemo(() => searchConcepts(index, query, scope), [index, query, scope]);
  const visible = expanded ? results : results.slice(0, PAGE);

  return (
    <IntroSection
      id="glossary"
      eyebrow="Glossary"
      title="Every term, one search away"
      lead="The same entries the (i) hints open across the app: pipeline stages, metrics, knobs, RAG and evaluation terms, each with its purpose, formula and sources."
    >
      <div className="grid gap-3 rounded-lg border border-border bg-surface-1 p-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="relative">
            <label htmlFor="glossary-query" className="sr-only">
              Search the glossary
            </label>
            <Search
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-3"
              aria-hidden="true"
            />
            <input
              id="glossary-query"
              type="search"
              value={query}
              autoComplete="off"
              spellCheck={false}
              placeholder="noise floor, DKW, driving edge, pool…"
              onChange={(event) => {
                setQuery(event.target.value);
                setExpanded(false);
                writeUrl(event.target.value, scope);
              }}
              className="h-10 w-full rounded-md border border-control-border bg-surface-2 pr-3 pl-9 text-sm text-text-1 placeholder:text-text-3 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring"
            />
          </div>
          <div className="-mx-1 overflow-x-auto px-1 pb-1 lg:pb-0">
            <ToggleGroup
              type="single"
              value={scope}
              onValueChange={(value) => {
                if (!value) return;
                setNamespace(value);
                setExpanded(false);
                writeUrl(query, value);
              }}
              aria-label="Concept group"
              className="flex-nowrap"
            >
              <ToggleGroupItem value="all">All</ToggleGroupItem>
              {namespaces.map(([ns, count]) => (
                <ToggleGroupItem key={ns} value={ns} className="whitespace-nowrap">
                  {namespaceLabel(ns)}
                  <span className="text-xs text-text-3">{count}</span>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </div>
        <p role="status" aria-live="polite" className="text-xs text-text-3">
          {complete
            ? `${formatCount(results.length)} of ${formatCount(concepts.length)} concepts${query.trim() ? ` match “${query.trim()}”` : ""}`
            : "Loading every concept file…"}
        </p>
      </div>

      {!complete && !concepts.length ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-lg" />
          ))}
        </div>
      ) : results.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-strong p-6 text-center text-sm text-text-2">
          <p className="font-semibold text-text-1">No concept matches “{query.trim()}”.</p>
          <p>Try a shorter word, another spelling, or the “All” group.</p>
        </div>
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Glossary results">
            {visible.map((concept) => (
              <li
                key={concept.id}
                className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-surface-1 p-4"
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm leading-snug font-semibold text-text-1">{concept.title}</h3>
                  <InfoHint concept={concept.id} side="left" className="-mt-0.5 -mr-1" />
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline">{namespaceLabel(namespaceOf(concept))}</Badge>
                  {concept.level ? <LevelChip level={concept.level} size="sm" /> : null}
                </div>
                <p className="line-clamp-3 text-sm leading-snug text-text-2">{concept.purpose}</p>
                <code className="mt-auto truncate font-mono text-[11px] text-text-3">{concept.id}</code>
              </li>
            ))}
          </ul>
          {results.length > PAGE ? (
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => setExpanded((open) => !open)}>
                {expanded ? "Show fewer" : `Show all ${formatCount(results.length)}`}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </IntroSection>
  );
}
