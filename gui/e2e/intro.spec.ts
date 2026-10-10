/**
 * INTRO end to end, against the real BFF in mock mode (desktop 1440 px and
 * mobile 390 px): the hero's stages deep-link to their tabs, the counters show
 * what /api/facets answers, the "How it works" cards quote DESIGN.md with
 * figures that load and carry provenance, reduced motion gets the static
 * rail, the glossary searches the registry, and axe finds no serious or
 * critical WCAG 2.2 AA violation in either theme.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Facets = {
  counts: { runs: number; evaluations: number; tables: number };
  latest: { evaluation_id: string; overall_score: number | null } | null;
};

const count = (value: number) => new Intl.NumberFormat("en-US").format(value);
const isMobile = (projectName: string) => projectName === "mobile";

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function expectNoSeriousViolations(page: Page, label: string, include?: string) {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]);
  if (include) builder = builder.include(include);
  const results = await builder.analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => `${label}: ${violation.id} → ${violation.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious).toEqual([]);
}

async function routeStamp(page: Page): Promise<{ routeId: string; search: Record<string, unknown> }> {
  const main = page.locator("main#main");
  return {
    routeId: (await main.getAttribute("data-route-id")) ?? "",
    search: JSON.parse((await main.getAttribute("data-route-search")) ?? "{}") as Record<string, unknown>,
  };
}

async function switchTheme(page: Page, projectName: string, to: "light" | "dark") {
  const name = `Switch to ${to} theme`;
  if (isMobile(projectName)) {
    await page.getByRole("button", { name: "More: theme and GitHub links" }).click();
    await page.getByRole("menuitem", { name }).click();
  } else {
    await page.getByRole("button", { name }).click();
  }
  await expect(page.locator("html")).toHaveAttribute("data-theme", to);
}

/** Brings every lazy piece (figures, both mermaid diagrams) into view so it renders, then returns to the top. */
async function renderEverything(page: Page) {
  const height = await page.evaluate(() => document.body.scrollHeight);
  for (let y = 0; y < height; y += 500) {
    await page.evaluate((top) => window.scrollTo(0, top), y);
    await page.waitForTimeout(80);
  }
  for (const [target, diagram] of [
    ["article[data-section='12']", "#how-it-works svg[id^='mermaid']"],
    ["#packages figure", "#packages svg[id^='mermaid']"],
  ] as const) {
    await page.locator(target).scrollIntoViewIfNeeded();
    await expect(page.locator(diagram)).toHaveCount(1, { timeout: 30_000 });
  }
  await page.evaluate(() => window.scrollTo(0, 0));
}

const stages = (page: Page) => page.getByRole("list", { name: "Pipeline stages" });

