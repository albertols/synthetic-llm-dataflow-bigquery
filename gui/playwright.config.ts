import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { defineConfig, devices } from "@playwright/test";

const CI = Boolean(process.env.CI);
// Locally: the installed Google Chrome (no browser download). CI: Playwright's bundled Chromium.
const channel = CI ? undefined : "chrome";
// Each worktree sets its own GUI_E2E_PORT (docs/ARCHITECTURE.md, "Ports"); never reuse a server,
// or one worktree's e2e would silently test another worktree's build.
const PORT = Number(process.env.GUI_E2E_PORT ?? 4173);

/** What the web build reads: a file here newer than dist/index.html means the build is stale. */
const BUILD_INPUTS = [
  "apps/web/src",
  "apps/web/public",
  "apps/web/index.html",
  "apps/web/vite.config.ts",
  "apps/web/package.json",
  "packages/contracts/src",
  "packages/contracts/generated",
  "packages/stats/src",
  "package-lock.json",
];

function newestMtime(path: string): number {
  if (!existsSync(path)) return 0;
  const stat = statSync(path);
  if (!stat.isDirectory()) return stat.mtimeMs;
  return readdirSync(path).reduce((newest, name) => Math.max(newest, newestMtime(join(path, name))), stat.mtimeMs);
}

/** dist/ was built after every input last changed (`npm run check` just built it). */
function buildIsFresh(): boolean {
  const built = "apps/web/dist/index.html";
  if (!existsSync(built)) return false;
  const builtAt = statSync(built).mtimeMs;
  return BUILD_INPUTS.every((input) => newestMtime(input) < builtAt);
}

// Build once: `npm run check` builds, and e2e serves that build when nothing changed since
// (a stale or missing dist is rebuilt). GUI_E2E_SKIP_BUILD=1 always serves the existing
// dist (CI, after `npm run check`); GUI_E2E_SKIP_BUILD=0 always rebuilds.
const SKIP_BUILD = process.env.GUI_E2E_SKIP_BUILD === "1" || (process.env.GUI_E2E_SKIP_BUILD !== "0" && buildIsFresh());

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
  // The real BFF (apps/server, mock data) serving the built SPA on GUI_E2E_PORT:
  // the same `npm start` a user runs, so e2e exercises /api/* end to end.
  webServer: {
    command: SKIP_BUILD ? "npm start" : "npm run build -w @synthetic-platform/web && npm start",
    url: `http://127.0.0.1:${PORT}/api/health`,
    env: { PORT: String(PORT), HOST: "127.0.0.1", DATA_SOURCE: "mock", LOG_LEVEL: "warn" },
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
