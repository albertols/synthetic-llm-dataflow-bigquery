/**
 * public/theme-init.js stamps the theme before the first paint (no dark flash
 * for a light-theme reader); lib/theme.ts owns the same key and colours. This
 * runs the script in jsdom and checks it agrees with theme.ts.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { readStoredPreference, resolveTheme, THEME_COLOR, THEME_STORAGE_KEY } from "./theme";

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
  document.head.innerHTML = "";
});

describe("theme-init.js (before the first paint)", () => {
  it("is loaded by index.html as a classic script, ahead of the app module", () => {
    const html = readFileSync(join(WEB, "index.html"), "utf8");
    const init = html.indexOf('<script src="/theme-init.js"></script>');
    expect(init).toBeGreaterThan(-1);
    expect(init).toBeLessThan(html.indexOf('type="module"'));
    expect(init).toBeLessThan(html.indexOf("</head>"));
  });

  it.each([
    [null, false, "dark"],
    ["dark", true, "dark"],
    ["light", false, "light"],
    ["system", true, "light"],
    ["system", false, "dark"],
    ["garbage", true, "dark"],
  ] as const)("stored %s, OS light %s → %s, the same as theme.ts resolves", (stored, osLight, expected) => {
    prefersLight(osLight);
    if (stored !== null) window.localStorage.setItem(THEME_STORAGE_KEY, stored);
    document.head.innerHTML = '<meta name="theme-color" content="#000000" />';

    runInit();

    expect(document.documentElement.dataset.theme).toBe(expected);
    expect(document.documentElement.style.colorScheme).toBe(expected);
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", THEME_COLOR[expected]);
    expect(resolveTheme(readStoredPreference())).toBe(expected);
  });

  it("falls back to dark when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    runInit();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
