/**
 * RAG tab, end to end against the BFF in mock mode (desktop 1440 px and
 * mobile 390 px): the cloud loads, the seed strategy switches through the
 * URL, the query box embeds and ranks, the 2-D fallback works by choice and
 * after a real WebGL context loss, the UMAP cache route round-trips, every
 * section mounts, and axe finds no serious or critical WCAG 2.2 AA
 * violation, dark and light. No console errors anywhere.
 */
import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const SLOW = { timeout: 60_000 };

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function expectNoSeriousViolations(page: Page, label: string) {
  // From the top: axe counts a target the sticky header happens to cover at the current scroll as obscured.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(200);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((v) => `${label}: ${v.id} → ${v.nodes.map((node) => node.target.join(" ")).join(" | ")}`);
  expect(serious).toEqual([]);
}

async function routeSearch(page: Page): Promise<Record<string, unknown>> {
  return JSON.parse((await page.locator("main#main").getAttribute("data-route-search")) ?? "{}") as Record<
    string,
    unknown
  >;
}

/** The explorer is ready when its 3-D frame (or, without WebGL, its 2-D plane) is on screen. */
async function waitForCloud(page: Page) {
  await expect(page.locator('[data-slot="deck-frame"], [data-slot="cloud-2d"]').first()).toBeVisible(SLOW);
}

/** Scroll the whole page so every lazily mounted section renders. */
async function mountAllSections(page: Page) {
  for (const id of ["lab", "index", "retrieval", "pools"]) {
    await page.locator(`#${id}`).scrollIntoViewIfNeeded();
    await expect(page.locator(`#${id} [aria-busy="true"]`)).toHaveCount(0, SLOW);
  }
  // Let charts and lazy chunks settle before measuring.
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1500);
}

/** Below 640 px the theme switch lives in the header's "More" menu. */
async function switchTheme(page: Page, projectName: string, to: "light" | "dark") {
  const name = `Switch to ${to} theme`;
  if (projectName === "mobile") {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole("button", { name: "More: theme and GitHub links" }).click();
    await page.getByRole("menuitem", { name }).click();
  } else await page.getByRole("button", { name }).click();
}

async function pickOption(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label }).click();
  await page.getByRole("option", { name: new RegExp(`^${option} ·`) }).click();
}

test.describe.configure({ timeout: 180_000 });

test("loads the cloud with its legend, seeds, projection quality and filters", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/rag");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("The retrieval layer, in 384 dimensions");
  await waitForCloud(page);
  const filters = page.getByRole("group", { name: "What to show" });
  await expect(filters.getByRole("combobox", { name: "Table" })).toContainText("users");
  await expect(filters.getByRole("combobox", { name: "Embedder" })).toContainText("hashing-384");
  await expect(page.getByRole("list", { name: /Categories/ })).toBeVisible();
  await expect(
    page.getByText(/Rings: the 8 seeds that Centroid top-k picks here \(exact port of the pipeline\)/),
  ).toBeVisible(SLOW);
  await expect(page.getByText(/10 nearest, 150 points, PCA 3-D/)).toBeVisible(SLOW);
  await expectNoSeriousViolations(page, "rag loaded");
  expect(errors).toEqual([]);
});

test("switches the seed strategy through the URL and labels teaching contrasts", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/rag");
  await waitForCloud(page);
  await pickOption(page, "Seed strategy", "k-center");
  await expect.poll(async () => (await routeSearch(page)).strategy).toBe("kcenter");
  await expect(page.getByText(/Rings: the 8 seeds that k-center picks here/)).toBeVisible(SLOW);

  await page.locator("#retrieval").scrollIntoViewIfNeeded();
  const table = page.getByRole("region", { name: "Strategies compared" });
  await expect(table).toBeVisible(SLOW);
  await expect(table.getByRole("row", { name: /MMR/ })).toContainText("teaching contrast — not in pipeline");
  await expect(table.getByRole("row", { name: /^k-center/ }).first()).toContainText("exact port");
  await table.getByRole("button", { name: "Random", exact: true }).click();
  await expect.poll(async () => (await routeSearch(page)).strategy).toBe("random");
  await expect(page.getByText(/Teaching contrast — not in pipeline/i).first()).toBeVisible();
  await expect(page.getByTestId("seed-list").getByRole("listitem")).toHaveCount(8);

  // The k-center walk: static and complete at first, then steppable.
  await expect(page.getByRole("list", { name: "The walk's picks, in order" }).getByRole("listitem")).toHaveCount(8);
  await page.getByRole("button", { name: "Previous pick" }).click();
  await expect(page.getByText(/^Pick 7:/)).toBeVisible();
  expect(errors).toEqual([]);
});

