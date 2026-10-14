/**
 * The brand-pack mechanism (vite-plugins/brandPacks.ts), with the committed
 * brand and the fictitious "acme" fixture: discovery, the checks a pack must
 * pass, how the default is chosen, and what index.html gets.
 */
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  brandCss,
  brandRegistry,
  discoverBrandPacks,
  injectBrandHead,
  packRoots,
  readBrandPack,
} from "../../vite-plugins/brandPacks";
import { BRAND_HEAD_ID, type BrandHead } from "./types";

function webRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "apps", "web", "index.html"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("gui/ not found above the working directory");
    dir = parent;
  }
  return join(dir, "apps", "web");
}

const WEB = webRoot();
const COMMITTED = packRoots(WEB)[0]!;
const FIXTURES = join(WEB, "src", "test", "fixtures", "brands");
const INDEX_HTML = readFileSync(join(WEB, "index.html"), "utf8");

const temps: string[] = [];
afterEach(() => {
  for (const dir of temps.splice(0)) rmSync(dir, { recursive: true, force: true });
});

/** A root holding one copy of the acme fixture, its brand.json edited by `edit`. */
function acmeVariant(edit: (pack: Record<string, unknown>) => void, folder = "acme"): string {
  const root = mkdtempSync(join(tmpdir(), "brand-packs-"));
  temps.push(root);
  cpSync(join(FIXTURES, "acme"), join(root, folder), { recursive: true });
  const file = join(root, folder, "brand.json");
  const pack = JSON.parse(readFileSync(file, "utf8")) as Record<string, unknown>;
  edit(pack);
  writeFileSync(file, JSON.stringify(pack));
  return join(root, folder);
}

const headOf = (html: string): BrandHead =>
  JSON.parse(
    new DOMParser().parseFromString(html, "text/html").getElementById(BRAND_HEAD_ID)!.textContent,
  ) as BrandHead;

describe("discovery", () => {
  it("finds the committed brand alone where no other pack exists", () => {
    expect(discoverBrandPacks([COMMITTED]).map((p) => p.id)).toEqual(["beam-dataflow"]);
  });

  it("reads a pack's name, colours, logo and overrides", () => {
    const acme = readBrandPack(join(FIXTURES, "acme"));
    expect(acme).toMatchObject({ id: "acme", name: "Acme Analytics", isDefault: true });
    expect(acme.themeColor).toEqual({ dark: "#0f1d1d", light: "#0f3b3a" });
    expect(acme.tokens.light["--nav-bg"]).toBe("#0f3b3a");
    expect(acme.tokens.dark["--accent-soft"]).toBe("rgb(47 191 173 / 0.14)");
    expect(acme.nav.light["--text-1"]).toBe("#ffffff");
    expect(acme.logo?.light).toBe(join(FIXTURES, "acme", "acme-on-dark.svg"));
  });

  it("skips a root that does not exist and refuses one id in two folders", () => {
    expect(discoverBrandPacks([join(WEB, "no-such-root"), COMMITTED]).map((p) => p.id)).toEqual(["beam-dataflow"]);
    const twin = acmeVariant(() => {});
    expect(() => discoverBrandPacks([FIXTURES, dirname(twin)])).toThrow(/id "acme" is also used by/);
  });
});

type Pack = Record<string, unknown>;
type Edit = (pack: Pack) => void;
const modeMap = (pack: Pack, key: "tokens" | "nav", mode: "dark" | "light") =>
  (pack[key] as Record<string, Record<string, string>>)[mode]!;

const REFUSALS: ReadonlyArray<[string, Edit, RegExp]> = [
  [
    "it has an unknown key",
    (p) => {
      p.colour = "#ffffff";
    },
    /unknown key "colour"/,
  ],
  [
    "its id is not its folder's name",
    (p) => {
      p.id = "acme-two";
    },
    /must match its folder/,
  ],
  [
    "a colour is not #rrggbb or rgb(r g b / a)",
    (p) => {
      p.themeColor = { dark: "teal", light: "#ffffff" };
    },
    /not a colour/,
  ],
  [
    "it sets a chart slot",
    (p) => {
      modeMap(p, "tokens", "dark")["--chart-1"] = "#123456";
    },
    /"--chart-1" is not a token/,
  ],
  [
    "it sets a status hue",
    (p) => {
      modeMap(p, "tokens", "light")["--status-good"] = "#123456";
    },
    /"--status-good" is not a token/,
  ],
  [
    "its top bar sets the page colour",
    (p) => {
      modeMap(p, "nav", "light")["--bg"] = "#123456";
    },
    /"--bg" is not a token/,
  ],
  [
    "a mode is neither dark nor light",
    (p) => {
      p.tokens = { dim: {} };
    },
    /unknown mode "dim"/,
  ],
  [
    "a logo is outside its folder",
    (p) => {
      p.logo = { light: "../x.svg", dark: "x.svg" };
    },
    /file name inside the pack/,
  ],
  [
    "a logo is missing",
    (p) => {
      p.logo = { light: "gone.svg", dark: "gone.svg" };
    },
    /gone.svg is not in/,
  ],
];

