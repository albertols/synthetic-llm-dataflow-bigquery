# GUI screenshots — provenance

Every PNG here is a crop of a screenshot that the GUI's own Playwright tests
take, in mock mode, from invented data. None is edited by hand. The claim each
one carries is the caption where [`gui/README.md`](../../README.md) shows it.

## How they were made

Captured on 2026-10-07 at commit `e832464` (`interim-evaluation-and-gui`),
Playwright 1.63.0 driving the installed Google Chrome 150 (`channel: "chrome"`),
dark colour scheme:

```bash
cd gui
npm ci && npm run build
GUI_SHOTS_DIR=/path/to/scratch npx playwright test -g screenshots --workers=2
```

- **Mock mode.** Playwright's web server is the real BFF started with
  `DATA_SOURCE=mock` ([`playwright.config.ts`](../../playwright.config.ts)
  `webServer.env`), so every value on screen comes from `packages/mock`:
  invented or public thelook-shaped names, e-mails at `example.com`, no
  BigQuery access.
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

| File                          | Captured by (test → file)                                | Crop box (px)     | Shows                                                                                          |
| :---------------------------- | :------------------------------------------------------- | :---------------- | :--------------------------------------------------------------------------------------------- |
| `evaluation-run-1440.png`     | `evaluation.spec.ts` → `evaluation-run-1440.png`         | 0, 0, 1440, 1490  | the run view of `eval-0039`: header, tables in scope, scorecards                               |
| `evaluation-compare-1440.png` | `evaluation.spec.ts` → `evaluation-compare-1440.png`     | 0, 0, 1440, 1750  | `eval-0005` · `eval-0035` · `eval-0037` compared, with the "not directly comparable" notice    |
| `evaluation-run-390.png`      | `evaluation.spec.ts` (mobile) → `evaluation-run-390.png` | 0, 0, 780, 2680   | the same run view at phone width                                                               |
| `intro-1440.png`              | `intro.spec.ts` → `intro-1440-dark-hero.png`             | — (viewport shot) | the INTRO hero and the nine-step pipeline tour                                                 |
| `rag-explorer-1440.png`       | `rag.spec.ts` → `rag-explorer-3d-1440.png`               | — (element shot)  | the 3-D vector space after the query "country is Brasil,"                                      |
| `config-amp-1440.png`         | `config.spec.ts` → `config-amp-1440.png`                 | 0, 0, 1440, 1400  | the Pipeline amp: meter bridge, SAMPLING and GENERATION channels                               |
| `config-scenario-1440.png`    | `config.spec.ts` → `config-scenario-90m-1440.png`        | 0, 0, 1440, 2310  | the scenario calculator on the 90M-from-1M preset, the fidelity maths and a "Docs differ" note |

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
