/**
 * The embedding explorer: the current space's 384-d vectors projected to 3-D
 * (PCA in a worker, or UMAP in a worker with the BFF caching the layout),
 * coloured by kind / column / table / 384-d cluster, the current strategy's
 * seeds ringed, hover text with nearest-neighbour lines, a query box, lasso
 * statistics and the projection's k-NN overlap. The 2-D canvas view is
 * both a choice and the WebGL fallback; "View data" is the table twin.
 */
import { Box, Lasso, RotateCcw, Square, Table2 } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { Callout } from "@/components/Callout";
import { DataTableFallback } from "@/components/DataTableFallback";
import { EmptyState } from "@/components/EmptyState";
import { InfoHint } from "@/components/InfoHint";
import { StatTile } from "@/components/StatTile";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatFixed, formatPercent } from "@/lib/format";
import { useReducedMotion } from "@/lib/motion";
import { useTheme } from "@/lib/theme";

import { categorise, COLOR_BY, COLOR_BY_LABELS, kindLabel, type ColorBy } from "../lib/categories";
import { hashingNoise, meanPairwiseCosine } from "../lib/metrics";
import { placeByNeighbours, projectPca, TRUST_K, TRUST_SAMPLE } from "../lib/projection";
import { BROWSER_EMBEDDER, listTables, spaceLabel } from "../lib/selection";
import { STRATEGIES } from "../lib/strategies";
import { topKByCosine } from "../lib/vectors";
import { FieldSelect } from "../FieldSelect";
import { useElementWidth } from "../useElementWidth";
import type { RagModel } from "../useRagModel";
import { Cloud2D } from "./Cloud2D";
import { Inspector } from "./Inspector";
import { Legend } from "./Legend";
import { pointColors, readPalette } from "./palette";
import { QueryPanel, type QueryState } from "./QueryPanel";
import { SelectionStats } from "./SelectionStats";
import type { CloudViewProps } from "./types";

const Cloud3D = lazy(() => import("./Cloud3D"));

const NEIGHBOURS = 5;

function defaultColorBy(space: string): ColorBy {
  if (space === "tables") return "table";
  if (space === "all") return "column";
  return "cluster";
}

function fitZoom(width: number, height: number): number {
  return Math.log2(Math.max(0.46 * Math.min(width || height, height), 1));
}

