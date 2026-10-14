/**
 * public/theme-init.js stamps the brand and theme before the first paint (no
 * default flash for a reader who chose otherwise); lib/theme.ts and
 * lib/brand.ts own the same keys and colours. This runs the script in jsdom,
 * against the head the build writes (vite-plugins/brandPacks.ts, with the
 * committed brand and the "acme" fixture), and checks it agrees with them.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { brandRegistry, discoverBrandPacks, injectBrandHead, packRoots } from "../../vite-plugins/brandPacks";
import { BRAND_STORAGE_KEY, FALLBACK_HEAD, readBrandHead, readStoredBrand, resolveBrand, themeColorFor } from "./brand";
import { readStoredPreference, resolveTheme, THEME_STORAGE_KEY } from "./theme";

function webRoot(): string {
  let dir = process.cwd();
  while (!existsSync(join(dir, "apps", "web", "public", "theme-init.js"))) {
    const parent = dirname(dir);
    if (parent === dir) throw new Error("gui/ not found above the working directory");
    dir = parent;
  }
  return join(dir, "apps", "web");
}

const WEB = webRoot();
const SCRIPT = readFileSync(join(WEB, "public", "theme-init.js"), "utf8");
const INDEX_HTML = readFileSync(join(WEB, "index.html"), "utf8");
const FIXTURES = join(WEB, "src", "test", "fixtures", "brands");

/** The <head> the build writes, with these packs: everything up to theme-init.js. */
function builtHead(roots: string[]): string {
  const html = injectBrandHead(INDEX_HTML, brandRegistry(discoverBrandPacks(roots)));
  const head = html.slice(html.indexOf("<head>") + "<head>".length, html.indexOf('<script src="/theme-init.js">'));
  return head;
}

const COMMITTED_HEAD = builtHead([packRoots(WEB)[0]!]);
const ACME_HEAD = builtHead([packRoots(WEB)[0]!, FIXTURES]);
const BARE_HEAD = '<meta name="theme-color" content="#000000" />';

function runInit() {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval -- runs public/theme-init.js as the browser would
  const init = new Function(SCRIPT) as () => void;
  init();
}

function prefersLight(light: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: light && query === "(prefers-color-scheme: light)",
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-brand");
  document.head.innerHTML = "";
});

describe("theme-init.js (before the first paint)", () => {
  it("is loaded by index.html as a classic script, ahead of the app module", () => {
    const init = INDEX_HTML.indexOf('<script src="/theme-init.js"></script>');
    expect(init).toBeGreaterThan(-1);
    expect(init).toBeLessThan(INDEX_HTML.indexOf('type="module"'));
    expect(init).toBeLessThan(INDEX_HTML.indexOf("</head>"));
  });

  it.each([
    [null, false, "dark"],
    ["dark", true, "dark"],
    ["light", false, "light"],
    ["system", true, "light"],
    ["system", false, "dark"],
    ["garbage", true, "dark"],
  ] as const)("stored theme %s, OS light %s → %s, the same as theme.ts resolves", (stored, osLight, expected) => {
    prefersLight(osLight);
    if (stored !== null) window.localStorage.setItem(THEME_STORAGE_KEY, stored);
    document.head.innerHTML = COMMITTED_HEAD;

    runInit();

    expect(document.documentElement.dataset.theme).toBe(expected);
    expect(document.documentElement.style.colorScheme).toBe(expected);
    expect(resolveTheme(readStoredPreference())).toBe(expected);
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute(
      "content",
      themeColorFor(readBrandHead(), "beam-dataflow", expected),
    );
  });

  it.each([
    ["the committed brand alone, nothing stored", COMMITTED_HEAD, null, "beam-dataflow"],
    ["a pack marked default, nothing stored", ACME_HEAD, null, "acme"],
    ["a pack marked default, the committed brand stored", ACME_HEAD, "beam-dataflow", "beam-dataflow"],
    ["a stored brand whose pack was removed", ACME_HEAD, "removed-pack", "acme"],
    ["a stored brand this build lacks", COMMITTED_HEAD, "acme", "beam-dataflow"],
    ["no brand list at all", BARE_HEAD, "acme", "beam-dataflow"],
    [
      "an unreadable brand list",
      `${BARE_HEAD}<script type="application/json" id="brand-packs">{not json</script>`,
      "acme",
      "beam-dataflow",
    ],
    [
      "a list whose default it does not carry",
      `${BARE_HEAD}<script type="application/json" id="brand-packs">{"default":"x","packs":[]}</script>`,
      null,
      "beam-dataflow",
    ],
  ] as const)("%s → %s, the same as brand.ts resolves", (_case, head, stored, expected) => {
    for (const theme of ["dark", "light"] as const) {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
      if (stored !== null) window.localStorage.setItem(BRAND_STORAGE_KEY, stored);
      document.head.innerHTML = head;

      runInit();

      const brand = resolveBrand(readStoredBrand(), readBrandHead());
      expect(brand).toBe(expected);
      expect(document.documentElement.dataset.brand).toBe(expected);
      expect(document.documentElement.dataset.theme).toBe(theme);
      expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute(
        "content",
        themeColorFor(readBrandHead(), brand, theme),
      );
    }
  });

  it("falls back to the committed brand's colours, the ones brand.json declares", () => {
    document.head.innerHTML = BARE_HEAD;
    runInit();
    expect(readBrandHead()).toBe(FALLBACK_HEAD);
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute(
      "content",
      FALLBACK_HEAD.packs[0]!.themeColor.dark,
    );
  });

  it("falls back to the default brand, dark, when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    document.head.innerHTML = ACME_HEAD;
    runInit();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.dataset.brand).toBe("acme");
  });
});
