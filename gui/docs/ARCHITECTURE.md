# Synthetic Platform — architecture

**Claim: one npm workspace, a React SPA and a small BFF, whose types are generated
from the Python side; the browser never holds SQL or credentials.** The rule
that allows the GUI is [ADR 0042](../../docs/adr/0042-self-hosted-platform-gui.md);
the system view is [`docs/DESIGN.md` §12](../../docs/DESIGN.md#12-platform-gui).

```mermaid
flowchart LR
  classDef web   fill:#2a78d6,color:#fff,stroke:#1d5599
  classDef bff   fill:#1baf7a,color:#fff,stroke:#127a55
  classDef pkg   fill:#6b7280,color:#fff,stroke:#4b5563
  classDef py    fill:#eb6834,color:#fff,stroke:#b44f26

  PY["Python side<br/>schemas · catalogue · knobs"]:::py
  CON["packages/contracts<br/>generated zod + concepts"]:::pkg
  STATS["packages/stats<br/>shared maths"]:::pkg
  MOCK["packages/mock<br/>seeded fixtures"]:::pkg
  WEB["apps/web<br/>React SPA"]:::web
  SRV["apps/server<br/>Fastify BFF"]:::bff

  PY -->|"contracts:sync"| CON
  CON --> WEB
  CON --> SRV
  STATS --> WEB
  STATS --> MOCK
  MOCK --> SRV
  WEB -->|"/api/* (named queries)"| SRV
```

_Arrows are imports (and the one generation step from Python). Nothing in
`packages/` imports an app; `apps/web` never imports `apps/server`._

## Workspace

| Path                 | What                                                                                              | Owner                             |
| :------------------- | :------------------------------------------------------------------------------------------------ | :-------------------------------- |
| `apps/web`           | React 19 + TypeScript (strict) + Vite 8 SPA                                                       | G0a (shell, kit), tabs (features) |
| `apps/server`        | Fastify BFF: named read-only BigQuery queries or the mock provider; serves `apps/web/dist`        | G0b                               |
| `packages/contracts` | zod types generated from the Python side (`generated/**`), the concept contract and concept files | G0b (+ one concept file per tab)  |
| `packages/stats`     | Pure TS maths shared by web and mock (DKW, Wilson, TVD, hashing embedder …)                       | G0b                               |
| `packages/mock`      | Seeded, self-consistent mock data                                                                 | G0b                               |
| `scripts/`           | `check-owned-paths.mjs`, `assets-sync.mjs`                                                        | G0a                               |

## The web app

```mermaid
flowchart TB
  classDef shell fill:#eb6834,color:#0b0d12,stroke:#b44f26
  classDef lazy  fill:#2a78d6,color:#fff,stroke:#1d5599

  MAIN["main.tsx<br/>theme stamp · QueryClient · ErrorBoundary"]:::shell
  ROUTER["router.tsx<br/>code-based routes"]:::shell
  SHELL["app/AppShell<br/>TopNav · skip link · announcer · toasts"]:::shell
  ROUTES["features/&lt;tab&gt;/route.tsx<br/>zod/mini searchSchema + lazy component"]:::shell
  PAGES["features/&lt;tab&gt;/*Page.tsx<br/>tab code"]:::lazy
  ECH["EChartCanvas<br/>echarts/core"]:::lazy
  DECK["DeckCanvas<br/>deck.gl"]:::lazy
  KTX["KatexRender"]:::lazy
  MER["MermaidRender"]:::lazy
  CONC["concept files<br/>(all but core.ts)"]:::lazy

  MAIN --> ROUTER --> SHELL
  ROUTER --> ROUTES -.->|"lazy"| PAGES
  PAGES -.-> ECH & DECK & KTX & MER
  SHELL -.->|"idle prefetch"| CONC
```

_Orange is the shell chunk (budget 200 kB gzip); blue boxes are lazy chunks.
Dashed arrows are dynamic imports._

### Routes (code-based; `src/router.tsx`)

| Path                        | Module                                          | Search params (zod, owned by the tab)   |
| :-------------------------- | :---------------------------------------------- | :-------------------------------------- |
| `/`                         | `features/intro/route.tsx`                      | —                                       |
| `/evaluation`               | `features/evaluation/route.tsx` `listComponent` | `listSearchSchema` (filters)            |
| `/evaluation/$evaluationId` | `runComponent`                                  | `runSearchSchema`                       |
| `/evaluation/compare`       | `compareComponent`                              | `compareSearchSchema` (`ids` + filters) |
| `/rag`                      | `features/rag/route.tsx`                        | `table`, `digest`, `embedder`           |
| `/config`                   | `features/config/route.tsx`                     | `knob`, `scenario`                      |
| `/kit`                      | `app/kit/KitPage.tsx`                           | — (design-system reference)             |

**Route-module contract.** A tab's `route.tsx` exports its zod search
schema(s) and lazy component(s) (`lazyRouteComponent(() => import("./Page"), "Page")`).
Route modules are imported by the router, so they sit in the shell chunk:
write their schemas with **`zod/mini`** and keep page code behind the lazy
import. Inside a page, read typed params with `getRouteApi("/rag").useSearch()`
(avoids importing the router). Each route sets `staticData.title` (document
title and the navigation announcement).

### Shared components (foundation; tabs consume, never edit)

| Import                                        | API                                                                                                                                                                                                 | Notes                                                                                                                                                                                                                                                       |
| :-------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `@/lib/concepts`                              | `getConcept(id)`, `useConcept(id)`, `useConcepts()`, `listConcepts()`, `loadConcepts()`, types `Concept`, `ConceptLink`                                                                             | Registry merged from `packages/contracts/src/concepts/*.ts` by `import.meta.glob`. `core.ts` is eager; the rest is one lazy batch (prefetched at idle)                                                                                                      |
| `@/components/InfoHint`                       | `<InfoHint concept side? size? />`                                                                                                                                                                  | Radix Popover: hover 150 ms **and** click / Enter / Space; Esc closes; title, level, purpose, `<Formula>`, `<MiniDiagram>`, interpretation, pitfalls, links (`target=_blank`, `rel="noopener noreferrer"`). Unknown id → dev `console.error` + neutral icon |
| `@/components/Formula`                        | `<Formula tex display? />`                                                                                                                                                                          | KaTeX, lazy; TeX source shown until loaded; MathML for screen readers                                                                                                                                                                                       |
| `@/components/MiniDiagram`                    | `<MiniDiagram id />`, `hasDiagram(id)`                                                                                                                                                              | `src/components/diagrams/<name>.tsx` → `core:<name>`; `src/features/<tab>/diagrams/<name>.tsx` → `<tab>:<name>` (tabs add diagrams in their own folder)                                                                                                     |
| `@/components/ChartFrame`                     | `<ChartFrame title concept? option data height? empty? description? actions? columns? />`                                                                                                           | ECharts via `echarts/core` (lazy); token theme; "View data" → `DataTableFallback`; `empty.when` or `data=[]` → labelled empty state; `dataset.source = data` when `option.dataset` is absent; no animation under reduced motion                             |
| `@/components/DeckFrame`                      | `<DeckFrame layers view initialViewState ariaLabel getTooltip? onWebglUnavailable? fallback? height? />`                                                                                            | deck.gl (lazy), one canvas, released on unmount; WebGL2 missing or context lost → `onWebglUnavailable` once + `fallback`; keyboard: arrows pan, Shift + arrows orbit, +/− zoom                                                                              |
| `@/components/DataTableFallback`              | `<DataTableFallback caption rows columns? maxRows? />`                                                                                                                                              | The table twin of any visual                                                                                                                                                                                                                                |
| `@/components/Mermaid`                        | `<Mermaid chart ariaLabel />`                                                                                                                                                                       | Lazy, `securityLevel: "strict"`, themed from tokens                                                                                                                                                                                                         |
| `@/components/StatusPill`                     | `<StatusPill status label? size? />`                                                                                                                                                                | Metric (`pass`…`not_evaluated`) and run (`RUNNING`…`FAILED`) statuses; icon + label + tone                                                                                                                                                                  |
| `@/components/LevelChip`                      | `<LevelChip level withHint? size? />`, `LEVELS`, `LEVEL_ORDER`                                                                                                                                      | FIELD … MODEL, icon + label                                                                                                                                                                                                                                 |
| `@/components/EmptyState`                     | `<EmptyState title description? icon? action? compact? headingLevel? />`                                                                                                                            |                                                                                                                                                                                                                                                             |
| `@/components/PageHeader`                     | `<PageHeader eyebrow? title description? concept? actions? />`                                                                                                                                      | The page's one `h1`                                                                                                                                                                                                                                         |
| `@/components/SourceLink`                     | `<SourceLink source="path:line" />` or `path`/`line`/`gitRef`                                                                                                                                       | GitHub blob link, new tab                                                                                                                                                                                                                                   |
| `@/components/GithubLinks`, `DataSourceBadge` | top-nav pieces                                                                                                                                                                                      |                                                                                                                                                                                                                                                             |
| `@/components/ui/*`                           | button, card, badge, tabs, popover, hover-card, dialog, sheet, select, combobox, slider, switch, table, skeleton, toast, tooltip, separator, scroll-area, toggle-group                              | Radix primitives styled with tokens (shadcn-style, local)                                                                                                                                                                                                   |
| `@/lib/format`                                | `formatNumber`, `formatFixed`, `formatCompact`, `formatPercent`, `formatCount`, `formatBytes`, `formatDuration`, `formatDate(Time)`, `formatMetricValue(value, valueKind)`, `formatCell`, `MISSING` | en-US; never prints `NaN`                                                                                                                                                                                                                                   |
| `@/lib/theme`                                 | `useTheme()`, `readToken("--chart-1")`                                                                                                                                                              | Canvas renderers read tokens here                                                                                                                                                                                                                           |
| `@/lib/color`                                 | `hexToRgb`, `tokenRgba("--chart-2")`                                                                                                                                                                | deck.gl colours from tokens                                                                                                                                                                                                                                 |
| `@/lib/motion`                                | `useReducedMotion()`, `prefersReducedMotion()`                                                                                                                                                      | Wrap Motion code in `<MotionConfig reducedMotion="user">`                                                                                                                                                                                                   |
| `@/lib/links`                                 | `REPO_URL`, `DSG_URL`, `repoBlobUrl`, `parseSource`                                                                                                                                                 |                                                                                                                                                                                                                                                             |
| `@/lib/webgl`                                 | `isWebGL2Available()`                                                                                                                                                                               |                                                                                                                                                                                                                                                             |
| `@/lib/dataSource`                            | `useDataSource()` → `{ mode: "mock" } \| { mode: "bigquery"; project }`                                                                                                                             | G0b wires it to `/api/health`                                                                                                                                                                                                                               |

**Concept files.** `packages/contracts/src/concepts/<owner>.ts` exports
`concepts` built with `defineConcepts([...])`. Ids are namespaced
(`core:`, `metric:`, `knob:`, `rag:`, `intro:` …) and unique across files.
`apps/web/src/lib/concepts.test.ts` validates every file: the zod
`conceptSchema`, KaTeX that parses, diagrams that exist, a purpose of at most
two sentences.

**ECharts modules registered** (`EChartCanvas.tsx`): bar, line, scatter,
heatmap, boxplot, radar, parallel, gauge, graph, custom; grid, dataset,
transform, tooltip, axis pointer (via tooltip), legend, title, mark
line/area/point, visual map, data zoom, brush, graphic, aria; label layout,
universal transition; canvas renderer. Pie is left out on purpose.

## Size budgets (`.size-limit.json`, gzip)

| Entry                                                                       | Budget |
| :-------------------------------------------------------------------------- | -----: |
| App shell: `index-*.js` + `index-*.css`                                     | 200 kB |
| Each tab: `tab-<tab>-*.js` (chunks whose facade is under `features/<tab>/`) | 350 kB |
| Lazy ECharts (`EChartCanvas-*.js`)                                          | 300 kB |
| Lazy KaTeX (`KatexRender-*.js`)                                             |  90 kB |
| Lazy deck.gl React binding (`DeckCanvas-*.js`)                              |  80 kB |

mermaid, KaTeX, deck.gl and umap-js are lazy by construction; a tab that
imports one statically pulls it into its own tab budget.

## Dependencies (pinned exactly; `.npmrc` `save-exact=true`)

Tab agents may not add dependencies. Versions were the current stable
releases on 2026-09-28, except where a peer range forced an older line.

| Package                                     |  Version | Workspace              | Why                                                                      |
| :------------------------------------------ | -------: | :--------------------- | :----------------------------------------------------------------------- |
| react, react-dom                            |   19.3.0 | web                    | UI                                                                       |
| @tanstack/react-router                      | 1.170.40 | web                    | code-based routes, typed search params                                   |
| @tanstack/react-query                       |  5.104.0 | web                    | server state (`/api/*`)                                                  |
| zod                                         |    4.6.5 | web, server, contracts | contracts; `zod/mini` in route modules                                   |
| tailwindcss, @tailwindcss/vite              |    4.3.3 | web (dev)              | tokens → utilities                                                       |
| radix-ui                                    |    1.6.7 | web                    | accessible primitives                                                    |
| class-variance-authority                    |    0.7.1 | web                    | component variants                                                       |
| clsx                                        |    2.1.1 | web                    | class joining                                                            |
| tailwind-merge                              |    3.7.0 | web                    | class conflict resolution                                                |
| lucide-react                                |   1.48.0 | web                    | icons (no brand marks; GitHub mark is inline)                            |
| katex                                       |   0.18.9 | web                    | formulas (mermaid 12 keeps its own katex 0.16 for math in diagrams)      |
| motion                                      |   13.4.4 | web                    | animation (reduced-motion aware)                                         |
| echarts                                     |    6.1.0 | web                    | 2-D charts via `echarts/core`                                            |
| @deck.gl/core, /layers, /react, /widgets    |    9.4.0 | web                    | 3-D point clouds (`widgets` is a required peer of `react`)               |
| mermaid                                     |   12.0.0 | web                    | diagrams                                                                 |
| umap-js                                     |    1.4.0 | web                    | UMAP projection (worker)                                                 |
| yaml                                        |    2.9.1 | web, server, contracts | catalogue YAML                                                           |
| @fontsource-variable/inter, /jetbrains-mono |    5.3.0 | web                    | self-hosted OFL fonts                                                    |
| fastify                                     |   5.12.5 | server                 | BFF                                                                      |
| @fastify/static                             |   10.1.5 | server                 | serves the SPA                                                           |
| @google-cloud/bigquery                      |    9.1.0 | server                 | named read-only queries                                                  |
| lru-cache                                   |   11.5.3 | server                 | in-memory row cache (never persisted)                                    |
| vite                                        |    8.3.1 | web (dev)              | bundler                                                                  |
| @vitejs/plugin-react                        |    6.1.1 | web (dev)              | JSX + Fast Refresh (not in the brief's list; required by Vite for React) |
| typescript                                  |    6.0.3 | root (dev)             | **not 7.0**: typescript-eslint 8.70 supports `<6.1`                      |
| vitest                                      |    5.0.2 | root (dev)             | unit tests (projects: web jsdom, node)                                   |
| @testing-library/react                      |   16.3.3 | web (dev)              |                                                                          |
| @testing-library/dom                        |   10.4.2 | web (dev)              | required peer of RTL                                                     |
| @testing-library/user-event                 |   14.6.7 | web (dev)              |                                                                          |
| @testing-library/jest-dom                   |    7.0.1 | web (dev)              |                                                                          |
| jsdom                                       |   30.1.1 | web (dev)              |                                                                          |
| @types/react, @types/react-dom              |   19.3.0 | web (dev)              |                                                                          |
| @types/node                                 |  22.20.4 | root (dev)             | matches the Node 22 runtime                                              |
| @playwright/test                            |   1.63.0 | root (dev)             | e2e (local: installed Chrome; CI: bundled Chromium)                      |
| @axe-core/playwright                        |   4.13.0 | root (dev)             | WCAG checks in e2e                                                       |
| eslint, @eslint/js                          |   9.39.5 | root (dev)             | **not 10.x**: eslint-plugin-jsx-a11y 6.10.2 supports `<=9`               |
| typescript-eslint                           |   8.70.1 | root (dev)             | type-aware lint                                                          |
| eslint-plugin-react-hooks                   |    7.1.1 | root (dev)             | hooks + React Compiler rules                                             |
| eslint-plugin-jsx-a11y                      |   6.10.2 | root (dev)             | a11y lint                                                                |
| globals                                     |  17.12.0 | root (dev)             | ESLint globals                                                           |
| prettier                                    |    3.9.9 | root (dev)             | formatting                                                               |
| size-limit, @size-limit/file                |   14.1.0 | root (dev)             | bundle budgets                                                           |
| tsx                                         |  4.23.15 | root (dev)             | runs the TS server in dev                                                |
| concurrently                                |   10.0.5 | root (dev)             | `npm run dev`                                                            |

`overrides`: `lodash-es` 4.18.1 (mermaid → chevrotain pulls 4.17.23, which
carries two high-severity advisories); `npm audit` is clean.

## Ownership map

Enforced on tab branches by `scripts/check-owned-paths.mjs` (reads the JSON
block below; CI runs it on every PR). A tab that needs a foundation change
writes `NEEDS_FOUNDATION: …` in its report instead of editing the file.

| Owner                | Paths                                                                                                                                                                          |
| :------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| G0a/G0b (foundation) | everything under `gui/` not listed for a tab, plus `scripts/gui/**` and `.github/workflows/gui.yml`                                                                            |
| G1 INTRO             | `gui/apps/web/src/features/intro/**`, `gui/packages/contracts/src/concepts/intro.ts`, `gui/e2e/intro.spec.ts`                                                                  |
| G2 EVALUATION        | `gui/apps/web/src/features/evaluation/**`, `gui/packages/contracts/src/concepts/evaluation.ts`, `gui/e2e/evaluation.spec.ts`, `gui/apps/server/src/routes/evaluation.extra.ts` |
| G3 RAG               | `gui/apps/web/src/features/rag/**`, `gui/packages/contracts/src/concepts/rag.ts`, `gui/e2e/rag.spec.ts`, `gui/apps/server/src/routes/rag.extra.ts`                             |
| G4 CONFIG            | `gui/apps/web/src/features/config/**`, `gui/packages/contracts/src/concepts/config.ts`, `gui/e2e/config.spec.ts`, `gui/apps/server/src/routes/config.extra.ts`                 |

<!-- owned-paths:start -->

```json
{
  "foundation": [
    "gui/package.json",
    "gui/package-lock.json",
    "gui/.npmrc",
    "gui/tsconfig*.json",
    "gui/eslint.config.js",
    "gui/.prettierrc.json",
    "gui/.prettierignore",
    "gui/playwright.config.ts",
    "gui/vitest.config.ts",
    "gui/.size-limit.json",
    "gui/Dockerfile",
    "gui/apps/web/{index.html,vite.config.ts,package.json,tsconfig.json}",
    "gui/apps/web/src/{main.tsx,router.tsx}",
    "gui/apps/web/src/{app,components,lib,styles,test}/**",
    "gui/apps/web/public/**",
    "gui/apps/server/**",
    "gui/packages/**",
    "gui/scripts/**",
    "gui/e2e/smoke.spec.ts",
    "gui/*.md",
    "gui/docs/**",
    "scripts/gui/**",
    ".github/workflows/gui.yml"
  ],
  "tabs": {
    "intro": [
      "gui/apps/web/src/features/intro/**",
      "gui/packages/contracts/src/concepts/intro.ts",
      "gui/e2e/intro.spec.ts"
    ],
    "evaluation": [
      "gui/apps/web/src/features/evaluation/**",
      "gui/packages/contracts/src/concepts/evaluation.ts",
      "gui/e2e/evaluation.spec.ts",
      "gui/apps/server/src/routes/evaluation.extra.ts"
    ],
    "rag": [
      "gui/apps/web/src/features/rag/**",
      "gui/packages/contracts/src/concepts/rag.ts",
      "gui/e2e/rag.spec.ts",
      "gui/apps/server/src/routes/rag.extra.ts"
    ],
    "config": [
      "gui/apps/web/src/features/config/**",
      "gui/packages/contracts/src/concepts/config.ts",
      "gui/e2e/config.spec.ts",
      "gui/apps/server/src/routes/config.extra.ts"
    ]
  }
}
```

<!-- owned-paths:end -->

## Commands

| Command                                | Does                                                                           |
| :------------------------------------- | :----------------------------------------------------------------------------- |
| `npm run dev`                          | server (G0b) + Vite on `127.0.0.1:5173` (proxies `/api` to `127.0.0.1:8787`)   |
| `npm run check`                        | typecheck → lint (ESLint + Prettier) → test → build → size → `contracts:check` |
| `npm run e2e`                          | Playwright + axe over the built app (desktop 1440 px and mobile 390 px)        |
| `npm run assets:sync` / `assets:check` | copy / verify repo figures in `apps/web/public/assets` (+ `provenance.json`)   |
| `npm run owned-paths -- feat/gui-rag`  | the ownership gate for a tab branch                                            |
