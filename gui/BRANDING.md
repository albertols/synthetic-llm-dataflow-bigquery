# Synthetic Platform — branding

**Claim: a dark, near-black console with Beam-orange and GCP-blue accents,
every colour a measured token, and no one else's marks in the product
identity.**

## Name and mark

- The product is **Synthetic Platform**. Never "Apache Beam …", never a name
  that implies an Apache, Google or Orange product.
- The mark (`apps/web/src/app/ProductMark.tsx`, `apps/web/public/favicon.svg`)
  is original artwork: a solid circle (the real sample) overlapped by a dashed
  twin (its synthetic copy).
- **Apache Beam™** is a trademark of The Apache Software Foundation. The name
  and the firefly mascot refer to Apache Beam only, unmodified, with the TM
  attribution (shown in the footer). The mascot is available at
  `/assets/beam-firefly-mascot.png` for pages that identify Beam; it never
  stands for this product.
- Google Cloud product icons appear only inside the repo's diagrams, per
  Google's icon terms; see `ATTRIBUTION.md`.
- The CONFIG "amp" is an **original neutral dark console with text-labelled
  knobs** and Beam-orange accents. It does not reproduce Orange Amps' trade
  dress: no pictogram-only legends, no orange tolex, no crest, wordmark or
  slogan.

## Palette (tokens: `apps/web/src/styles/tokens.css`)

Brand hues: Beam orange `#eb6834`, GCP blue `#2a78d6`, aqua `#1baf7a`
(CPU, healthy), purple `#7a3fd1` (GPU), slate `#6b7280`.

### Dark (default)

| Token                       | Hex                               | Contrast (on surface-1 `#12151b`) | Use                                              |
| :-------------------------- | :-------------------------------- | --------------------------------: | :----------------------------------------------- |
| `--bg`                      | `#0b0d12`                         |                                 — | page                                             |
| `--surface-1/2/3`           | `#12151b` / `#181c24` / `#20252f` |                                 — | cards / popovers / hover                         |
| `--text-1`                  | `#f2f4f7`                         |                              16.6 | primary text                                     |
| `--text-2`                  | `#b8bfcc`                         |                               9.9 | secondary text                                   |
| `--text-3`                  | `#939aa7`                         |            6.1 (5.4 on surface-3) | muted text, axis labels                          |
| `--accent`                  | `#eb6834`                         |                               5.7 | primary action, focus, active tab                |
| `--accent-fg` on `--accent` | `#0b0d12`                         |                               6.1 | text on orange buttons                           |
| `--link`                    | `#6da7ec`                         |                               7.3 | links                                            |
| `--control-border`          | `#737b8d`                         |            3.8 (4.0 on surface-2) | input boundaries (WCAG 1.4.11)                   |
| `--gpu-text`                | `#a37ae6`                         |                               5.6 | GPU labels (`--gpu` `#7a3fd1` is for marks, 3.0) |
| `--status-good`             | `#1baf7a`                         |                               6.5 | good (icon + label)                              |
| `--status-warn`             | `#fab219`                         |                              10.0 | warning                                          |
| `--status-serious`          | `#ec835a`                         |                               6.9 | serious                                          |
| `--status-critical-text`    | `#f07373`                         |                               6.5 | critical text (`#d03b3b` for marks, 3.8)         |

### Light (accessibility toggle)

| Token                                        | Hex                                           | Contrast (on surface-1 `#fcfcfb`) |
| :------------------------------------------- | :-------------------------------------------- | --------------------------------: |
| `--text-1/2/3`                               | `#0b0d12` / `#454b57` / `#5d6470`             |                  18.9 / 8.5 / 5.8 |
| `--accent-text`                              | `#b8481c`                                     |                               5.1 |
| `--link`                                     | `#1c5cab`                                     |                               6.5 |
| `--control-border`                           | `#7d8492`                                     |                               3.8 |
| status text good / warn / serious / critical | `#0f7a54` / `#8a5a00` / `#b8481c` / `#b42b2b` |             5.2 / 5.8 / 5.1 / 6.2 |

Contrast ratios are WCAG 2.x relative-luminance ratios, computed with the
dataviz skill's `contrast()` (2026-09-28).

### Chart palette

Categorical slots in **fixed order** — the dataviz skill's validated default
order with the brand's purple in slot 7:

|      Slot | Dark      | Light     |
| --------: | :-------- | :-------- |
|    1 blue | `#3987e5` | `#2a78d6` |
|  2 orange | `#d95926` | `#eb6834` |
|    3 aqua | `#1baf7a` | `#1baf7a` |
|  4 yellow | `#c98500` | `#eda100` |
| 5 magenta | `#d55181` | `#e87ba4` |
|   6 green | `#008300` | `#008300` |
|  7 purple | `#7a3fd1` | `#7a3fd1` |
|     8 red | `#e66767` | `#e34948` |

`validate_palette.js` results (adjacent pairs, Machado 2009 CVD simulation):

- **dark on `#12151b`: all checks pass** — lightness band, chroma, worst
  adjacent CVD ΔE 9.3 (aqua ↔ orange), normal-vision ΔE ≥ 19.3, every slot
  ≥ 3 : 1. Beam orange `#eb6834` itself sits just above the dark band
  (L 0.671 > 0.67), so charts use its dark step `#d95926`; UI accents keep
  `#eb6834`.
- **light on `#fcfcfb`: pass with the contrast WARN** — aqua, yellow and
  magenta are below 3 : 1, so the relief channel is mandatory: every chart has
  the "View data" table (ChartFrame) and direct labels where used.
- `--pairs all` (scatter, small multiples): the first **three** slots pass
  (worst CVD ΔE 9.3, normal-vision 22.4); past three, fold to "Other" or facet.

Status colours are reserved and always paired with an icon and a label. The
brand's "healthy" aqua is both status-good and categorical slot 3, so a chart
never shows both roles at once (UX.md, Charts).

## Type

Self-hosted OFL fonts: **Inter Variable** (UI) and **JetBrains Mono
Variable** (code, ids, tab labels), from `@fontsource-variable/*`.
Proportional figures for standalone numbers; `tabular-nums` in tables and
axes.
