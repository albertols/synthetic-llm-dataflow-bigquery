/**
 * Brand × mode end to end (gui/BRANDING.md, "Brand packs"), against the real
 * BFF in mock mode, desktop 1440 px and mobile 390 px. The brands are the ones
 * this build carries (the committed brand, plus any pack on the machine that
 * built it), read from the page's own `#brand-packs` list.
 *
 * Checks: the switcher is a labelled menu that works from the keyboard; the
 * stored brand and theme are on <html>, and painted, before the app's code
 * runs; an unknown stored brand falls back to the default; the "built with"
 * marks link to their projects; and every brand, in both modes, passes axe
 * (WCAG 2.2 AA, no serious or critical violation) on INTRO and EVALUATION
 * without scrolling sideways.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Head = { default: string; packs: Array<{ id: string; name: string }> };

const THEME_KEY = "synthetic-platform.theme";
const BRAND_KEY = "synthetic-platform.brand";
const isMobile = (projectName: string) => projectName === "mobile";

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function expectNoSeriousViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => `${label}: ${violation.id} → ${violation.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious).toEqual([]);
}

async function readHead(page: Page): Promise<Head> {
  return page.evaluate(() => JSON.parse(document.getElementById("brand-packs")!.textContent) as Head);
}

/** Stores a brand and mode, then loads `path`: theme-init.js applies them before the first paint. */
async function openAs(page: Page, path: string, brand: string, mode: "dark" | "light") {
  await page.evaluate(
    ([brandKey, themeKey, b, m]) => {
      window.localStorage.setItem(brandKey, b);
      window.localStorage.setItem(themeKey, m);
    },
    [BRAND_KEY, THEME_KEY, brand, mode] as const,
  );
  await page.goto(path);
  await expect(page.locator("html")).toHaveAttribute("data-brand", brand);
  await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
}

function switcher(page: Page, projectName: string) {
  return page.getByRole("button", {
    name: isMobile(projectName) ? "More: brand, theme and links" : "Brand and theme",
  });
}

