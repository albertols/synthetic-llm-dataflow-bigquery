import { describe, expect, it } from "vitest";

import { ASSETS, buildProvenance, designGenerators, scriptThatWrites } from "./assets-sync.mjs";

describe("assets-sync: which script draws a figure", () => {
  it("maps every figure of a DESIGN.md §10 row to the row's script, one figure or several", () => {
    const map = designGenerators(
      [
        "| Figure | Source | Kind |",
        "| :-- | :-- | :-- |",
        "| `assets/overview.png` | `assets/overview.drawio` (draw.io export) | architecture |",
        "| `designs/assets/one.png` | `scripts/doc/make_one.py` | concept |",
        "| `designs/assets/two-a.png`, `designs/assets/two-b.gif` | `scripts/doc/make_two.py` | concept |",
      ].join("\n"),
    );
    expect(Object.fromEntries(map)).toEqual({
      "docs/assets/overview.png": "docs/assets/overview.drawio",
      "docs/designs/assets/one.png": "scripts/doc/make_one.py",
      "docs/designs/assets/two-a.png": "scripts/doc/make_two.py",
      "docs/designs/assets/two-b.gif": "scripts/doc/make_two.py",
    });
  });

  it("finds a script that names a figure by its stem and adds the extension itself", () => {
    const scripts = {
      "scripts/doc/make_a.py": 'fig.savefig(ASSETS / "plain-name.png")',
      "scripts/doc/make_b.py": '_save(fig, "built-name", seed=SEED)  # f"{name}.png"',
    };
    const read = (/** @type {string} */ script) => scripts[/** @type {keyof typeof scripts} */ (script)];
    const names = Object.keys(scripts);
    expect(scriptThatWrites("docs/designs/assets/plain-name.png", names, read)).toBe("scripts/doc/make_a.py");
    expect(scriptThatWrites("docs/designs/assets/built-name.png", names, read)).toBe("scripts/doc/make_b.py");
    expect(scriptThatWrites("docs/designs/assets/unknown.png", names, read)).toBeUndefined();
  });

  it("gives every figure of DESIGN.md's table its row's script, the two-figure row included", () => {
    const fromDesign = designGenerators();
    // The row that lists two figures in one cell (the evaluation's concept figures).
    expect(fromDesign.get("docs/designs/assets/eval-levels.png")).toBe("scripts/doc/make_eval_figures.py");
    expect(fromDesign.get("docs/designs/assets/eval-noise-floor.png")).toBe("scripts/doc/make_eval_figures.py");
    const { assets } = buildProvenance();
    expect(assets.map((a) => a.source)).toEqual(ASSETS);
    // No figure of that table is left without the generator its row names.
    const mapped = assets.filter((a) => fromDesign.has(a.source));
    expect(mapped.length).toBeGreaterThan(5);
    for (const asset of mapped) expect(asset.generator, asset.file).toBe(fromDesign.get(asset.source));
  });

  it("finds the evaluation figures' script, which names them by stem", () => {
    const byFile = new Map(buildProvenance().assets.map((a) => [a.file, a.generator]));
    for (const file of ["eval-noise-floor.png", "eval-dcr-nndr.png", "eval-ks-vs-wasserstein.png"])
      expect(byFile.get(file), file).toBe("scripts/doc/make_eval_figures.py");
  });
});
