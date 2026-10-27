# GUI screenshots — provenance

Every PNG here is a crop of a screenshot that the GUI's own Playwright tests
take, in mock mode, from invented data. None is edited by hand. The claim each
one carries is the caption where [`gui/README.md`](../../README.md) or
[`UX.md`](../UX.md#adjusted-keys-adr-0038) shows it.

## How they were made

Captured on 2026-10-07 at commit `52c1eb6` (`interim-evaluation-and-gui`);
for the files the table marks `9babc22`, on 2026-10-13 at that commit
(`task/gui-adr0038-adjustments`, branched from `interim-evaluation-and-gui` at
`0c3a4bd`); and for those it marks `8d06292`, on 2026-10-22 at that commit
(`feat/config-record-model-launch`). Each time Playwright 1.63.0 drove the
installed Google Chrome 150 (`channel: "chrome"`), dark colour scheme:

```bash
cd gui
npm ci && npm run build
GUI_SHOTS_DIR=/path/to/scratch npx playwright test -g screenshots --workers=2
```

- **Mock mode.** Playwright's web server is the real BFF started with
  `DATA_SOURCE=mock` ([`playwright.config.ts`](../../playwright.config.ts)
  `webServer.env`), so every value on screen comes from `packages/mock`:
  invented or public thelook-shaped names, aliased table names for the
  adjusted-keys launch, e-mails at `example.com`, no BigQuery access.
- **Viewports.** Project `desktop` is 1440 × 900 at device scale 1; project
  `mobile` is 390 × 844 at device scale 2, so its PNGs are 780 px wide.
- **The screenshot tests** are the `screenshots of …` tests in
  `e2e/{intro,evaluation,rag,config,smoke}.spec.ts`. They skip unless
  `GUI_SHOTS_DIR` is set; `smoke` and `config` also append `GUI_SHOTS_SUFFIX`
  to their file names.
- **Crop and compression.** Each capture below was cropped to the box given
  and reduced to a 256-colour palette with Pillow (from the root uv
  workspace env), no dithering:

  ```python
  from PIL import Image
  im = Image.open(src).convert("RGB").crop(box)  # box = (left, top, right, bottom); none for a full frame
  im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(out, optimize=True)
  ```

## Files

| File                                | Captured by (test → file)                                  | Crop box (px)     | Captured at | Shows                                                                                             |
| :---------------------------------- | :--------------------------------------------------------- | :---------------- | :---------- | :------------------------------------------------------------------------------------------------ |
| `evaluation-run-1440.png`           | `evaluation.spec.ts` → `evaluation-run-1440.png`           | 0, 0, 1440, 1650  | `8d06292`   | the run view of `eval-0039`: header, tables in scope, the model summary card, scorecards          |
| `evaluation-compare-1440.png`       | `evaluation.spec.ts` → `evaluation-compare-1440.png`       | 0, 0, 1440, 1750  | `8d06292`   | `eval-0005` · `eval-0035` · `eval-0037` compared, with the "not directly comparable" notice       |
| `evaluation-run-390.png`            | `evaluation.spec.ts` (mobile) → `evaluation-run-390.png`   | 0, 0, 780, 3205   | `8d06292`   | the same run view at phone width, down to the model summary card                                  |
| `evaluation-adjusted-keys-1440.png` | `evaluation.spec.ts` → `evaluation-adjusted-keys-1440.png` | — (element shot)  | `9babc22`   | the adjusted-keys strip of `eval-0041` (Relational tab): source share, tolerance, synthetic share |
| `intro-1440.png`                    | `intro.spec.ts` → `intro-1440-dark-hero.png`               | — (viewport shot) | `8d06292`   | the INTRO hero and the nine-step pipeline tour                                                    |
| `rag-explorer-1440.png`             | `rag.spec.ts` → `rag-explorer-3d-1440.png`                 | — (element shot)  | `52c1eb6`   | the 3-D vector space after the query "country is Brasil,"                                         |
| `config-amp-1440.png`               | `config.spec.ts` → `config-amp-1440.png`                   | 0, 0, 1440, 1400  | `52c1eb6`   | the Pipeline amp: meter bridge, SAMPLING and GENERATION channels                                  |
| `config-scenario-1440.png`          | `config.spec.ts` → `config-scenario-90m-1440.png`          | 0, 0, 1440, 2310  | `52c1eb6`   | the scenario calculator on the 90M-from-1M preset, the fidelity maths and a "Docs differ" note    |

Why three files were taken again at `9babc22`: that change added one launch
and its two evaluations to the mock (ADR 0038,
[`packages/mock/src/adjusted.ts`](../../packages/mock/src/adjusted.ts)).
`evaluation-adjusted-keys-1440.png` is new. `intro-1440.png` counts the
evaluations on its last step (40, now 42), and `evaluation-compare-1440.png`
draws every other evaluation as a grey point behind the compared ones (two
more points). The two run views of `eval-0039`, taken again at `9babc22` and
put through the same crop and palette, are pixel-identical to the files here,
so they keep their capture.

Why four files were taken again at `8d06292`: the whole-model launches added
`eval-0043` to the mock (ADR 0045, [`packages/mock/src/forest.ts`](../../packages/mock/src/forest.ts)),
so `intro-1440.png` now counts 43 evaluations and `evaluation-compare-1440.png`
draws it among the grey points; and the run view of `eval-0039` (three tables) gained
the model summary card between the headline numbers and the scorecards, so
both run-view crops grew to keep the scorecards (1440 px) or the card (390 px)
in frame. The top bar of these four now also carries the "Built with" links
(the Apache Beam logo and "Dataflow" as text), covered by
[`ATTRIBUTION.md`](../../ATTRIBUTION.md); the INTRO strip shows the Beam and
vLLM logos it lists too.

The numbers in `evaluation-adjusted-keys-1440.png` are the mock's: per table,
the landed rows, the distinct declared-key tuples and the source's key-repeat
share are typed once, in the `LAUNCH` block of `adjusted.ts`; the shares, the
differences and the verdicts on screen are derived from them.

## What else is on screen

- The top navigation links to this repository and to the Dataflow Solution
  Guides on GitHub.
- None of these crops includes the footer (with its Apache Beam™ notice),
  the Beam mascot or a Google Cloud icon. A future crop that does is covered
  by [`ATTRIBUTION.md`](../../ATTRIBUTION.md).
- The CONFIG crops show knob values exported from the code at the commit
  stamped on the amp (`values from the code at …`), not at the capture
  commit.

Regenerate when a view changes shape: re-run the command above, re-crop with
the same boxes (adjust a box if the layout moved, and update this table), and
keep each file under 300 KB.
