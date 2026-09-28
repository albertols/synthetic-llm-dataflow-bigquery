/**
 * Shell smoke: the four tabs are navigable, nothing logs a console error,
 * axe finds no serious or critical WCAG 2.2 AA violation, and the shared
 * pieces (InfoHint, ChartFrame, theme toggle) work in a real browser.
 * Tab agents add e2e/<tab>.spec.ts next to this file.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const TABS = [
  { name: "Intro", path: "/", heading: "How this project makes synthetic data" },
  { name: "Evaluation", path: "/evaluation", heading: "Evaluations" },
  { name: "RAG", path: "/rag", heading: "The retrieval layer, in 384 dimensions" },
  { name: "Config", path: "/config", heading: "Knobs, scenarios and source statistics" },
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
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(TABS[0].heading);

  for (const tab of TABS) {
    await tabLink(page, tab.name).click();
    await expect(page).toHaveURL(tab.path === "/" ? /\/$/ : new RegExp(`${tab.path}$`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(tab.heading);
    await expect(tabLink(page, tab.name)).toHaveAttribute("aria-current", "page");
    await expect(page).toHaveTitle(`${tab.name} · Synthetic Platform`);
    await expectNoSeriousViolations(page, tab.name);
  }
  expect(errors).toEqual([]);
});

test("every tab deep-links, and unknown paths show the not-found page", async ({ page }) => {
  const errors = watchErrors(page);
  for (const tab of TABS) {
    await page.goto(tab.path);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(tab.heading);
  }
  await page.goto("/rag?embedder=not-a-real-embedder");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(TABS[2].heading);
  await page.goto("/evaluation/compare?ids=%5B%22a%22%2C%22b%22%5D");
  await expect(page.getByText("Selected: a, b")).toBeVisible();
  await page.goto("/no-such-page");
  await expect(page.getByRole("heading", { name: "This page does not exist" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the shell shows the data source and both GitHub links", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page.getByText("MOCK", { exact: true })).toBeVisible();
  if (isMobile(testInfo.project.name)) {
    await page.getByRole("button", { name: "More: theme and GitHub links" }).click();
    await expect(page.getByRole("menu")).toBeVisible();
    await expectNoSeriousViolations(page, "mobile menu");
  }
  // In the mobile menu the anchors are menu items (role="menuitem"), still real links with an href.
  const mobile = isMobile(testInfo.project.name);
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
  await page.goto("/evaluation");
  const header = page.getByRole("banner");
  const brand = header.getByRole("link", { name: "Synthetic Platform — Intro" });
  const badge = header.getByText("MOCK", { exact: true });
  const brandBox = await brand.boundingBox();
  const badgeBox = await badge.boundingBox();
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
  test.skip(testInfo.project.name === "mobile", "touch devices open hints by tap");
  await page.goto("/kit");
  await page.getByRole("button", { name: "About: Reference baseline" }).hover();
  await expect(page.getByRole("dialog", { name: "Reference baseline" })).toBeVisible();
});

test("the design system renders charts with a table fallback, in both themes", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await page.goto("/kit");
  const dkw = page.getByRole("figure", { name: "DKW band ε(n) at α = 0.05" });
  await expect(dkw.locator("canvas")).toBeVisible();
  await dkw.getByRole("button", { name: "View data" }).click();
  await expect(dkw.getByRole("table")).toBeVisible();
  await expect(dkw.getByRole("row")).toHaveCount(11);
  await expect(page.getByText("Not evaluated: the reference sample is unverified.")).toBeVisible();
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
  const width = testInfo.project.name === "mobile" ? 390 : 1440;
  for (const [name, path] of [
    ["intro", "/"],
    ["evaluation", "/evaluation"],
    ["rag", "/rag"],
    ["config", "/config"],
    ["kit", "/kit"],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForTimeout(path === "/kit" ? 3000 : 400);
    await page.screenshot({ path: `${dir}/shell-${name}-${width}.png`, fullPage: true });
  }
  await switchTheme(page, testInfo.project.name, "light");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${dir}/shell-kit-light-${width}.png`, fullPage: true });
  await switchTheme(page, testInfo.project.name, "dark");
  await page.goto("/evaluation");
  await page.getByRole("button", { name: "About: Metric status" }).click();
  await expect(page.getByRole("dialog", { name: "Metric status" })).toBeVisible();
  await page.screenshot({ path: `${dir}/shell-infohint-${width}.png` });
});