describe("a pack is refused when", () => {
  it.each(REFUSALS)("%s", (_case, edit, message) => {
    expect(() => readBrandPack(acmeVariant(edit))).toThrow(message);
  });

  it("an SVG logo carries a script or an external reference", () => {
    for (const body of [
      '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
      '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>',
      '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
    ]) {
      const dir = acmeVariant(() => {});
      writeFileSync(join(dir, "acme-on-dark.svg"), body);
      expect(() => readBrandPack(dir)).toThrow(/script, an event handler or an external reference/);
    }
  });
});

describe("the default brand", () => {
  const committed = () => discoverBrandPacks([COMMITTED]);
  const withAcme = () => discoverBrandPacks([COMMITTED, FIXTURES]);

  it("is the committed brand when no pack claims it", () => {
    expect(brandRegistry(committed()).default).toBe("beam-dataflow");
  });

  it('is a pack marked "default": true, listed after the committed brand', () => {
    const registry = brandRegistry(withAcme());
    expect(registry.default).toBe("acme");
    expect(registry.packs.map((p) => p.id)).toEqual(["beam-dataflow", "acme"]);
  });

  it("is VITE_DEFAULT_BRAND when set, over a pack's own claim", () => {
    expect(brandRegistry(withAcme(), "beam-dataflow").default).toBe("beam-dataflow");
    expect(() => brandRegistry(withAcme(), "nope")).toThrow(
      /VITE_DEFAULT_BRAND names no brand pack \(found: beam-dataflow, acme\)/,
    );
  });

  it("cannot be claimed by two packs", () => {
    const second = acmeVariant((p) => {
      p.id = "acme-two";
    }, "acme-two");
    expect(() => brandRegistry([...withAcme(), readBrandPack(second)])).toThrow(
      /more than one pack says "default": true/,
    );
  });

  it("needs the committed brand", () => {
    expect(() => brandRegistry(discoverBrandPacks([FIXTURES]))).toThrow(/committed brand is missing/);
  });
});

describe("index.html", () => {
  it("with no other pack, gets the committed brand alone and no token overrides", () => {
    const registry = brandRegistry(discoverBrandPacks([COMMITTED]));
    expect(brandCss(registry.packs)).toBe("");
    const html = injectBrandHead(INDEX_HTML, registry);
    expect(html).not.toContain('id="brand-tokens"');
    expect(headOf(html)).toEqual({
      default: "beam-dataflow",
      packs: [{ id: "beam-dataflow", name: "Beam · Dataflow", themeColor: { dark: "#0b0d12", light: "#f4f5f7" } }],
    });
    expect(html).toContain('<html lang="en" data-theme="dark" data-brand="beam-dataflow">');
    expect(html).toContain('<meta name="theme-color" content="#0b0d12" />');
  });

  it("with a default pack, gets its list, tokens and colour ahead of theme-init.js", () => {
    const html = injectBrandHead(INDEX_HTML, brandRegistry(discoverBrandPacks([COMMITTED, FIXTURES])));
    const list = html.indexOf(`id="${BRAND_HEAD_ID}"`);
    const style = html.indexOf('<style id="brand-tokens">');
    const init = html.indexOf('<script src="/theme-init.js"></script>');
    expect(list).toBeGreaterThan(-1);
    expect(style).toBeGreaterThan(list);
    expect(init).toBeGreaterThan(style);
    expect(html).toContain('data-brand="acme"');
    expect(html).toContain('<meta name="theme-color" content="#0f1d1d" />');
    expect(html).toContain(':root[data-brand="acme"][data-theme="light"]{--bg:#f3f6f6;');
    expect(html).toContain(':root[data-brand="acme"][data-theme="light"] [data-app-header]{--text-1:#ffffff;');

    const head = headOf(html);
    expect(head.default).toBe("acme");
    const acme = head.packs.find((p) => p.id === "acme")!;
    expect(acme.logo?.light).toMatch(/^data:image\/svg\+xml;base64,/);
    // No path of the machine that built it reaches the page.
    expect(JSON.stringify(head)).not.toContain(WEB);
  });

  it.each(["Acme </script>", "Acme $& $1 $'"])(
    "writes the name %s as it is, and nothing that closes the block",
    (name) => {
      const dir = acmeVariant((p) => {
        p.name = name;
      });
      const html = injectBrandHead(INDEX_HTML, brandRegistry([...discoverBrandPacks([COMMITTED]), readBrandPack(dir)]));
      expect(html).not.toContain("Acme </script>");
      expect(html.match(/<script src="\/theme-init\.js"><\/script>/g)).toHaveLength(1);
      expect(headOf(html).packs.map((p) => p.name)).toContain(name);
    },
  );

  it("refuses a page without theme-init.js", () => {
    expect(() => injectBrandHead("<html><head></head></html>", brandRegistry(discoverBrandPacks([COMMITTED])))).toThrow(
      /theme-init\.js/,
    );
  });
});