export function ExplorerSection({ model }: { model: RagModel }) {
  const { cloud, resolved, set, projection, seeds, search, setSearch, strategy, k } = model;
  const reducedMotion = useReducedMotion();
  const { resolved: theme } = useTheme();
  const boxRef = useRef<HTMLDivElement | null>(null);
  const width = useElementWidth(boxRef);
  const height = width && width < 640 ? 340 : 480;

  const [hover, setHover] = useState<{ index: number; client: { x: number; y: number } } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [isolated, setIsolated] = useState<string | null>(null);
  const [lassoActive, setLassoActive] = useState(false);
  const [lasso, setLasso] = useState<number[] | null>(null);
  const [query, setQuery] = useState<QueryState | null>(null);
  const [camera, setCamera] = useState<object | null>(null);
  const [webglLost, setWebglLost] = useState(false);
  const [showTable, setShowTable] = useState(false);

  // A new cloud invalidates every index-based choice.
  const cloudKey = cloud?.key ?? "";
  const [seenKey, setSeenKey] = useState(cloudKey);
  if (seenKey !== cloudKey) {
    setSeenKey(cloudKey);
    setHover(null);
    setSelected(null);
    setLasso(null);
    setIsolated(null);
    setQuery(null);
  }

  // Esc leaves lasso mode.
  useEffect(() => {
    if (!lassoActive) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLassoActive(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lassoActive]);

  const pcaEntry = projection.pca;
  const active = projection.active;
  const shown = active?.status === "done" ? active : pcaEntry?.status === "done" ? pcaEntry : null;
  const result = shown?.status === "done" ? shown.result : null;
  const clusters = pcaEntry?.status === "done" ? (pcaEntry.clusters ?? null) : null;
  const pcaModel = pcaEntry?.status === "done" ? pcaEntry.result.pca : undefined;

  const availableColorBys = useMemo(() => {
    if (!cloud) return ["cluster"] as ColorBy[];
    const kinds = new Set(cloud.meta.map((m) => m.chunk_kind)).size;
    const columns = new Set(cloud.meta.map((m) => m.column ?? "")).size;
    const tables = new Set(cloud.tables).size;
    return COLOR_BY.filter(
      (by) =>
        by === "cluster" ||
        (by === "kind" && kinds > 1) ||
        (by === "column" && columns > 1) ||
        (by === "table" && tables > 1),
    );
  }, [cloud]);
  const colorBy: ColorBy =
    search.color && availableColorBys.includes(search.color)
      ? search.color
      : availableColorBys.includes(defaultColorBy(resolved?.space ?? "rows"))
        ? defaultColorBy(resolved?.space ?? "rows")
        : "cluster";

  const categorised = useMemo(
    () =>
      cloud
        ? categorise(cloud, colorBy, { columns: set?.columns ?? [], tables: listTables(model.sets), clusters })
        : null,
    [cloud, colorBy, set, model.sets, clusters],
  );
  // `theme` re-reads the tokens after a theme switch.
  const palette = useMemo(() => {
    void theme;
    return readPalette();
  }, [theme]);
  const isolatedIndices = useMemo(() => {
    if (!isolated || !categorised) return null;
    const c = categorised.categories.findIndex((cat) => cat.key === isolated);
    const out: number[] = [];
    categorised.of.forEach((value, i) => {
      if (value === c) out.push(i);
    });
    return out;
  }, [isolated, categorised]);
  const keep = useMemo(() => (lasso ? new Set(lasso) : null), [lasso]);
  const colors = useMemo(
    () => (categorised ? pointColors(categorised, palette, { isolated, keep }) : new Uint8ClampedArray()),
    [categorised, palette, isolated, keep],
  );
  const colorVersion = `${colorBy}|${isolated ?? ""}|${lasso?.length ?? 0}|${theme}|${clusters ? 1 : 0}|${cloudKey}`;

  const focus = hover?.index ?? selected;
  const neighbourHits = useMemo(
    () =>
      cloud && focus !== null && cloud.rows[focus]
        ? topKByCosine(cloud.rows[focus], cloud.rows, NEIGHBOURS, { exclude: focus, rowNorms: cloud.norms })
        : [],
    [cloud, focus],
  );
  const selectedHits = useMemo(
    () =>
      cloud && selected !== null && cloud.rows[selected]
        ? topKByCosine(cloud.rows[selected], cloud.rows, NEIGHBOURS, { exclude: selected, rowNorms: cloud.norms })
        : [],
    [cloud, selected],
  );
  const baseline = useMemo(
    () =>
      cloud
        ? (meanPairwiseCosine(
            cloud.rows,
            cloud.rows.map((_, i) => i),
            cloud.norms,
            20_000,
          )?.mean ?? null)
        : null,
    [cloud],
  );

  const canEmbed = !!set && set.embedder_id === BROWSER_EMBEDDER && cloud?.dim === 384;
  const queryView = useMemo(() => {
    if (!query || !result) return null;
    let position: readonly [number, number, number] | null = null;
    if (query.vector) {
      position =
        result.method === "pca" && pcaModel
          ? projectPca(query.vector, pcaModel, result.frame)
          : placeByNeighbours(query.hits, result.coords);
    }
    return { position, hits: query.hits.map((h) => h.index) };
  }, [query, result, pcaModel]);

  const layoutKey = `${cloudKey}|${result?.method ?? "none"}`;
  const viewState = camera ?? {
    target: [0, 0, 0],
    rotationX: 18,
    rotationOrbit: 32,
    zoom: fitZoom(width, height),
    minZoom: 3,
    maxZoom: 14,
  };

  if (!resolved || !set) return null;
  if (model.cloudQuery.error) {
    return (
      <Callout tone="danger" title="The vectors could not be loaded">
        {model.cloudQuery.error.message}. Check that the BFF is running, then reload the page.
      </Callout>
    );
  }

  const view = search.view ?? "3d";
  const info = STRATEGIES[strategy];
  const seedRank = selected !== null ? seeds.indexOf(selected) + 1 || null : null;
  const axes: readonly [string, string] = result?.method === "umap" ? ["UMAP 1", "UMAP 2"] : ["PC 1", "PC 2"];
  const describe = (dims: "3-D" | "2-D") =>
    cloud
      ? `${spaceLabel(resolved.space)}: ${cloud.n} vectors of ${cloud.dim} dimensions, projected to ${dims} by ${
          result?.method === "umap" ? "UMAP" : "PCA"
        }, coloured by ${COLOR_BY_LABELS[colorBy].toLowerCase()}${
          seeds.length ? `, ${seeds.length} ${info.label} seeds ringed` : ""
        }`
      : "Loading the vectors";
  const ariaLabel = describe(view === "3d" ? "3-D" : "2-D");

  const viewProps: CloudViewProps | null =
    cloud && result && categorised
      ? {
          n: cloud.n,
          coords: result.coords,
          colors,
          colorVersion,
          palette,
          seeds,
          focus,
          neighbours: neighbourHits.map((h) => h.index),
          query: queryView,
          lassoActive,
          reducedMotion,
          height,
          ariaLabel,
          layoutKey,
          axes,
          onHover: (index, client) => setHover(index === null || !client ? null : { index, client }),
          onSelect: (index) => setSelected(index),
          onLasso: (indices) => {
            setLasso(indices);
            setIsolated(null);
            setLassoActive(false);
          },
        }
      : null;

  const selection = lasso
    ? { indices: lasso, source: "lasso" }
    : isolated && isolatedIndices
      ? {
          indices: isolatedIndices,
          source: categorised?.categories.find((c) => c.key === isolated)?.label ?? isolated,
        }
      : null;

  const progress =
    active?.status === "running" && search.proj === "umap"
      ? `Fitting UMAP in a worker${active.progress !== null ? ` · ${formatPercent(active.progress, 0)}` : "…"}`
      : null;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end gap-2" role="toolbar" aria-label="Explorer view options">
        <ToggleGroup
          type="single"
          aria-label="Projection"
          value={search.proj ?? "pca"}
          onValueChange={(value) => value && setSearch({ proj: value as "pca" | "umap" })}
        >
          <ToggleGroupItem value="pca">PCA</ToggleGroupItem>
          <ToggleGroupItem value="umap">UMAP</ToggleGroupItem>
        </ToggleGroup>
        <InfoHint concept={search.proj === "umap" ? "rag:umap" : "rag:pca"} />
        <ToggleGroup
          type="single"
          aria-label="View"
          value={view}
          onValueChange={(value) => {
            if (!value) return;
            setSearch({ view: value as "3d" | "2d" });
            if (value === "3d") setWebglLost(false);
          }}
        >
          <ToggleGroupItem value="3d">
            <Box aria-hidden="true" />
            3-D
          </ToggleGroupItem>
          <ToggleGroupItem value="2d">
            <Square aria-hidden="true" />
            2-D
          </ToggleGroupItem>
        </ToggleGroup>
        <FieldSelect
          label="Colour by"
          className="w-44"
          value={colorBy}
          options={availableColorBys.map((by) => ({ value: by, label: COLOR_BY_LABELS[by] }))}
          onChange={(value) => setSearch({ color: value as ColorBy })}
          hint={<InfoHint concept={colorBy === "cluster" ? "rag:clusters" : "rag:chunk-kinds"} />}
        />
        <Button
          size="sm"
          variant={lassoActive ? "primary" : "secondary"}
          aria-pressed={lassoActive}
          onClick={() => setLassoActive((on) => !on)}
          disabled={!viewProps}
        >
          <Lasso aria-hidden="true" />
          {lassoActive ? "Drawing: drag round points (Esc)" : "Lasso"}
        </Button>
        {view === "3d" && !webglLost ? (
          <Button size="sm" variant="ghost" onClick={() => setCamera(null)}>
            <RotateCcw aria-hidden="true" />
            Reset view
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" aria-pressed={showTable} onClick={() => setShowTable((on) => !on)}>
          <Table2 aria-hidden="true" />
          {showTable ? "Hide data" : "View data"}
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div ref={boxRef} className="grid min-w-0 content-start gap-2">
          <div className="relative">
            {!viewProps ? (
              <Skeleton style={{ height }} className="w-full" />
            ) : view === "2d" ? (
              <div className="overflow-hidden rounded-lg border border-border bg-surface-1">
                <Cloud2D {...viewProps} />
                <p className="border-t border-border px-3 py-1.5 text-xs text-text-3">
                  2-D view ({axes[0]} × {axes[1]}) on a plain canvas, no WebGL. Hover to read a point, click to pin it.
                </p>
              </div>
            ) : (
              <Suspense fallback={<Skeleton style={{ height }} className="w-full" />}>
                <Cloud3D
                  {...viewProps}
                  viewState={viewState}
                  onViewStateChange={setCamera}
                  onWebglUnavailable={() => setWebglLost(true)}
                  fallback={<Cloud2D {...viewProps} ariaLabel={describe("2-D")} />}
                />
              </Suspense>
            )}
            {progress ? (
              <p
                role="status"
                className="absolute top-2 left-2 rounded-md border border-border bg-surface-2/90 px-2 py-1 text-xs text-text-2"
              >
                {progress} — showing PCA meanwhile
              </p>
            ) : null}
          </div>
          {categorised ? (
            <Legend
              categories={categorised.categories}
              palette={palette}
              isolated={isolated}
              onIsolate={(key) => {
                setIsolated(key);
                setLasso(null);
              }}
              showSeeds={seeds.length > 0}
              showQuery={!!queryView?.position}
            />
          ) : null}
          {!model.retrieves ? (
            <p className="text-xs text-text-3">
              No seeds here: the pipeline retrieves per column (value chunks) or over one table&apos;s row documents,
              never across {resolved.space === "tables" ? "tables" : "kinds"}. Pick a single space to see them.
            </p>
          ) : (
            <p className="text-xs text-text-3">
              Rings: the {seeds.length} seeds that {info.label} picks here ({info.truth.toLowerCase()}).
              {resolved.space === "rows" && strategy !== "centroid"
                ? " In the pipeline the row exemplars always use the centroid; the strategy chooses among a column's values."
                : ""}
            </p>
          )}
          {cloud ? (
            <div className="mt-2 rounded-lg border border-border bg-surface-1 p-4">
              <QueryPanel
                key={cloudKey}
                cloud={cloud}
                canEmbed={canEmbed}
                embedderLabel={set.embedder_id}
                k={k}
                query={query}
                onQuery={setQuery}
                onSelect={setSelected}
                selected={selected}
              />
            </div>
          ) : null}
        </div>

        <aside className="grid content-start gap-4" aria-label="Explorer details">
          {cloud ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <StatTile
                  label="k-NN overlap"
                  concept="rag:knn-overlap"
                  value={shown?.status === "done" ? (shown.trust ?? null) : null}
                  format={(v) => formatPercent(v, 0)}
                  footnote={
                    shown?.status === "done" && shown.trust === undefined
                      ? "measuring…"
                      : `${TRUST_K} nearest, ${TRUST_SAMPLE} points, ${result?.method === "umap" ? "UMAP" : "PCA"} 3-D`
                  }
                />
                {pcaModel && result?.method !== "umap" ? (
                  <StatTile
                    label="Variance in 3 axes"
                    concept="rag:pca"
                    value={pcaModel.explained.reduce((a, b) => a + b, 0)}
                    format={(v) => formatPercent(v, 1)}
                    footnote={`PC1 ${formatPercent(pcaModel.explained[0], 1)}`}
                  />
                ) : (
                  <StatTile
                    label="Layout"
                    concept="rag:umap"
                    value={
                      shown?.status === "done" && shown.fromCache
                        ? "cached"
                        : result?.method === "umap"
                          ? "fitted"
                          : "—"
                    }
                    footnote={
                      shown?.status === "done" && shown.cacheNote
                        ? `not cached: ${shown.cacheNote}`
                        : shown?.status === "done" && shown.fromCache
                          ? "served from the BFF's memory"
                          : "in this browser's worker; the BFF keeps it"
                    }
                  />
                )}
              </div>
              <Inspector
                cloud={cloud}
                index={selected}
                neighbours={selectedHits}
                seedRank={seedRank}
                onSelect={setSelected}
                noise={set.embedder_id === BROWSER_EMBEDDER ? hashingNoise(cloud.dim) : null}
              />
              {selection ? (
                <SelectionStats
                  cloud={cloud}
                  indices={selection.indices}
                  source={selection.source}
                  baseline={baseline}
                  onClear={() => {
                    setLasso(null);
                    setIsolated(null);
                  }}
                />
              ) : null}
              {active?.status === "error" ? (
                <Callout tone="warn" title="Projection failed">
                  {active.message} Showing PCA.
                </Callout>
              ) : null}
              {model.cloudQuery.data?.dataSource === "mock" ? (
                <p className="text-xs text-text-3">
                  Mock data: the chunk text is invented (thelook-shaped);{" "}
                  {set.embedder_id === BROWSER_EMBEDDER
                    ? "the hashing vectors are the real embedder's output over it."
                    : "bge vectors are a seeded mixture, not a model's output."}
                </p>
              ) : null}
            </>
          ) : (
            <Skeleton className="h-64 w-full" />
          )}
        </aside>
      </div>

      {hover && cloud?.meta[hover.index] ? <HoverCard cloud={cloud} index={hover.index} client={hover.client} /> : null}

      {showTable && cloud && result ? (
        <DataTableFallback
          caption={`${spaceLabel(resolved.space)} — ${cloud.n} points (first 500 listed)`}
          rows={cloud.meta.map((m, i) => ({
            point: i + 1,
            kind: kindLabel(m.chunk_kind),
            column: m.column ?? "",
            table: cloud.tables[i],
            text: m.chunk_text.length > 90 ? `${m.chunk_text.slice(0, 90)}…` : m.chunk_text,
            x: formatFixed(result.coords[i * 3], 3),
            y: formatFixed(result.coords[i * 3 + 1], 3),
            z: formatFixed(result.coords[i * 3 + 2], 3),
            cluster: clusters ? `C${(clusters[i] ?? 0) + 1}` : "",
            seed: seeds.indexOf(i) >= 0 ? `#${seeds.indexOf(i) + 1}` : "",
          }))}
        />
      ) : null}
      {cloud && cloud.n === 0 ? (
        <EmptyState
          compact
          headingLevel={3}
          title="No vectors in this space"
          description="The set lists no chunks of this kind. Pick another space or sample above."
        />
      ) : null}
    </div>
  );
}

/** The hover tooltip: fixed at the pointer, never the only way to read a point (the inspector and table are). */
function HoverCard({
  cloud,
  index,
  client,
}: {
  cloud: NonNullable<RagModel["cloud"]>;
  index: number;
  client: { x: number; y: number };
}) {
  const meta = cloud.meta[index]!;
  const text = meta.chunk_text.length > 260 ? `${meta.chunk_text.slice(0, 260)}…` : meta.chunk_text;
  const left = Math.min(client.x + 14, (typeof window !== "undefined" ? window.innerWidth : 1200) - 336);
  const top = client.y + 14;
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed z-40 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-border-strong bg-surface-2 px-3 py-2 text-xs shadow-[var(--shadow-pop)]"
      style={{ left: Math.max(8, left), top }}
    >
      <p className="mb-1 font-semibold text-text-1">
        {kindLabel(meta.chunk_kind)}
        {meta.column ? ` · ${meta.column}` : ""} · point {index + 1}
      </p>
      <p className="font-mono leading-relaxed break-words text-text-2">{text}</p>
      <p className="mt-1 text-text-3">Lines: its 5 nearest by cosine. Click to pin.</p>
    </div>
  );
}