test("embeds a query in the browser and ranks the cloud by cosine", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/rag");
  await waitForCloud(page);
  await page.getByRole("textbox", { name: "Query the space" }).fill("country is Brasil,");
  await page.getByRole("button", { name: "Embed", exact: true }).click();
  const results = page.getByRole("list", { name: "Query results" });
  await expect(results.getByRole("listitem")).toHaveCount(8);
  await expect(page.getByText("3 tokens → top 8 by cosine:")).toBeVisible();
  const first = results.getByRole("button").first();
  const text = ((await first.textContent()) ?? "").replace(/^\s*1\s*[0-9.]+\s*/, "");
  await first.click();
  await expect(page.getByTestId("inspector")).toContainText(text.slice(0, 24));
  await expect(page.getByText(/Nearest by cosine/i)).toBeVisible();
  expect(errors).toEqual([]);
});

test("falls back to 2-D by choice and after a WebGL context loss", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/rag");
  await waitForCloud(page);
  const hasWebGL = (await page.locator('[data-slot="deck-frame"]').count()) > 0;

  // By choice: the 2-D canvas, no warning.
  await page.getByRole("radio", { name: "2-D" }).click();
  await expect.poll(async () => (await routeSearch(page)).view).toBe("2d");
  await expect(page.getByRole("img", { name: /projected to 2-D by PCA/ })).toBeVisible();
  await expect(page.getByText(/on a plain canvas, no WebGL/)).toBeVisible();
  await expect(page.locator('[data-slot="deck-frame"]')).toHaveCount(0);
  await expectNoSeriousViolations(page, "rag 2-D");

  // Back to 3-D, then lose the GL context for real.
  await page.getByRole("radio", { name: "3-D" }).click();
  test.skip(!hasWebGL, "this browser has no WebGL2: the fallback was already on");
  await expect(page.locator('[data-slot="deck-frame"] canvas')).toBeVisible(SLOW);
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>('[data-slot="deck-frame"] canvas');
    canvas?.getContext("webgl2")?.getExtension("WEBGL_lose_context")?.loseContext();
  });
  await expect(page.getByText(/The 3-D view stopped: the graphics context was lost/)).toBeVisible();
  await expect(page.getByRole("img", { name: /projected to 2-D by PCA/ })).toBeVisible();
  await expect(page.locator('[data-slot="deck-frame"]')).toHaveCount(0);
  const unexpected = errors.filter((e) => !/CONTEXT_LOST|context lost|WebGL/i.test(e));
  expect(unexpected).toEqual([]);
});

test("every section mounts, reads without horizontal scroll, and passes axe in both themes", async ({
  page,
}, testInfo) => {
  const errors = watchErrors(page);
  await page.goto("/rag");
  await waitForCloud(page);
  await mountAllSections(page);
  await expect(page.getByTestId("great-sentence")).toContainText("id is");
  await expect(page.getByText("identical to the stored chunk_text")).toBeVisible();
  await expect(page.getByText(/matches the stored vector/)).toBeVisible();
  await expect(page.getByText("1.5 MiB")).toBeVisible();
  await expect(page.getByRole("region", { name: "Pools per column" })).toContainText("LLM ladder");
  await expect(page.getByRole("link", { name: /Tune the pool cap in CONFIG/ })).toHaveAttribute(
    "href",
    /\/config\?knob=free_text_pool_max/,
  );
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await expectNoSeriousViolations(page, "rag all sections dark");
  // The real theme switch, with transitions collapsed so axe reads settled colours.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await switchTheme(page, testInfo.project.name, "light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.waitForTimeout(1000);
  await expectNoSeriousViolations(page, "rag all sections light");
  expect(errors).toEqual([]);
});