/** Scrolls the page through so lazy figures and diagrams render, then returns to the top. */
async function renderEverything(page: Page) {
  const height = await page.evaluate(() => document.body.scrollHeight);
  for (let y = 0; y < height; y += 600) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(60);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

test("the brand and theme switcher is two labelled radio groups and works from the keyboard", async ({
  page,
}, testInfo) => {
  const errors = watchErrors(page);
  await page.goto("/evaluation");
  const head = await readHead(page);
  await expect(page.locator("html")).toHaveAttribute("data-brand", head.default);

  const trigger = switcher(page, testInfo.project.name);
  await trigger.focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  const brands = menu.getByRole("group", { name: "Brand" });
  await expect(brands.getByRole("menuitemradio")).toHaveText(head.packs.map((p) => p.name));
  const defaultName = head.packs.find((p) => p.id === head.default)!.name;
  await expect(brands.getByRole("menuitemradio", { name: defaultName, exact: true })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  const modes = menu.getByRole("group", { name: "Theme" });
  await expect(modes.getByRole("menuitemradio")).toHaveText(["Dark", "Light", "Match system"]);
  await expectNoSeriousViolations(page, "switcher open");

  // Arrow keys move through the items; Enter picks "Light" and closes the menu.
  const light = modes.getByRole("menuitemradio", { name: "Light", exact: true });
  for (let i = 0; i < 12 && !(await light.evaluate((el) => el === document.activeElement)); i += 1)
    await page.keyboard.press("ArrowDown");
  await expect(light).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(menu).toBeHidden();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(trigger).toBeFocused();

  // Every brand, picked from the menu, lands on <html> and survives a reload.
  for (const pack of head.packs) {
    await trigger.click();
    await page.getByRole("menuitemradio", { name: pack.name, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-brand", pack.id);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  }
  const last = head.packs.at(-1)!;
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-brand", last.id);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(errors).toEqual([]);
});

test("the stored brand and theme are painted before the app's code runs", async ({ page }) => {
  await page.goto("/evaluation");
  const head = await readHead(page);
  const brand = head.packs.at(-1)!.id;
  // With the app's code blocked, only theme-init.js can stamp <html>: no flash of the default.
  await page.route("**/assets/*.js", (route) => route.abort());
  await page.evaluate(
    ([brandKey, themeKey, b]) => {
      window.localStorage.setItem(brandKey, b);
      window.localStorage.setItem(themeKey, "light");
    },
    [BRAND_KEY, THEME_KEY, brand] as const,
  );
  await page.goto("/evaluation", { waitUntil: "load" });
  await expect(page.locator("html")).toHaveAttribute("data-brand", brand);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const painted = await page.evaluate(() => ({
    page: getComputedStyle(document.body).backgroundColor,
    token: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
  }));
  const hex = (rgb: string) =>
    `#${(rgb.match(/\d+/g) ?? [])
      .slice(0, 3)
      .map((n) => Number(n).toString(16).padStart(2, "0"))
      .join("")}`;
  expect(hex(painted.page)).toBe(painted.token);
});

test("an unknown stored brand falls back to the build's default", async ({ page }) => {
  await page.goto("/evaluation");
  const head = await readHead(page);
  await openAs(page, "/evaluation", head.default, "dark");
  await page.evaluate((key) => window.localStorage.setItem(key, "a-pack-since-removed"), BRAND_KEY);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-brand", head.default);
});

test("the Apache Beam and Dataflow marks link to their projects", async ({ page }, testInfo) => {
  await page.goto("/evaluation");
  const mobile = isMobile(testInfo.project.name);
  if (mobile) await switcher(page, testInfo.project.name).click();
  const scope = mobile ? page.getByRole("menu") : page.getByRole("banner").getByRole("group", { name: "Built with" });
  const role = mobile ? "menuitem" : "link";
  await expect(scope.getByRole(role, { name: /^Apache Beam/ })).toHaveAttribute("href", "https://beam.apache.org/");
  await expect(scope.getByRole(role, { name: /^Google Cloud Dataflow/ })).toHaveAttribute(
    "href",
    "https://cloud.google.com/dataflow",
  );
});

test("every brand, in both modes, passes axe on INTRO and EVALUATION and never scrolls sideways", async ({ page }) => {
  test.setTimeout(420_000);
  const errors = watchErrors(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/evaluation");
  const head = await readHead(page);
  for (const pack of head.packs)
    for (const mode of ["dark", "light"] as const) {
      for (const path of ["/", "/evaluation"]) {
        await openAs(page, path, pack.id, mode);
        await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
        if (path === "/") await renderEverything(page);
        else await page.waitForLoadState("networkidle");
        await expectNoSeriousViolations(page, `${pack.id} ${mode} ${path}`);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `${pack.id} ${mode} ${path}`).toBeLessThanOrEqual(0);
      }
    }
  expect(errors).toEqual([]);
});

test("screenshots of each brand (set GUI_SHOTS_DIR to capture)", async ({ page }, testInfo) => {
  const dir = process.env.GUI_SHOTS_DIR;
  test.skip(!dir, "GUI_SHOTS_DIR not set");
  test.setTimeout(240_000);
  const mobile = isMobile(testInfo.project.name);
  const width = mobile ? 390 : 1440;
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/evaluation");
  const head = await readHead(page);
  for (const pack of head.packs)
    for (const mode of ["dark", "light"] as const) {
      const stem = `${dir}/brand-${pack.id}-${mode}`;
      await openAs(page, "/", pack.id, mode);
      const pipeline = page.locator("#pipeline");
      await pipeline.scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(1200);
      const box = (await pipeline.boundingBox())!;
      await page.screenshot({
        path: `${stem}-intro-${width}.png`,
        clip: { x: 0, y: 0, width, height: Math.ceil(box.y + box.height + 24) },
        fullPage: true,
      });
      await openAs(page, "/evaluation", pack.id, mode);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(800);
      await page.screenshot({ path: `${stem}-evaluation-${width}.png` });
      if (mobile) {
        await switcher(page, testInfo.project.name).click();
        await expect(page.getByRole("menu")).toBeVisible();
        await page.screenshot({ path: `${stem}-menu-${width}.png` });
        await page.keyboard.press("Escape");
      }
    }
  if (!mobile) {
    // The default brand's EVALUATION landing, light, 1200 px wide.
    await page.setViewportSize({ width: 1200, height: 760 });
    await openAs(page, "/evaluation", head.default, "light");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${dir}/brand-${head.default}-light-evaluation-1200.png` });
  }
});
