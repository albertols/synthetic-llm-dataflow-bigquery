# Synthetic Platform — attribution

Third-party marks, artwork and fonts the GUI ships or displays, and the
terms they are used under. Code dependencies and their licences are listed
in `package-lock.json`; `docs/ARCHITECTURE.md` lists each dependency's role.

## Marks

Third-party marks only identify the technology or organisation they belong
to: unaltered, never merged into the product's name or mark (`BRANDING.md`).

| Mark                       | Where                                                                                                                                     | Owner and terms                                                                                                                                                                                         |
| :------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Apache Beam™ name          | footer, INTRO text, the top bar's "Built with" links                                                                                      | Trademark of The Apache Software Foundation. Used to refer to Apache Beam only, with the ™ notice; never in the product name. See the [ASF trademark policy](https://www.apache.org/foundation/marks/). |
| Apache Beam logo           | the top bar's "Built with" links and the INTRO pipeline strip's Beam stages, always as a link to beam.apache.org (see Official marks)     | ASF trademark; see Official marks below for the file and the governing sentence.                                                                                                                        |
| Apache Beam firefly mascot | `apps/web/public/assets/beam-firefly-mascot.png` (copied from `docs/articles/assets/`), and embedded in the draw.io diagrams listed below | © The Apache Software Foundation ([beam.apache.org/community/mascot](https://beam.apache.org/community/mascot/), retrieved 2026-09-01), ASF trademark. Used unmodified, only to identify Apache Beam.   |
| Google Cloud product icons | embedded in the draw.io diagrams listed below, and in the INTRO pipeline strip (an architecture diagram; see Official marks)              | Google's official architecture diagram icon set, used per [Google Cloud's icon library](https://cloud.google.com/icons), only to identify the Google Cloud products shown.                              |
| Dataflow name              | the top bar's "Built with" links, as text                                                                                                 | Google Cloud product name, used to refer to the service only. The banner names it in text rather than with an icon (see Official marks).                                                                |
| vLLM logo                  | the INTRO pipeline strip's GPU stage (see Official marks)                                                                                 | The vLLM project's official media kit; used unmodified, only to identify vLLM.                                                                                                                          |
| GitHub mark                | top nav (`apps/web/src/components/GithubLinks.tsx`, inline SVG from [Octicons](https://github.com/primer/octicons), MIT)                  | Used unmodified only to link to GitHub, per [GitHub's logo guidelines](https://github.com/logos).                                                                                                       |

The product mark (`apps/web/src/app/ProductMark.tsx`, `favicon.svg`) is
original work of this project. A deployment's own brand pack (`BRANDING.md`,
"Brand packs") stays out of the repository with its logos; the pack's folder
records where they came from.

### Official marks

Files in `apps/web/src/assets/marks/`, byte-identical to their source (the
names are the sources' own). `src/assets/marks/marks.test.ts` recomputes each
SHA-256 below and fails on a changed or unlisted file. All retrieved
2026-10-14.

| File                             | SHA-256                                                            | Shown                                                                                    | Source                                                                                                                                                                                                                                      |
| :------------------------------- | :----------------------------------------------------------------- | :--------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `beam-logo-3-color-nameless.svg` | `2c4de25182d223accea32a4aa04b5b259a307d692cb0f32ef97313e9d0ed791f` | top bar "Built with" (link); INTRO strip, Beam and evaluation stages (link); "More" menu | [beam.apache.org/community/logos](https://beam.apache.org/community/logos/), "Three color", nameless SVG ([file](https://beam.apache.org/images/logos/3-color/nameless/beam-logo-3-color-nameless.svg))                                     |
| `BigQuery-512-color.svg`         | `6c088a2a7afbfcab01918c959aad90d76835093cfc2ec8314bf7ba25b85aaae8` | INTRO strip, BigQuery stages (source table, `source_table_stats`, registry)              | [cloud.google.com/icons](https://cloud.google.com/icons), "Core product icons" ([`core-products-icons.zip`](https://services.google.com/fh/files/misc/core-products-icons.zip) → `Unique Icons/BigQuery/SVG/BigQuery-512-color.svg`)        |
| `DataAnalytics-512-color.svg`    | `b32c8b4702fb90e9669e841e0410687471b7ca97ad236527f6dad8e3d90c87d9` | INTRO strip, the launcher stage (the Dataflow Flex Template launcher), named "Dataflow"  | [cloud.google.com/icons](https://cloud.google.com/icons), "Product category icons" ([`category-icons.zip`](https://services.google.com/fh/files/misc/category-icons.zip) → `Category Icons/Data Analytics/SVG/DataAnalytics-512-color.svg`) |
| `vLLM-Logo.svg`                  | `12a0812e247cef4b31160e0d0267142ffe491491537f3e275d8bc645b61eb1a8` | INTRO strip, the "Generation on L4" stage, named "vLLM"                                  | [github.com/vllm-project/media-kit](https://github.com/vllm-project/media-kit) at `79b2ea5` (2026-01-15), the compact logo                                                                                                                  |

The terms, quoted:

- **Apache Beam logo** ([ASF trademark policy](https://www.apache.org/foundation/marks/)):
  "You needn't ask us for permission to use the ASF's graphics logos (the
  versions published on individual project's websites) on your own website
  solely as a hyperlink to the specific ASF project website or to
  www.apache.org. The VP, Brand Management, a member of the Brand Management
  Committee, or the relevant Apache project's VP must approve in writing all
  other uses of Apache Foo (and similar) graphic logos." So every place the
  GUI shows the logo is a link to <https://beam.apache.org/>. The logos page
  adds: "Apache Beam, Apache, Beam, the Beam logo, and the Apache feather logo
  are either registered trademarks or trademarks of The Apache Software
  Foundation." The three-colour variant is used because it is pure vector
  (4 kB); the full-colour SVGs embed raster layers (134 kB for the nameless
  one).
- **Google Cloud icons** ([cloud.google.com/icons](https://cloud.google.com/icons)):
  "Welcome to the official library for Google Cloud product icons. Here you
  can find the Google Cloud product icons you need for your diagrams,
  technical documentation, and more." (the page's description: "Official
  Google Cloud icons to build your own architectural diagrams or reference
  architectures."). The INTRO pipeline strip is an architecture diagram of
  the pipeline, so the icons appear there. The product icons guide
  ([PDF](https://services.google.com/fh/files/misc/google-cloud-product-icons.pdf),
  "Updated: May 2026") lists Dataflow among the "Data Analytics products"
  (p. 13), where "* Denotes core product and should default to unique icon"
  and Dataflow carries no asterisk: Dataflow's icon is the Data Analytics
  category icon. The legacy console icons, which held a Dataflow hexagon, are
  not used: "Please note, these icons should not be used as of 2026." (p. 33).
  The top bar is not a diagram or documentation, and the category icon alone
  does not say "Dataflow", so the banner names Dataflow in text, as a link to
  <https://cloud.google.com/dataflow>.
- **vLLM logo** ([media kit README](https://github.com/vllm-project/media-kit)):
  "This repository contains official logo assets for vLLM in various formats
  and styles." Its best practices: "Maintain clear space around the logo",
  "Don't modify or distort the logo proportions", "Use the appropriate
  version for your background (light/dark)". The repository carries no
  licence file; the logo is used unmodified, at its own proportions, only to
  name the inference server the GPU stage runs (the same mark, yellow and
  blue, reads on both backgrounds).

## Figures shown by the GUI

`apps/web/public/assets/` holds byte-identical copies of repository figures,
made by `npm run assets:sync`. `apps/web/public/assets/provenance.json`
records, per file, the source path, its SHA-256, the script or `.drawio`
file that produces it, and the doc that shows it. These draw.io exports may
embed the Beam firefly and Google Cloud icons (terms above):

- `architecture-overview.png` (`docs/assets/architecture-overview.drawio`)
- `rag-end-to-end-flow.png`, `rag-faiss-data-path.png`, `rag-chunk-identity.png`,
  `validation-guardrails.png`, `generation-plan-routing.png`,
  `freetext-pool-ladder.png`, `runtime-fleet-topology.png`,
  `runtime-cpu-gpu-sequence.png` (`docs/articles/assets/*.drawio`)

All other figures are drawn by this project's scripts under `scripts/doc/`
(Apache-2.0, like the repository).

## Fonts

| Font                    | Package                               | Licence                   |
| :---------------------- | :------------------------------------ | :------------------------ |
| Inter Variable          | `@fontsource-variable/inter`          | SIL Open Font License 1.1 |
| JetBrains Mono Variable | `@fontsource-variable/jetbrains-mono` | SIL Open Font License 1.1 |

Both are self-hosted (bundled by Vite); no font is fetched from a third-party
CDN.

## Icons and maths

- UI icons: [Lucide](https://lucide.dev) (`lucide-react`, ISC).
- Formula rendering: [KaTeX](https://katex.org) (MIT), including its bundled
  fonts (SIL OFL 1.1).
