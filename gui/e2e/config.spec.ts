/**
 * CONFIG tab e2e (desktop 1440 and mobile 390, the real BFF on mock data):
 * the amp (knob → sheet, keyboard turning, constants that do not turn), the
 * scenario calculator's 90M / 1M / 10k preset, the source-stats tier compare
 * and the guardrails — each checked with axe (WCAG 2.2 AA, serious and
 * critical fail) and for horizontal overflow at 390 px.
 *
 * Screenshots: GUI_SHOTS_DIR=/private/tmp/claude-501/gui-shots/config npx playwright test config -g screenshots
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const USERS = "demo-project.synthetic_source.users";

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function expectAxeClean(page: Page, label: string, include?: string) {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]);
  if (include) builder = builder.include(include);
  const results = await builder.analyze();
  const serious = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${label}: ${v.id} → ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious).toEqual([]);
}

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function search(page: Page): Promise<Record<string, unknown>> {
  return JSON.parse((await page.locator("main#main").getAttribute("data-route-search")) ?? "{}") as Record<
    string,
    unknown
  >;
}

const meter = (page: Page, label: string) =>
  page
    .getByTestId("meter-bridge")
    .locator("div", { has: page.getByText(label, { exact: true }) })
    .locator("dd");

test.describe.configure({ timeout: 90_000 });

test("amp: dials turn with the keyboard, constants do not, and a knob opens its sheet", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/config");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Knobs, scenarios and source statistics");
  await expect(page.getByRole("heading", { name: "Pipeline amp" })).toBeVisible();
  await expectAxeClean(page, "amp");

  const dial = page.getByRole("slider", { name: "Reference rows (n)" });
  await expect(dial).toHaveAttribute("aria-valuetext", "10,000 rows");
  await dial.focus();
  await page.keyboard.press("ArrowUp");
  await expect(dial).toHaveAttribute("aria-valuetext", "20,000 rows");
  await expect(meter(page, "Sample n")).toHaveText("20,000");
  await expect(meter(page, "DKW ε(n)")).toHaveText("0.0096");
  await page.keyboard.press("Home");
  await expect(dial).toHaveAttribute("aria-valuetext", "1,000 rows");

  // Enter opens the sheet; the knob lands in the URL; Esc closes and returns focus.
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Reference rows (n)" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText("--reference_rows_limit")).toBeVisible();
  await expect(sheet.getByRole("figure", { name: /DKW band/ })).toBeVisible();
  expect((await search(page)).knob).toBe("reference_rows_limit");
  await expectAxeClean(page, "knob sheet", '[role="dialog"]');
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(dial).toBeFocused();

  // A constant is a fixed screw: a button, never a slider.
  await page.getByRole("radio", { name: "FREE TEXT" }).click();
  const screw = page.getByRole("button", { name: /^Pool cap: 512 values$/ });
  await expect(page.locator('[data-knob="free_text_pool_max"] [role="slider"]')).toHaveCount(0);
  await screw.click();
  const screwSheet = page.getByRole("dialog", { name: "Pool cap" });
  await expect(screwSheet.getByText("Fixed constant: not settable, change it in code.")).toBeVisible();
  await expect(screwSheet.getByRole("slider")).toHaveCount(0);
  await page.keyboard.press("Escape");

  expect(errors).toEqual([]);
});

test("amp: a deep link opens a sheet with its Docs differ note", async ({ page }) => {
  await page.goto("/config?knob=similarity");
  const sheet = page.getByRole("dialog", { name: "Similarity" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText(/Docs differ: similarity means different things in b1 and b2/)).toBeVisible();
  await expect(sheet.getByRole("link", { name: /_fidelity\.py:226/ })).toHaveAttribute(
    "href",
    /github\.com\/albertols\/synthetic-llm-dataflow-bigquery\/blob\/[0-9a-f]{40}\/.+#L226$/,
  );
});

test("scenario: the 90M-from-1M-with-a-10k-seed preset and its outputs", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/config?section=scenario&scenario=90m-from-1m");
  const headline = page.getByTestId("scenario-headline");
  await expect(headline).toContainText("9,000×");
  await expect(headline).toContainText("M/N = 90× rows per source row");
  await expect(headline).toContainText("0.0136");
  await expect(headline).toContainText("175,781");
  await expect(page.getByTestId("dkw-epsilon")).toHaveText("0.0136");
  await expect(page.getByTestId("rare-capture")).toHaveText("99.995%");
  await expect(page.getByTestId("tail-points")).toHaveText("10.0");
  await expect(page.getByTestId("distinct-sample")).toHaveText("≤ 470");
  await expect(page.getByTestId("pk-pool-dlq")).toHaveText("89,999,488");
  await expect(page.getByTestId("collision-prob")).toHaveText("100%");
  await expect(page.locator('[data-rec="exact-tier"]')).toBeVisible();
  await expect(page.getByTestId("measured-note").first()).toContainText("MEASURED");
  await expectAxeClean(page, "scenario");
  await expectNoHorizontalScroll(page);

  // The exact tier drops the tier warning; typing M re-computes everything.
  await page.getByTestId("preset-exact-tier").click();
  await expect(page.locator('[data-rec="exact-tier"]')).toHaveCount(0);
  const m = page.getByLabel("Target rows M");
  await m.fill("1M");
  await m.blur();
  await expect(page.getByTestId("pk-pool-dlq")).toHaveText("999,488");
  expect(errors).toEqual([]);
});

test("source stats: tiers, the tier compare of one digest and the legacy NULL tier", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(`/config?section=sources&table=${USERS}`);
  const cards = page.getByTestId("snapshot-card");
  await expect(cards).toHaveCount(2, { timeout: 30_000 });
  await expect(page.getByTestId("column-detail")).toBeVisible();
  await expectAxeClean(page, "sources");

  await page.getByRole("radio", { name: "Tier compare" }).click();
  const compare = page.getByTestId("tier-compare");
  await expect(compare).toBeVisible();
  await expect(cards.filter({ hasText: "sample tier" })).toHaveCount(1);
  await expect(cards.filter({ hasText: "exact tier" })).toHaveCount(1);
  const digests = await cards.locator("text=/digest [0-9a-f]{12}/").allTextContents();
  expect(new Set(digests).size).toBe(1);
  await expect(compare.getByRole("row", { name: /email/ })).toContainText("×");
  expect(typeof (await search(page)).digest).toBe("string");
  await expectAxeClean(page, "tier compare");
  await expectNoHorizontalScroll(page);

  // The legacy profiler-1 snapshot (NULL stats_tier) reads as the sample tier.
  await page.getByRole("radio", { name: "One snapshot" }).click();
  await page.getByLabel("Snapshot", { exact: true }).click();
  await page.getByRole("option", { name: /NULL tier/ }).click();
  await expect(page.getByText("NULL tier → sample").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("guardrails: lines of defence, DLQ rules as emitted, uniqueness and the env gate", async ({ page }) => {
  await page.goto("/config?section=guardrails");
  await expect(page.getByTestId("defence-chain")).toContainText("EnforceUniqueness");
  const rules = page.getByTestId("dlq-rules");
  await expect(rules.locator('[data-rule="null.required"]')).toContainText("never");
  await expect(rules.locator('[data-rule="null.required"]')).toContainText("counted");
  await expect(rules.locator('[data-rule="fk.orphan"]')).toContainText("not counted");
  await expect(page.getByText(/Docs differ: fk\.orphan is BLOCKER/)).toBeVisible();
  await expect(page.getByText("Docs differ: what line 3 is")).toBeVisible();
  const ratios = page.getByTestId("blocker-ratios");
  await expect(ratios).toContainText("20%");
  await expect(ratios).toContainText("5%");
  await expect(ratios).toContainText("1%");
  await page.getByRole("radio", { name: "prd" }).click();
  await expect(ratios).toContainText("prd (selected)");
  await expectAxeClean(page, "guardrails");
  await expectNoHorizontalScroll(page);
});

test("screenshots of the config tab (set GUI_SHOTS_DIR to capture)", async ({ page }, testInfo) => {
  const dir = process.env.GUI_SHOTS_DIR;
  test.skip(!dir, "GUI_SHOTS_DIR not set");
  test.setTimeout(240_000);
  const width = testInfo.project.name === "mobile" ? 390 : 1440;
  const settle = async () => {
    for (let y = 0; y < 30_000; y += 700) {
      await page.evaluate((top) => window.scrollTo(0, top), y);
      await page.waitForTimeout(60);
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(1500);
  };
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Each shot waits for the data it shows (the mock BFF can be slow to warm).
  const shots: Array<[string, string, string]> = [
    ["amp", "/config", "meter-bridge"],
    ["scenario-90m", "/config?section=scenario&scenario=90m-from-1m", "recommendations"],
    ["sources", `/config?section=sources&table=${USERS}&column=country`, "column-detail"],
    ["guardrails", "/config?section=guardrails", "dlq-rules"],
  ];
  for (const [name, path, ready] of shots) {
    await page.goto(path);
    await expect(page.getByTestId(ready)).toBeVisible({ timeout: 60_000 });
    if (name === "guardrails") await expect(page.getByTestId("dlq-rules")).not.toContainText("…", { timeout: 60_000 });
    await settle();
    await page.screenshot({ path: `${dir}/config-${name}-${width}.png`, fullPage: true });
  }
  await page.goto(`/config?section=sources&table=${USERS}`);
  await expect(page.getByTestId("snapshot-card").first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("radio", { name: "Tier compare" }).click();
  await expect(page.getByTestId("tier-compare")).toBeVisible({ timeout: 60_000 });
  await settle();
  await page.screenshot({ path: `${dir}/config-sources-tier-compare-${width}.png`, fullPage: true });

  await page.goto("/config?knob=reference_rows_limit");
  await expect(page.getByRole("dialog", { name: "Reference rows (n)" })).toBeVisible();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${dir}/config-knob-sheet-${width}.png` });
});