test("the UMAP layout cache answers a miss, stores a layout and serves it back", async ({ request }, testInfo) => {
  // One BFF serves both projects: each run uses its own key.
  const key = {
    digest: "a".repeat(64),
    embedder: "hashing-384/v1",
    space: "rows",
    ids: "b".repeat(64),
    params: `e2e ${testInfo.project.name} ${Date.now()}`,
  };
  const query = new URLSearchParams(key).toString();
  const miss = await request.get(`/api/x/rag/projection?${query}`);
  expect(miss.status()).toBe(200);
  expect(await miss.json()).toEqual({ hit: false });
  const stored = await request.post("/api/x/rag/projection", {
    data: { ...key, n: 2, coords: [0, 0, 0, 1, 1, 1], frame: { center: [0, 0, 0], scale: 1 }, trust: 0.5 },
  });
  expect(stored.status()).toBe(201);
  const hit = (await (await request.get(`/api/x/rag/projection?${query}`)).json()) as { hit: boolean; n: number };
  expect(hit).toMatchObject({ hit: true, n: 2, coords: [0, 0, 0, 1, 1, 1], trust: 0.5 });
  const bad = await request.post("/api/x/rag/projection", {
    data: { ...key, n: 3, coords: [0, 0, 0], frame: { center: [0, 0, 0], scale: 1 }, trust: null },
  });
  expect(bad.status()).toBe(400);
  expect((await request.get("/api/x/rag/projection?digest=zz")).status()).toBe(400);
});

test("screenshots of the RAG tab (set GUI_SHOTS_DIR to capture)", async ({ page }, testInfo) => {
  const dir = process.env.GUI_SHOTS_DIR;
  test.skip(!dir, "GUI_SHOTS_DIR not set");
  const width = testInfo.project.name === "mobile" ? 390 : 1440;
  await page.goto("/rag");
  await waitForCloud(page);
  // Section shots scroll: keep the sticky header from painting over them.
  await page.addStyleTag({ content: "header { position: static !important; }" });
  await page.waitForTimeout(2500);
  await page.getByRole("textbox", { name: "Query the space" }).fill("country is Brasil,");
  await page.getByRole("button", { name: "Embed", exact: true }).click();
  await page.getByRole("list", { name: "Query results" }).getByRole("button").first().click();
  await page.waitForTimeout(800);
  await page.locator("#explorer").screenshot({ path: `${dir}/rag-explorer-3d-${width}.png` });
  await mountAllSections(page);
  await page.waitForTimeout(2500);
  for (const [id, name] of [
    ["lab", "embedder-lab"],
    ["index", "faiss"],
    ["retrieval", "retrieval-simulator"],
    ["pools", "pools"],
  ] as const) {
    await page.locator(`#${id}`).screenshot({ path: `${dir}/rag-${name}-${width}.png` });
  }
  await page.screenshot({ path: `${dir}/rag-full-${width}.png`, fullPage: true });
  await page.locator("#explorer").scrollIntoViewIfNeeded();
  await page.getByRole("radio", { name: "2-D" }).click();
  await page.waitForTimeout(800);
  await page.locator("#explorer").screenshot({ path: `${dir}/rag-fallback-2d-${width}.png` });
  await page.getByRole("radio", { name: "3-D" }).click();
  await expect(page.locator('[data-slot="deck-frame"] canvas')).toBeVisible(SLOW);
  await page.waitForTimeout(800);
  await page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>('[data-slot="deck-frame"] canvas');
    canvas?.getContext("webgl2")?.getExtension("WEBGL_lose_context")?.loseContext();
  });
  await expect(page.getByText(/graphics context was lost/)).toBeVisible();
  await page.locator("#explorer").screenshot({ path: `${dir}/rag-fallback-context-lost-${width}.png` });
});
