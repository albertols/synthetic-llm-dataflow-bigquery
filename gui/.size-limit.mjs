/**
 * Bundle budgets (gzip), measured on what a browser downloads — not on chunk
 * names, which let shared chunks escape every budget. Each budget is an import
 * closure read from Vite's manifest (apps/web/dist/.vite/manifest.json): a
 * chunk, its CSS and every chunk it imports statically, transitively.
 *
 *   shell   index.html's closure (the entry chunk, the shared chunks it
 *           imports, the global CSS)                                    ≤ 200 kB
 *   tab     the closure of the tab's lazy page chunks (the router's dynamic
 *           imports under src/features/<tab>/), minus what the shell already
 *           loaded                                                      ≤ 350 kB
 *   lazy    ECharts, KaTeX and deck.gl, each beyond the shell
 *
 * Also a gate: mermaid, KaTeX, deck.gl, ECharts and umap-js must stay out of
 * the shell. The shell's chunks are checked by their source maps, so a lazy
 * library pulled in statically fails here whatever Rolldown names the chunk.
 * Run after `npm run build` (`npm run check` does).
 */
import { existsSync, readFileSync } from "node:fs";

const DIST = "apps/web/dist";
const MANIFEST = `${DIST}/.vite/manifest.json`;
if (!existsSync(MANIFEST))
  throw new Error(`${MANIFEST} is missing: run \`npm run build\` first (vite.config.ts sets build.manifest)`);
/** @type {Record<string, { file: string; src?: string; css?: string[]; imports?: string[]; dynamicImports?: string[]; isEntry?: boolean; isDynamicEntry?: boolean }>} */
const manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));

/** Output files (JS + CSS) for these manifest keys and every static import, transitively. */
function closure(keys) {
  const files = new Set();
  const seen = new Set();
  const stack = [...keys];
  while (stack.length) {
    const key = stack.pop();
    if (seen.has(key)) continue;
    seen.add(key);
    const chunk = manifest[key];
    if (!chunk) throw new Error(`${key} is not in the Vite manifest`);
    files.add(chunk.file);
    for (const css of chunk.css ?? []) files.add(css);
    stack.push(...(chunk.imports ?? []));
  }
  return files;
}

const shell = closure(["index.html"]);
const beyondShell = (files) => [...files].filter((file) => !shell.has(file));
const paths = (files) => [...files].map((file) => `${DIST}/${file}`);

/** A lazy chunk's manifest key, checked to still be a dynamic entry. */
function lazy(src) {
  if (!manifest[src]?.isDynamicEntry) throw new Error(`${src} is no longer a lazy (dynamically imported) chunk`);
  return src;
}

const PAGES = manifest["index.html"].dynamicImports ?? [];
function tabPages(tab) {
  const pages = PAGES.filter((key) => key.startsWith(`src/features/${tab}/`));
  if (!pages.length) throw new Error(`no lazy page chunk under src/features/${tab}/ (router.tsx)`);
  return pages;
}

/** node_modules packages bundled into a chunk, from its source map (Vite's own helpers have none). */
const packageCache = new Map();
function packagesIn(file) {
  if (!packageCache.has(file)) packageCache.set(file, readPackages(file));
  return packageCache.get(file);
}
function readPackages(file) {
  const mapFile = `${DIST}/${file}.map`;
  if (!existsSync(mapFile)) return new Set();
  const map = JSON.parse(readFileSync(mapFile, "utf8"));
  return new Set(
    map.sources
      .filter((source) => source.includes("node_modules/"))
      .map((source) => {
        const [first, second] = source.split("node_modules/").pop().split("/");
        return first.startsWith("@") ? `${first}/${second}` : first;
      }),
  );
}

const LAZY_LIBRARIES = [
  "mermaid",
  "katex",
  "echarts",
  "zrender",
  "umap-js",
  "@deck.gl/core",
  "@deck.gl/layers",
  "@luma.gl/core",
];
for (const file of shell) {
  if (!file.endsWith(".js")) continue;
  const leaked = LAZY_LIBRARIES.filter((name) => packagesIn(file).has(name));
  if (leaked.length)
    throw new Error(
      `the app shell (${file}) bundles ${leaked.join(", ")}: import it lazily (see docs/ARCHITECTURE.md)`,
    );
}

/** Chunks holding deck.gl's core and layers (the part DeckCanvas and RAG's 3-D view share), found by source map. */
const deckFiles = beyondShell(closure([lazy("src/components/DeckCanvas.tsx")])).filter(
  (file) =>
    file.endsWith(".js") &&
    ["@deck.gl/core", "@deck.gl/layers", "@luma.gl/core"].some((name) => packagesIn(file).has(name)),
);
const deckBinding = manifest["src/components/DeckCanvas.tsx"].file;

export default [
  { name: "app shell (index.html closure, JS + CSS)", path: paths(shell), limit: "200 kB", gzip: true },
  ...["intro", "evaluation", "rag", "config"].map((tab) => ({
    name: `tab: ${tab.toUpperCase()} (page closure beyond the shell)`,
    path: paths(beyondShell(closure(tabPages(tab)))),
    limit: "350 kB",
    gzip: true,
  })),
  {
    name: "lazy: ECharts (EChartCanvas closure beyond the shell)",
    path: paths(beyondShell(closure([lazy("src/components/EChartCanvas.tsx")]))),
    limit: "300 kB",
    gzip: true,
  },
  {
    name: "lazy: KaTeX (KatexRender closure, JS + CSS)",
    path: paths(beyondShell(closure([lazy("src/components/KatexRender.tsx")]))),
    limit: "90 kB",
    gzip: true,
  },
  { name: "lazy: deck.gl React binding (DeckCanvas chunk)", path: paths([deckBinding]), limit: "80 kB", gzip: true },
  {
    name: "lazy: deck.gl core + layers (shared chunks, by source map)",
    path: paths(deckFiles.filter((file) => file !== deckBinding)),
    limit: "200 kB",
    gzip: true,
  },
];
