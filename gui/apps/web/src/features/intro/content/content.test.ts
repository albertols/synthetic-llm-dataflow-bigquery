/**
 * The INTRO copy is copied, not written: every quote, caption and ADR title
 * must still be in the repository file it came from. When DESIGN.md changes
 * (a claim reworded, a section added), this suite fails and names the card.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { knobs } from "@contracts/generated/knobs";
import { concepts as introConcepts } from "@contracts/concepts/intro";
import { concepts as coreConcepts } from "@contracts/concepts/core";

import { formatCount } from "@/lib/format";

import { ADRS } from "./adrs";
import { DESIGN_SECTIONS } from "./designSections";
import { PACKAGES } from "./packages";
import { SHAPES, SHAPES_LEAD } from "./shapes";
import { STAGES } from "./stages";

/** The repository root: the nearest directory above the test's working directory that holds docs/DESIGN.md. */
function repoRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "docs", "DESIGN.md"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`docs/DESIGN.md not found above ${process.cwd()}`);
    dir = parent;
  }
  return `${dir}/`;
}

const REPO = repoRoot();
const read = (path: string) => readFileSync(`${REPO}${path}`, "utf8");

/** Markdown → the text a reader sees: links and images stripped, emphasis dropped, whitespace collapsed. */
function plainMarkdown(markdown: string): string {
  return markdown
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/^\s*>\s?/gm, " ")
    .replace(/^\s*--\s?/gm, " ")
    .replace(/\s+/g, " ")
    .replace(/(^|[\s(])\*(\S[^*]*?\S)\*(?=[\s).,;:]|$)/g, "$1$2");
}

const loose = (text: string) => text.replaceAll("`", "").toLowerCase();

const DESIGN_RAW = read("docs/DESIGN.md");
const SOURCES = new Map<string, string>();
function source(path: string): string {
  if (!SOURCES.has(path)) SOURCES.set(path, plainMarkdown(read(path)));
  return SOURCES.get(path)!;
}

/** GitHub's heading anchor. */
function slug(heading: string): string {
  return heading
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s/g, "-");
}

