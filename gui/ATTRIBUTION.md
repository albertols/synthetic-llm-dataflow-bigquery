# Synthetic Platform — attribution

Third-party marks, artwork and fonts the GUI ships or displays, and the
terms they are used under. Code dependencies and their licences are listed
in `package-lock.json`; `docs/ARCHITECTURE.md` lists each dependency's role.

## Marks

| Mark                       | Where                                                                                                                                     | Owner and terms                                                                                                                                                                                         |
| :------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Apache Beam™ name          | footer, INTRO text                                                                                                                        | Trademark of The Apache Software Foundation. Used to refer to Apache Beam only, with the ™ notice; never in the product name. See the [ASF trademark policy](https://www.apache.org/foundation/marks/). |
| Apache Beam firefly mascot | `apps/web/public/assets/beam-firefly-mascot.png` (copied from `docs/articles/assets/`), and embedded in the draw.io diagrams listed below | © The Apache Software Foundation ([beam.apache.org/community/mascot](https://beam.apache.org/community/mascot/), retrieved 2026-09-01), ASF trademark. Used unmodified, only to identify Apache Beam.   |
| Google Cloud product icons | embedded in the draw.io diagrams listed below                                                                                             | Google's official architecture diagram icon set, used per [Google Cloud's icon terms](https://cloud.google.com/icons), only to identify the Google Cloud products shown.                                |
| GitHub mark                | top nav (`apps/web/src/components/GithubLinks.tsx`, inline SVG from [Octicons](https://github.com/primer/octicons), MIT)                  | Used unmodified only to link to GitHub, per [GitHub's logo guidelines](https://github.com/logos).                                                                                                       |

The product mark (`apps/web/src/app/ProductMark.tsx`, `favicon.svg`) is
original work of this project.

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
