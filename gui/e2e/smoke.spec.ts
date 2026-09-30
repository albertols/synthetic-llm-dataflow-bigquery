/**
 * Shell smoke — tab-agnostic on purpose: it reads only what the foundation
 * owns (router titles, the tab nav, the route stamp on <main>, /kit, the
 * not-found page), so tab agents can replace every placeholder without
 * touching this file. Each tab adds e2e/<tab>.spec.ts for its own content.
 *
 * Checks: the four tabs navigate with aria-current and page titles; every
 * page has exactly one non-empty h1; deep links and bad search params
 * recover; no console errors; axe finds no serious or critical WCAG 2.2 AA
 * violation; the shared pieces (InfoHint, ChartFrame, theme, mobile header)
 * work in a real browser.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const TABS = [
  { name: "Intro", path: "/", title: "Intro" },
  { name: "Evaluation", path: "/evaluation", title: "Evaluation" },
  { name: "RAG", path: "/rag", title: "RAG" },
  { name: "Config", path: "/config", title: "Config" },
] as const;

/** Console errors and uncaught exceptions, collected for the whole test. */
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
    .map(
      (violation) => `${label}: ${violation.id} → ${violation.nodes.map((node) => node.target.join(" ")).join(" | ")}`,
    );
  expect(serious).toEqual([]);
}

/** The page's single heading: exactly one h1, with text. */
async function expectOnePageHeading(page: Page) {
  const h1 = page.getByRole("heading", { level: 1 });
  await expect(h1).toHaveCount(1);
  await expect(h1).not.toBeEmpty();
}

/** The validated search params of the matched route, from the foundation's stamp on <main>. */
async function routeStamp(page: Page): Promise<{ routeId: string; search: Record<string, unknown> }> {
  const main = page.locator("main#main");
  const routeId = (await main.getAttribute("data-route-id")) ?? "";
  const search = JSON.parse((await main.getAttribute("data-route-search")) ?? "{}") as Record<string, unknown>;
  return { routeId, search };
}

const isMobile = (projectName: string) => projectName === "mobile";

/** Below 640 px the theme switch and GitHub links live in the "More" menu. */
async function switchTheme(page: Page, projectName: string, to: "light" | "dark") {
  const name = `Switch to ${to} theme`;
  if (isMobile(projectName)) {
    await page.getByRole("button", { name: "More: theme and GitHub links" }).click();
    await page.getByRole("menuitem", { name }).click();
  } else {
    await page.getByRole("button", { name }).click();
  }
}

function tabLink(page: Page, name: string) {
  return page.getByRole("navigation", { name: "Tabs" }).getByRole("link", { name, exact: true });
}

test("the four tabs are navigable from the top nav, accessible, and error-free", async ({ page }) => {
  // Four page-level axe passes (INTRO alone is ~2.3k nodes): 45 s ran out on a loaded machine.
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  await page.goto("/");
  await expectOnePageHeading(page);

  for (const tab of TABS) {
    await tabLink(page, tab.name).click();
    await expect(page).toHaveURL(tab.path === "/" ? /\/$/ : new RegExp(`${tab.path}$`));
    await expect(page).toHaveTitle(`${tab.title} · Synthetic Platform`);
    await expect(tabLink(page, tab.name)).toHaveAttribute("aria-current", "page");
    await expectOnePageHeading(page);
    await expectNoSeriousViolations(page, tab.name);
  }
  expect(errors).toEqual([]);
});

