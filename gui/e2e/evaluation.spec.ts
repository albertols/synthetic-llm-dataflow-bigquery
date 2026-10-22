/**
 * EVALUATION tab e2e (real BFF, mock data): filter the list → open a run →
 * open a column drawer → compare two runs, with axe at each step; the
 * 200-column evaluation; a RUNNING event on both sides of the 24-hour stale
 * rule, with the browser's clock fixed; the degenerate registry states
 * (SKIPPED, FAILED, reference unverified, unknown id, a single id to compare).
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
  // The first request warms the mock (a few seconds on a busy machine). 43: the storyline's 40,
  // the two evaluations of the adjusted-keys launch and the isolated-forest launch's one.
  await expect(page.getByRole("heading", { level: 2, name: "43 evaluations" })).toBeVisible({ timeout: 45_000 });
  await expectNoSeriousViolations(page, "list");

  // Filter: engine = b2_library (a multi-select combobox; the filter lives in the URL).
  const engine = page.getByRole("combobox", { name: "Engine" });
  await engine.click();
  await engine.fill("b2");
  await page.getByRole("option", { name: /b2_library/ }).click();
  await page.keyboard.press("Escape");
  await expect.poll(async () => (await routeSearch(page)).engine).toEqual(["b2_library"]);
  await expect(page.getByRole("heading", { level: 2, name: /^\d+ evaluations?$/ })).not.toHaveText("43 evaluations");
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

// The mock's last evaluation, eval-0040, has a RUNNING event recorded on 2026-09-27 at 18:45 UTC
// and no FINAL event. Whether it reads Running or Stale depends only on the clock, so the two
// tests below fix the browser's clock (Date only: timers keep running) instead of reading the
// real date. The server is untouched: the rule is derived in the browser.
const RUNNING_EVENT = Date.parse("2026-09-27T18:45:00Z");
const HOUR = 3_600_000;

test("a RUNNING event up to 24 hours old reads Running, in the list and on its page", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page);
  await page.clock.setFixedTime(new Date(RUNNING_EVENT + 2 * HOUR));

  await page.goto("/evaluation");
  const row = page.locator('[data-evaluation="eval-0040"]').filter({ visible: true });
  await expect(row.locator('[data-status="RUNNING"]')).toHaveText(/^Running/, { timeout: 45_000 });
  await expect(row.locator('[data-status="STALE"]')).toHaveCount(0);
  await noHorizontalScroll(page);

  await page.goto("/evaluation/eval-0040");
  await expect(page.getByText("Metrics are still being computed")).toBeVisible();
  const header = page.getByRole("region", { name: "Run status and scope" });
  await expect(header.locator('[data-status="RUNNING"]')).toHaveText(/^Running/);
  await expect(header).toContainText("running since 2026-09-27 18:45 UTC");
  const note = page.getByRole("note").filter({ hasText: "Still running" });
  await expect(note).toContainText("Numbers may change: the FINAL event has not been recorded yet");
  await expect(page.locator('[data-status="STALE"]')).toHaveCount(0);
  await expectNoSeriousViolations(page, "running run");
  await noHorizontalScroll(page);
  expect(errors).toEqual([]);
});

test("a RUNNING event more than 24 hours old reads Stale, with the reason on top of its page", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page);
  await page.clock.setFixedTime(new Date(RUNNING_EVENT + 30 * HOUR));

  await page.goto("/evaluation");
  const row = page.locator('[data-evaluation="eval-0040"]').filter({ visible: true });
  await expect(row.locator('[data-status="STALE"]')).toHaveText(/^Stale/, { timeout: 45_000 });
  await expect(row.locator('[data-status="RUNNING"]')).toHaveCount(0);
  await noHorizontalScroll(page);

  await page.goto("/evaluation/eval-0040");
  await expect(page.getByText("No metrics, and no result recorded for more than 24 hours")).toBeVisible();
  const header = page.getByRole("region", { name: "Run status and scope" });
  await expect(header.locator('[data-status="STALE"]')).toHaveText(/^Stale/);
  await expect(header).toContainText("RUNNING event recorded 2026-09-27 18:45 UTC · no FINAL event since");
  const callout = page.getByRole("note").filter({ hasText: "Stale: no result recorded for more than 24 hours" });
  await expect(callout).toContainText("most likely ended without recording a result");
  await expect(callout).toContainText("check that no Dataflow job is still running for it");
  await expect(callout.getByRole("link", { name: /A RUNNING row that never closes/ })).toBeVisible();
  // Under the header and above the sections, on every tab.
  const top = async (locator: ReturnType<Page["locator"]>) => (await locator.boundingBox())!.y;
  expect(await top(header)).toBeLessThan(await top(callout));
  expect(await top(callout)).toBeLessThan(await top(page.getByRole("tablist", { name: "Evaluation sections" })));
  // Exact: the callout itself says to check that no job "is still running" before closing the row.
  await expect(page.getByText("Still running", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Metrics are still being computed")).toHaveCount(0);
  await expectNoSeriousViolations(page, "stale run");
  await noHorizontalScroll(page);
  expect(errors).toEqual([]);
});

// eval-0041 and eval-0042 evaluate one launch whose declared keys the source disproved (ADR 0038,
// packages/mock/src/adjusted.ts): the first is chained to it, the second was handed an evaluator job.
test("adjusted keys read the generator's verdict next to the evaluator's FAIL", async ({ page }) => {
  test.setTimeout(240_000);
  const errors = watchErrors(page);

  await page.goto("/evaluation/eval-0041");
  await expect(page.getByTestId("adjusted-summary")).toContainText(
    "4 PK-duplicate FAILs are on keys the launch adjusted (ADR 0038) — 1 faithful, 2 under-reproduced, 1 over-reproduced.",
    { timeout: 45_000 },
  );
  // The evaluator's own counts stand: its five key failures, four of them on adjusted keys.
  const integrity = page.getByRole("region", { name: /^Integrity/ });
  await expect(integrity.getByText("5 key failures")).toBeVisible();
  await expect(integrity.getByTestId("adjusted-key-failures")).toHaveText(/4 on adjusted keys/);
  // A copy that missed ranks right after the FAILs; a faithful one sits further down, before the INFO.
  const findings = page.getByRole("list", { name: "Findings, most severe first" });
  const under = findings.getByRole("listitem").filter({ hasText: "C_TABLE's declared key" });
  await expect(under.locator('[data-status="adjusted"]')).toHaveText(/Adjusted · under/);
  await expect(under).toContainText("evaluator: FAIL (threshold 0)");
  await page.getByRole("button", { name: /^Show all \d+ findings$/ }).click();
  const faithful = findings.getByRole("listitem").filter({ hasText: "E_TABLE's declared key" });
  await expect(faithful.locator('[data-status="adjusted"]')).toHaveText(/Adjusted · faithful/);
  await expect(faithful).toContainText("evaluator: FAIL (threshold 0)");
  await expect(page.getByTestId("pk-adjusted-legend")).toBeVisible();
  await expectNoSeriousViolations(page, "adjusted overview");
  await noHorizontalScroll(page);

  await page.goto("/evaluation/eval-0041?tab=relational");
  const section = page.getByTestId("model-adjustments");
  await expect(section).toBeVisible();
  await expect(section).toHaveAccessibleName(/^Model adjustments \(ADR 0038\)/);
  const row = (table: string) => section.locator(`tr[data-table="${table}"]`);
  await expect(row("B_TABLE")).toContainText("not adjusted: a unique key is expected");
  await expect(row("E_TABLE")).toContainText("Adjusted · faithful");
  await expect(row("E_TABLE")).toContainText("99.999%");
  await expect(row("C_TABLE")).toContainText("Adjusted · under");
  await expect(row("A_TABLE")).toContainText("Adjusted · under");
  await expect(row("D_TABLE")).toContainText("Adjusted · over");
  await expect(row("G_TABLE")).toContainText("Adjusted?");
  const strip = section.getByRole("figure", { name: /Synthetic key-repeat share against the source's/ });
  await expect(strip.locator('[data-slot="interval-ref"]')).toHaveCount(5);
  // The (i) of a pill opens the concept.
  await row("E_TABLE").scrollIntoViewIfNeeded();
  await section.getByRole("button", { name: "About: Adjusted model (ADR 0038)" }).first().click();
  await expect(page.getByRole("dialog", { name: "Adjusted model (ADR 0038)" })).toBeVisible();
  await page.keyboard.press("Escape");
  // Orphan rates are another question: still there, still clean.
  await expect(page.getByRole("region", { name: "Orphan rate per foreign key" })).toBeVisible();
  await expectNoSeriousViolations(page, "adjusted relational");
  await noHorizontalScroll(page);

  await page.goto("/evaluation/eval-0041?tab=params");
  await expect(page.getByTestId("model-adjusted-inferred")).toContainText(
    "(validation_runs says adjusted on 6 tables)",
  );

  // The mis-wired evaluation: a danger banner, and nothing but the root joins.
  await page.goto("/evaluation/eval-0042?tab=relational");
  const banner = page.getByRole("note").filter({ hasText: "Wrong generation job" });
  await expect(banner).toContainText(
    "This evaluation points at an evaluator job, not a generation launch; scope and adjustments cannot be joined.",
  );
  await expect(banner.getByRole("link", { name: "eval-0041" })).toBeVisible();
  const unjoined = page.getByRole("region", { name: /^Model adjustments \(ADR 0038\)/ });
  await expect(unjoined.locator('tr[data-verdict="unknown"]')).toHaveCount(6);
  await expect(unjoined.locator('tr[data-table="E_TABLE"]')).toContainText("no validation_runs row joined");
  await expectNoSeriousViolations(page, "mis-wired evaluation");
  await noHorizontalScroll(page);

  // CONFIG's guardrails list the same runs with the same verdicts.
  await page.goto("/config?section=guardrails");
  const runs = page.getByTestId("adjusted-runs");
  await expect(runs.getByRole("region", { name: "Runs whose gate excluded a rule" })).toBeVisible({ timeout: 60_000 });
  await expect(runs.locator('[data-slot="adjustment"]')).toHaveCount(6);
  await expectNoSeriousViolations(page, "guardrails adjusted runs");
  expect(errors).toEqual([]);
});

test("a run's Parameters tab shows its generation record (ADR 0044)", async ({ page }) => {
  test.setTimeout(120_000);
  const errors = watchErrors(page);
  await page.goto("/evaluation/eval-0001?tab=params");
  const panel = page.getByRole("region", { name: "Generator" });
  await expect(panel).toBeVisible({ timeout: 45_000 });
  await expect(panel.getByText(/^(file|flag|default)$/).first()).toBeVisible();
  await expect(panel.getByRole("button", { name: "Download as config.yaml" })).toBeEnabled();
  await expectNoSeriousViolations(page, "generator panel", "section[aria-label='Generator']");

  await page.goto("/evaluation/eval-0041?tab=params");
  await expect(page.getByText(/No generation record/)).toBeVisible({ timeout: 45_000 });
  expect(errors).toEqual([]);
});

test("degenerate registry states render labelled empty states, never a crash", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page);

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
  // The adjusted-keys strip (docs/UX.md): an element shot of the figure, nothing cropped by hand.
  // The viewport grows to the page first, so neither the sticky header nor the docked legend covers it.
  await page.goto("/evaluation/eval-0041?tab=relational");
  const strip = page.getByRole("figure", { name: /Synthetic key-repeat share against the source's/ });
  await expect(strip).toBeVisible({ timeout: 30_000 });
  const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width, height: Math.min(pageHeight, 12_000) });
  await page.waitForTimeout(1500);
  await strip.screenshot({ path: `${dir}/evaluation-adjusted-keys-${width}.png` });
  await page.setViewportSize({ width, height: isMobile(testInfo.project.name) ? 844 : 900 });
});