describe("How it works cards", () => {
  it("has one card per DESIGN.md section, with the heading and anchor it cites", () => {
    const headings = [...DESIGN_RAW.matchAll(/^## (\d+)\. (.+)$/gm)].map((m) => ({ n: Number(m[1]), title: m[2]! }));
    for (const heading of headings) {
      const card = DESIGN_SECTIONS.find((section) => section.number === heading.n);
      expect(card, `§${heading.n} "${heading.title}" has no card`).toBeDefined();
      expect(card?.title).toBe(heading.title);
      expect(card?.anchor).toBe(slug(`${heading.n}. ${heading.title}`));
    }
    for (const card of DESIGN_SECTIONS) {
      expect(
        headings.some((h) => h.n === card.number),
        `§${card.number} is not in DESIGN.md`,
      ).toBe(true);
    }
    expect(DESIGN_SECTIONS.map((s) => s.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  });

  it("quotes every claim, lead and caption verbatim", () => {
    for (const card of DESIGN_SECTIONS) {
      for (const quote of [card.quote, ...(card.more ?? [])]) {
        const text = quote.source === "DESIGN.md" ? source("docs/DESIGN.md") : source(quote.source);
        expect(text, `§${card.number} ${quote.kind}`).toContain(quote.text);
        if (quote.kind === "claim") expect(text).toContain(`Claim: ${quote.text}`);
      }
      if (card.visual.kind === "image" && card.visual.caption) {
        expect(source("docs/DESIGN.md"), `§${card.number} caption`).toContain(card.visual.caption);
      }
      if (card.visual.kind === "mermaid") {
        expect(DESIGN_RAW, `§${card.number} diagram`).toContain(card.visual.chart);
        if (card.visual.caption) expect(source("docs/DESIGN.md")).toContain(card.visual.caption);
      }
    }
  });

  it("shows only figures that assets:sync copied, each with a provenance entry", () => {
    const provenance = JSON.parse(read("gui/apps/web/public/assets/provenance.json")) as {
      assets: Array<{ file: string }>;
    };
    const files = new Set(provenance.assets.map((a) => a.file));
    for (const card of DESIGN_SECTIONS) {
      if (card.visual.kind !== "image") continue;
      expect(files.has(card.visual.file), card.visual.file).toBe(true);
      expect(existsSync(`${REPO}gui/apps/web/public/assets/${card.visual.file}`)).toBe(true);
    }
  });

  it("carries DESIGN.md §9's ADR map exactly", () => {
    const map = DESIGN_RAW.split("<!-- adr-map:start -->")[1]!.split("<!-- adr-map:end -->")[0]!;
    const rows = [...map.matchAll(/^\| (\d{4}) \| \[§(\d+) [^\]]*\]\([^)]*\) \| \[(.*)\]\(adr\/([^)]*)\) \|$/gm)].map(
      (m) => ({ number: m[1], section: Number(m[2]), title: m[3], file: m[4] }),
    );
    expect(rows.length).toBeGreaterThan(30);
    expect(ADRS).toEqual(rows);
  });
});

describe("pipeline stages", () => {
  it("quotes each stage verbatim from the file it names", () => {
    for (const stage of STAGES) {
      expect(source(stage.quote.source), stage.id).toContain(stage.quote.text);
    }
  });

  it("prints knob defaults from the contracts, never typed numbers", () => {
    const value = (id: string) => knobs.knobs.find((k) => k.id === id)!.value as number;
    const chips = (id: string) => STAGES.find((s) => s.id === id)!.chips({});
    expect(chips("reference")).toContain(`${formatCount(value("reference_rows_limit"))} rows`);
    expect(chips("rag")).toContain(`${formatCount(value("max_row_doc_rows"))} chunks`);
    expect(chips("rag")).toContain(`${value("hashing_embedder_dim")}-d vectors`);
    expect(chips("rag")).toContain(`IndexFlatIP · top-${value("rag_top_k")}`);
    expect(chips("generate")).toContain(`pool ≤ ${formatCount(value("free_text_pool_max"))}`);
  });

  it("walks the brief's order: source → sample → stats → RAG → L4 → Mode A → landing → evaluation → registry", () => {
    expect(STAGES.map((s) => s.id)).toEqual([
      "source",
      "reference",
      "stats",
      "rag",
      "generate",
      "mode-a",
      "land",
      "evaluate",
      "registry",
    ]);
  });
});

describe("package map and shapes", () => {
  it("quotes each package description from its manifest or the README", () => {
    for (const pkg of PACKAGES) {
      const text = pkg.descriptionSource.endsWith(".md")
        ? source(pkg.descriptionSource)
        : read(pkg.descriptionSource).replace(/\s+/g, " ");
      expect(text, pkg.id).toContain(pkg.description);
    }
  });

  it("quotes DESIGN.md §4.2 and the README where a shape caption says so", () => {
    expect(source("docs/DESIGN.md")).toContain(SHAPES_LEAD);
    const chain = SHAPES.find((s) => s.id === "chain")!;
    const tree = SHAPES.find((s) => s.id === "tree")!;
    expect(loose(source("docs/DESIGN.md"))).toContain(loose(chain.caption));
    expect(loose(source("README.md"))).toContain(loose(tree.caption.replace(/\.$/, "")));
  });
});

describe("concept references", () => {
  it("every (i) the page uses is registered", () => {
    const ids = new Set<string>([...introConcepts, ...coreConcepts].map((c) => c.id));
    const used = [
      ...STAGES.map((s) => s.concept),
      ...SHAPES.map((s) => s.concept),
      ...["driving", "implied", "independent", "conditional", "external", "documented", "disabled"].map(
        (role) => `intro:role-${role}`,
      ),
      "intro:claim",
      "intro:adr",
      "intro:figure-provenance",
      "intro:import-direction",
      "intro:relationship-model",
      "intro:counter-runs",
      "intro:counter-evaluations",
      "intro:counter-tables",
      "core:score",
      "core:data-source",
      "core:bytes-estimate",
    ];
    expect(used.filter((id) => !ids.has(id))).toEqual([]);
  });
});