test("deep links and malformed search params recover; unknown paths show the not-found page", async ({ page }) => {
  const errors = watchErrors(page);
  const cases = [
    { url: "/?unexpected=1", routeId: "/", title: "Intro", tab: "Intro" },
    { url: "/evaluation?q=%22unterminated", routeId: "/evaluation", title: "Evaluation", tab: "Evaluation" },
    { url: "/evaluation/run-0001", routeId: "/evaluation/$evaluationId", title: "Evaluation run", tab: "Evaluation" },
    {
      url: "/evaluation/compare?ids=not-json",
      routeId: "/evaluation/compare",
      title: "Compare evaluations",
      tab: "Evaluation",
    },
    { url: "/rag?embedder=not-a-real-embedder&table=orders", routeId: "/rag", title: "RAG", tab: "RAG" },
    { url: "/config?knob=%7B%7D&scenario=", routeId: "/config", title: "Config", tab: "Config" },
  ];
  for (const { url, routeId, title, tab } of cases) {
    await page.goto(url);
    await expect(page).toHaveTitle(`${title} · Synthetic Platform`);
    await expectOnePageHeading(page);
    await expect(tabLink(page, tab)).toHaveAttribute("aria-current", "page");
    expect((await routeStamp(page)).routeId).toBe(routeId);
  }

  // The brief fixes /evaluation/compare's `ids` (an array of evaluation ids): it must round-trip.
  await page.goto(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["a", "b"]))}`);
  await expectOnePageHeading(page);
  expect((await routeStamp(page)).search.ids).toEqual(["a", "b"]);

  await page.goto("/no-such-page");
  await expect(page).toHaveTitle("Page not found · Synthetic Platform");
  await expect(page.getByRole("heading", { name: "This page does not exist" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the BFF answers /api/* in mock mode and serves client routes", async ({ request }) => {
  const health = await request.get("/api/health");
  expect(health.ok()).toBe(true);
  expect(health.headers()["x-data-source"]).toBe("mock");
  expect(await health.json()).toMatchObject({ status: "ok", mode: "mock", project: null });
  const page = (await (await request.get("/api/evaluations?limit=2")).json()) as { total: number; items: unknown[] };
  expect(page.total).toBe(40);
  expect(page.items).toHaveLength(2);
  const spa = await request.get("/evaluation/eval-0001");
  expect(spa.ok()).toBe(true);
  expect(spa.headers()["content-type"]).toContain("text/html");
  expect((await request.get("/api/does-not-exist")).status()).toBe(404);
});

test("the shell shows the data source and both GitHub links", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await page.goto("/kit");
  await expect(page.getByRole("banner").getByText("MOCK", { exact: true })).toBeVisible();
  // In the mobile menu the anchors are menu items (role="menuitem"), still real links with an href.
  const mobile = isMobile(testInfo.project.name);
  if (mobile) {
    await page.getByRole("button", { name: "More: theme and GitHub links" }).click();
    await expect(page.getByRole("menu")).toBeVisible();
    await expectNoSeriousViolations(page, "mobile menu");
  }
  const scope = mobile ? page.getByRole("menu") : page.getByRole("banner");
  const role = mobile ? "menuitem" : "link";
  await expect(scope.getByRole(role, { name: /^Repo: synthetic-llm-dataflow-bigquery/ })).toHaveAttribute(
    "href",
    "https://github.com/albertols/synthetic-llm-dataflow-bigquery",
  );
  await expect(scope.getByRole(role, { name: /^DSG: Dataflow Solution Guides/ })).toHaveAttribute(
    "href",
    "https://github.com/GoogleCloudPlatform/dataflow-solution-guides",
  );
  expect(errors).toEqual([]);
});

test("the header stays on one row above the tabs and nothing scrolls sideways", async ({ page }) => {
  await page.goto("/kit");
  const header = page.getByRole("banner");
  const brandBox = await header.getByRole("link", { name: "Synthetic Platform — Intro" }).boundingBox();
  const badgeBox = await header.getByText("MOCK", { exact: true }).boundingBox();
  expect(
    brandBox && badgeBox && Math.abs(brandBox.y + brandBox.height / 2 - (badgeBox.y + badgeBox.height / 2)),
  ).toBeLessThan(8);
  for (const tab of TABS) await expect(tabLink(page, tab.name)).toBeInViewport({ ratio: 1 });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("InfoHint opens on click and Enter, renders KaTeX, and closes on Escape", async ({ page }) => {
  // /kit is the heaviest page after INTRO, and this test runs an axe pass on it.
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  await page.goto("/kit");
  const trigger = page.getByRole("button", { name: "About: Noise floor" }).first();

  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Noise floor" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".katex").first()).toBeVisible();
  await expectNoSeriousViolations(page, "InfoHint open");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();

  await page.keyboard.press("Enter");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
});

test("InfoHint opens on hover", async ({ page }, testInfo) => {
  test.skip(isMobile(testInfo.project.name), "touch devices open hints by tap");
  await page.goto("/kit");
  await page.getByRole("button", { name: "About: Reference baseline" }).hover();
  await expect(page.getByRole("dialog", { name: "Reference baseline" })).toBeVisible();
});

test("the design system renders charts with a table fallback and events, in both themes", async ({
  page,
}, testInfo) => {
  const errors = watchErrors(page);
  await page.goto("/kit");
  const dkw = page.getByRole("figure", { name: "DKW band ε(n) at α = 0.05" });
  await expect(dkw.locator("canvas")).toBeVisible();
  await dkw.getByRole("button", { name: "View data" }).click();
  await expect(dkw.getByRole("table")).toBeVisible();
  await expect(dkw.getByRole("row")).toHaveCount(11);
  await expect(page.getByText("Not evaluated: the reference sample is unverified.")).toBeVisible();

  // ChartFrame onReady + onEvents: the instance dispatches a highlight, the bound handler reports it.
  const bars = page.getByRole("figure", { name: "Order totals: source vs synthetic" });
  await expect(bars.locator("canvas")).toBeVisible();
  await page.getByRole("button", { name: "Highlight 25–50" }).click();
  await expect(page.getByTestId("kit-chart-event")).toContainText("highlight · 25–50");

  await expectNoSeriousViolations(page, "kit dark");
  await switchTheme(page, testInfo.project.name, "light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expectNoSeriousViolations(page, "kit light");
  await switchTheme(page, testInfo.project.name, "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(errors).toEqual([]);
});

test("screenshots of the shell (set GUI_SHOTS_DIR to capture)", async ({ page }, testInfo) => {
  const dir = process.env.GUI_SHOTS_DIR;
  test.skip(!dir, "GUI_SHOTS_DIR not set");
  const suffix = process.env.GUI_SHOTS_SUFFIX ?? "";
  const width = isMobile(testInfo.project.name) ? 390 : 1440;
  const pages = [...TABS.map((tab) => [tab.name.toLowerCase(), tab.path] as const), ["kit", "/kit"] as const];
  for (const [name, path] of pages) {
    await page.goto(path);
    await expectOnePageHeading(page);
    await page.waitForTimeout(path === "/kit" ? 3000 : 600);
    await page.screenshot({ path: `${dir}/shell-${name}-${width}${suffix}.png`, fullPage: true });
  }
  await switchTheme(page, testInfo.project.name, "light");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${dir}/shell-kit-light-${width}${suffix}.png`, fullPage: true });
  await switchTheme(page, testInfo.project.name, "dark");
  await page.getByRole("button", { name: "About: Noise floor" }).first().click();
  await expect(page.getByRole("dialog", { name: "Noise floor" })).toBeVisible();
  await page.screenshot({ path: `${dir}/shell-infohint-${width}${suffix}.png` });
});
