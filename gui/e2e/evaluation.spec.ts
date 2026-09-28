/**
 * EVALUATION tab e2e (real BFF, mock data): filter the list → open a run →
 * open a column drawer → compare two runs, with axe at each step; the
 * 200-column evaluation; the degenerate registry states (RUNNING, SKIPPED,
 * FAILED, reference unverified, unknown id, a single id to compare).
 * Screenshots of the key views when GUI_SHOTS_DIR is set.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function expectNoSeriousViolations(page: Page, label: string, include?: string) {
  let builder = new AxeBuilder({ page }).withTags(TAGS);
  if (include) builder = builder.include(include);
  const results = await builder.analyze();
  const serious = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${label}: ${v.id} → ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious).toEqual([]);
}

async function routeSearch(page: Page): Promise<Record<string, unknown>> {
  return JSON.parse((await page.locator("main#main").getAttribute("data-route-search")) ?? "{}") as Record<
    string,
    unknown
  >;
}

const isMobile = (name: string) => name === "mobile";

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

// Generous timeouts: four worktrees share this machine's CPU during e2e; CI runs one project at a time.
test("filter the list, open a run, open a column drawer, then compare two runs", async ({ page }, testInfo) => {
  test.setTimeout(300_000);
  const errors = watchErrors(page);
  const mobile = isMobile(testInfo.project.name);

  await page.goto("/evaluation");
  await expect(page.getByRole("heading", { level: 1, name: "Evaluations" })).toBeVisible();
  // The first request warms the mock (a few seconds on a busy machine).
  await expect(page.getByRole("heading", { level: 2, name: "40 evaluations" })).toBeVisible({ timeout: 45_000 });
  await expectNoSeriousViolations(page, "list");

  // Filter: engine = b2_library (a multi-select combobox; the filter lives in the URL).
  const engine = page.getByRole("combobox", { name: "Engine" });
  await engine.click();
  await engine.fill("b2");
  await page.getByRole("option", { name: /b2_library/ }).click();
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await routeSearch(page)).engine).toEqual(["b2_library"]);
  await expect(page.getByRole("heading", { level: 2, name: /^\d+ evaluations?$/ })).not.toHaveText("40 evaluations");
  await expect(page.getByRole("button", { name: "Remove filter Engine: b2_library" })).toBeVisible();
  await noHorizontalScroll(page);

  // Pick two runs for compare, then open the first one.
  const picks = page.getByRole("checkbox", { name: /^Pick eval-\d+ for compare$/ }).filter({ visible: true });
  await picks.nth(0).check();
  await picks.nth(1).check();
  await expect.poll(async () => ((await routeSearch(page)).pick as string[] | undefined)?.length).toBe(2);
  const firstRun = page
    .getByRole("link", { name: /^eval-\d{4}$/ })
    .filter({ visible: true })
    .first();
  const runId = (await firstRun.textContent())!.trim();
  const listUrl = page.url();
  await firstRun.click();

  await expect(page.getByRole("heading", { level: 1, name: runId })).toBeVisible();
  await expect(page.getByRole("region", { name: "Tables in scope" })).toBeVisible();
  await expect(page.getByRole("note", { name: "Legend: how metric bars read" })).toBeVisible();
  await expect(page.getByTestId("run-headline")).toContainText("Overall score");
  await expectNoSeriousViolations(page, "run view");

  // Columns tab → the heatmap → a drawer (profiles load on open).
  await page.getByRole("tab", { name: "Columns" }).click();
  const heatmap = page.getByRole("table", { name: /Columns by metric/ });
  await expect(heatmap).toBeVisible();
  const profileRequest = page.waitForRequest((r) => r.url().includes("/profiles?") && r.url().includes("column="));
  await heatmap
    .getByRole("button", { name: /^Open / })
    .first()
    .click();
  await profileRequest;
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: /^All metrics \(\d+\)/ })).toBeVisible();
  await expect(drawer.getByRole("figure").first()).toBeVisible();
  await expectNoSeriousViolations(page, "drawer", '[role="dialog"]');
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  expect((await routeSearch(page)).column).toBeUndefined();

  // Back to the list (the filter and picks live in its URL) → compare.
  await page.goto(listUrl);
  await expect(page.getByRole("heading", { level: 1, name: "Evaluations" })).toBeVisible();
  const compare = page.getByRole("link", { name: "Compare 2" });
  await expect(compare).toBeVisible();
  await compare.click();
  await expect(page.getByRole("heading", { level: 1, name: "Compare evaluations" })).toBeVisible();
  expect(((await routeSearch(page)).ids as string[]).length).toBe(2);
  await expect(page.getByTestId("ab-summary")).toContainText("B against A");
  await expect(page.getByRole("region", { name: "Compared evaluations" })).toBeVisible();
  await expectNoSeriousViolations(page, "compare");
  if (!mobile) await noHorizontalScroll(page);
  expect(errors).toEqual([]);
});

test("a comparison across catalogue and evaluator versions is flagged not comparable", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page);
  await page.goto(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-0005", "eval-0035"]))}`);
  await expect(page.getByText("Not directly comparable")).toBeVisible({ timeout: 45_000 });
  const reasons = page.getByTestId("not-comparable-reasons");
  await expect(reasons).toContainText("catalogue_version differs");
  await expect(reasons).toContainText("evaluator_version differs");
  await expect(page.getByTestId("ab-summary")).toContainText("not comparable");
  await page.getByRole("radio", { name: "Not comparable" }).click();
  await expect(page.locator('tr[data-not-comparable="true"]').first()).toBeVisible();
  await expectNoSeriousViolations(page, "not comparable");
  expect(errors).toEqual([]);
});

test("the 200-column evaluation renders 50 rows at a time and stays responsive", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page);
  const started = Date.now();
  await page.goto("/evaluation/eval-0032?tab=columns");
  const heatmap = page.getByRole("table", { name: /Columns by metric/ });
  await expect(heatmap).toBeVisible({ timeout: 30_000 });
  expect(Date.now() - started).toBeLessThan(30_000);
  await expect(heatmap.getByRole("rowheader")).toHaveCount(50);
  await expect(page.getByText(/200 of 200 columns/)).toBeVisible();
  await page.getByRole("button", { name: "Show 50 more" }).click();
  await expect(heatmap.getByRole("rowheader")).toHaveCount(100);
  await page.getByRole("switch", { name: "Problems only" }).click();
  await expect.poll(async () => (await routeSearch(page)).problems).toBe(true);
  await noHorizontalScroll(page);
  await expectNoSeriousViolations(page, "wide table");
  expect(errors).toEqual([]);
});

test("degenerate registry states render labelled empty states, never a crash", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page);

  await page.goto("/evaluation/eval-0040");
  await expect(page.getByText("Metrics are still being computed")).toBeVisible();
  await expect(page.getByText("Still running")).toBeVisible();

  await page.goto("/evaluation/eval-0024");
  await expect(page.getByText("No metrics were written")).toBeVisible();
  await expect(page.getByText("Scope needs attention")).toBeVisible();

  await page.goto("/evaluation/eval-0014");
  await expect(page.getByText(/bytes billed would exceed max_bytes_billed/).first()).toBeVisible();

  await page.goto("/evaluation/eval-0027?tab=privacy");
  await expect(page.getByText("Reference not verified").first()).toBeVisible();
  await expect(page.getByText(/privacy metrics not evaluated/)).toBeVisible();

  await page.goto("/evaluation/no-such-evaluation");
  await expect(page.getByText("No evaluation with this id")).toBeVisible();

  await page.goto(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-0039"]))}`);
  await expect(page.getByText("Add one more evaluation")).toBeVisible();

  await page.goto(`/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["nope-1", "nope-2"]))}`);
  await expect(page.getByText("Fewer than two of these evaluations exist")).toBeVisible();

  await expectNoSeriousViolations(page, "degenerate");
  expect(await page.locator("body").textContent()).not.toMatch(/NaN/);
  expect(errors).toEqual([]);
});

test("screenshots of the evaluation tab (set GUI_SHOTS_DIR to capture)", async ({ page }, testInfo) => {
  const dir = process.env.GUI_SHOTS_DIR;
  test.skip(!dir, "GUI_SHOTS_DIR not set");
  test.setTimeout(420_000);
  const width = isMobile(testInfo.project.name) ? 390 : 1440;
  /** Full page with the docked legend at the real bottom: grow the viewport to the page first. */
  const shoot = async (name: string) => {
    await page.waitForTimeout(2500);
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width, height: Math.min(height, 12_000) });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${dir}/evaluation-${name}-${width}.png`, fullPage: true });
    await page.setViewportSize({ width, height: isMobile(testInfo.project.name) ? 844 : 900 });
  };
  await page.goto("/evaluation");
  await expect(page.getByRole("heading", { level: 1, name: "Evaluations" })).toBeVisible();
  await shoot("list");
  await page.goto("/evaluation/eval-0039");
  await expect(page.getByTestId("run-headline")).toBeVisible();
  await shoot("run");
  await page.goto("/evaluation/eval-0039?tab=columns&column=users.created_at");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${dir}/evaluation-drawer-${width}.png` });
  await page.goto("/evaluation/eval-0003?tab=privacy");
  await expect(page.getByText("Risk indicators, not guarantees")).toBeVisible();
  await shoot("privacy");
  await page.goto(
    `/evaluation/compare?ids=${encodeURIComponent(JSON.stringify(["eval-0005", "eval-0035", "eval-0037"]))}`,
  );
  await expect(page.getByTestId("ab-summary")).toBeVisible();
  await shoot("compare");
  await page.goto("/evaluation/eval-0032?tab=columns");
  await expect(page.getByRole("table", { name: /Columns by metric/ })).toBeVisible({ timeout: 30_000 });
  await shoot("wide-table");
});