test("the hero walks nine stages and each one deep-links to its tab", async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  const facets = (await (await request.get("/api/facets")).json()) as Facets;
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expect(stages(page).locator("li[data-stage]")).toHaveCount(9);

  const cases = [
    { link: "Reference sample", routeId: "/config", search: { section: "sources" } },
    { link: "RAG retrieval", routeId: "/rag", search: {} },
    { link: "Generation on L4", routeId: "/config", search: { section: "amp", channel: "generation" } },
    { link: "Mode A guardrails", routeId: "/config", search: { knob: "uniqueness_mode" } },
    { link: "Evaluation", routeId: "/evaluation", search: {} },
  ];
  for (const { link, routeId, search } of cases) {
    await stages(page).getByRole("link", { name: link, exact: true }).click();
    await expect.poll(async () => (await routeStamp(page)).routeId).toBe(routeId);
    expect((await routeStamp(page)).search).toMatchObject(search);
    await page.goBack();
    await expect(stages(page).locator("li[data-stage]")).toHaveCount(9);
  }

  // The registry stage opens the newest finished evaluation once the facets are in.
  await expect(stages(page).getByText(`${count(facets.counts.evaluations)} evaluations`)).toBeVisible();
  await stages(page).getByRole("link", { name: "Registry", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/evaluation/${facets.latest!.evaluation_id}$`));
  expect(errors).toEqual([]);
});

test("the counters show what /api/facets answers in mock mode", async ({ page, request }) => {
  const facets = (await (await request.get("/api/facets")).json()) as Facets;
  await page.goto("/");
  const tiles = page.getByTestId("live-counters");
  const tile = (name: string) => tiles.getByRole("group", { name });
  await expect(tile("Generation runs")).toContainText(count(facets.counts.runs));
  await expect(tile("Evaluations")).toContainText(count(facets.counts.evaluations));
  await expect(tile("Tables evaluated")).toContainText(count(facets.counts.tables));
  await expect(tile("Latest overall score")).toContainText(facets.latest!.overall_score!.toFixed(2));
  await expect(tile("Latest overall score").getByRole("link", { name: facets.latest!.evaluation_id })).toBeVisible();
  await expect(page.getByText("Seeded mock data · no GCP access needed")).toBeVisible();
});

test("How it works quotes DESIGN.md with figures that load and carry provenance", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const errors = watchErrors(page);
  await page.goto("/#how-it-works");
  const cards = page.getByRole("list", { name: "DESIGN.md sections" }).getByRole("article");
  await expect(cards).toHaveCount(12);
  await expect(cards.first()).toContainText(
    "one Dataflow job reads a bounded sample, touches the GPU a bounded number of times, and writes validated rows; nothing leaves the project.",
  );
  await expect(page.locator("article[data-section='11']")).toContainText(
    "evaluation is a separate CPU job, after generation, that scores each unit of the synthetic data against the full source",
  );

  await renderEverything(page);
  const images = page.locator("#how-it-works img");
  for (let i = 0; i < (await images.count()); i += 1) await images.nth(i).scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      images.evaluateAll((all) =>
        (all as HTMLImageElement[]).filter((img) => !img.complete || img.naturalWidth === 0).map((img) => img.src),
      ),
    )
    .toEqual([]);

  const fanout = page.locator("article[data-section='4']").getByRole("button", { name: /Parent-driven fan-out/ });
  if (!isMobile(testInfo.project.name)) {
    await fanout.hover();
    await expect(page.getByText("scripts/doc/make_fanout_figures.py").first()).toBeVisible();
  }
  await fanout.click();
  const dialog = page.getByRole("dialog", { name: /§4 · Relational generation/ });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Provenance");
  await expect(dialog.getByRole("link", { name: /scripts\/doc\/make_fanout_figures\.py/ })).toBeVisible();
  await expectNoSeriousViolations(page, "figure dialog", '[role="dialog"]');
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(fanout).toBeFocused();
  expect(errors).toEqual([]);
});

test("reduced motion gets the rail at rest, fully lit; otherwise it plays and pauses", async ({ page }) => {
  test.setTimeout(90_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByText("Animation off: your system asks for reduced motion.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Pause" })).toHaveCount(0);
  const lit = await stages(page)
    .locator("li[data-stage]")
    .evaluateAll((items) => items.map((li) => li.getAttribute("data-lit")));
  expect(lit).toEqual(Array(9).fill("true"));
  // Mermaid lays out at its natural size under reduced motion too (globals.css exempts mermaid's SVG).
  await page.locator("#packages figure").scrollIntoViewIfNeeded();
  const diagram = page.locator("#packages svg[id^='mermaid']");
  await expect(diagram).toHaveCount(1, { timeout: 30_000 });
  const viewBoxWidth = Number(((await diagram.getAttribute("viewBox")) ?? "").split(" ")[2]);
  expect(viewBoxWidth).toBeGreaterThan(0);
  expect(viewBoxWidth).toBeLessThan(2000);

  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/");
  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
  await page.getByRole("button", { name: "Next step" }).click();
  await expect(page.getByTestId("stage-detail")).toContainText(/Step \d of 9/);
});

test("the glossary searches the concept registry and keeps the query in the URL", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/#glossary");
  await page.getByRole("searchbox", { name: "Search the glossary" }).fill("noise floor");
  const results = page.getByRole("list", { name: "Glossary results" });
  await expect(results.getByRole("heading", { level: 3 }).first()).toHaveText("Noise floor");
  await expect.poll(async () => (await routeStamp(page)).search.q).toBe("noise floor");

  await results.getByRole("button", { name: "About: Noise floor" }).click();
  const hint = page.getByRole("dialog", { name: "Noise floor" });
  await expect(hint).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(hint).toBeHidden();

  await page.getByRole("searchbox", { name: "Search the glossary" }).fill("");
  await page
    .getByRole("radiogroup", { name: "Concept group" })
    .getByRole("radio", { name: /^Metrics/ })
    .click();
  await expect.poll(async () => (await routeStamp(page)).search.ns).toBe("metric");
  await expect(results.getByText("Metrics", { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("the shape gallery draws six shapes and highlights the thelook example", async ({ page }) => {
  await page.goto("/#shapes");
  await expect(page.locator("#shapes article[data-shape]")).toHaveCount(6);
  const thelook = page.locator("article[data-shape='graph']");
  await expect(thelook.getByRole("heading", { name: "Graph: the thelook example" })).toBeVisible();
  await expect(thelook.getByRole("img", { name: /thelook/ })).toBeVisible();
  await thelook.getByRole("radio", { name: "Mock model" }).click();
  await expect(thelook).toContainText("thelook_demo");
  await expect(thelook).toContainText("documented edge");
});

test("INTRO is accessible in both themes and never scrolls sideways", async ({ page }, testInfo) => {
  // Two full-page axe passes over a long page: generous, so a loaded CI machine does not flake.
  test.setTimeout(180_000);
  const errors = watchErrors(page);
  await page.goto("/");
  await renderEverything(page);
  await expectNoSeriousViolations(page, "intro dark");
  await switchTheme(page, testInfo.project.name, "light");
  await expectNoSeriousViolations(page, "intro light");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await switchTheme(page, testInfo.project.name, "dark");
  expect(errors).toEqual([]);
});

test("screenshots of INTRO (set GUI_SHOTS_DIR to capture)", async ({ page }, testInfo) => {
  const dir = process.env.GUI_SHOTS_DIR;
  test.skip(!dir, "GUI_SHOTS_DIR not set");
  test.setTimeout(120_000);
  const width = isMobile(testInfo.project.name) ? 390 : 1440;
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await renderEverything(page);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${dir}/intro-${width}-dark.png`, fullPage: true });
  await page.screenshot({ path: `${dir}/intro-${width}-dark-hero.png` });
  await switchTheme(page, testInfo.project.name, "light");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${dir}/intro-${width}-light.png`, fullPage: true });
  await page.screenshot({ path: `${dir}/intro-${width}-light-hero.png` });
  await switchTheme(page, testInfo.project.name, "dark");
});
