import { defineConfig, devices } from "@playwright/test";

const CI = Boolean(process.env.CI);
// Locally: the installed Google Chrome (no browser download). CI: Playwright's bundled Chromium.
const channel = CI ? undefined : "chrome";
// Each worktree sets its own GUI_E2E_PORT (docs/ARCHITECTURE.md, "Ports"); never reuse a server,
// or one worktree's e2e would silently test another worktree's build.
const PORT = Number(process.env.GUI_E2E_PORT ?? 4173);
// CI builds in `npm run check`; GUI_E2E_SKIP_BUILD=1 previews that build instead of building twice.
const SKIP_BUILD = process.env.GUI_E2E_SKIP_BUILD === "1";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  workers: CI ? 2 : undefined,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: CI ? [["github"], ["html", { open: "never" }]] : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    colorScheme: "dark",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      use: {
        ...devices["Desktop Chrome"],
        channel,
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  // Static preview of the built SPA. Task G0b switches this to the BFF
  // (`npm start`, mock mode) once apps/server exists.
  webServer: {
    command: `npm run ${SKIP_BUILD ? "preview" : "preview:e2e"} -w @synthetic-platform/web`,
    url: `http://127.0.0.1:${PORT}`,
    env: { GUI_E2E_PORT: String(PORT) },
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
