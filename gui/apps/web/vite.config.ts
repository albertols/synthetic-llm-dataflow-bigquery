/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const TAB_CHUNK = /\/src\/features\/(intro|evaluation|rag|config)\//;

/**
 * Ports come from the environment so parallel worktrees never collide
 * (docs/ARCHITECTURE.md, "Ports"): GUI_WEB_PORT (dev, 5173),
 * GUI_E2E_PORT (preview for Playwright, 4173), GUI_SERVER_PORT (the BFF
 * the dev proxy targets, 8787).
 */
function port(name: string, fallback: number): number {
  const value = Number(process.env[name]);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}
const WEB_PORT = port("GUI_WEB_PORT", 5173);
const E2E_PORT = port("GUI_E2E_PORT", 4173);
const SERVER_PORT = port("GUI_SERVER_PORT", 8787);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Most specific first: "@contracts/generated/*" must not fall into "@contracts/*".
    alias: [
      {
        find: "@contracts/generated",
        replacement: fileURLToPath(new URL("../../packages/contracts/generated", import.meta.url)),
      },
      { find: "@contracts", replacement: fileURLToPath(new URL("../../packages/contracts/src", import.meta.url)) },
      { find: "@", replacement: fileURLToPath(new URL("./src", import.meta.url)) },
    ],
  },
  server: {
    host: "127.0.0.1",
    port: WEB_PORT,
    strictPort: true,
    // The BFF (apps/server, task G0b) listens on 127.0.0.1:GUI_SERVER_PORT.
    proxy: { "/api": `http://127.0.0.1:${SERVER_PORT}` },
  },
  preview: { host: "127.0.0.1", port: E2E_PORT, strictPort: true },
  build: {
    target: "es2023",
    // .size-limit.mjs budgets each tab by its import closure (dist/.vite/manifest.json) and
    // checks the shell's chunks for lazy libraries by their source maps.
    manifest: true,
    sourcemap: true,
    // Never inline a font as a data: URI: @font-face files load only when their unicode-range
    // is on the page, but an inlined one is in the CSS for everyone (a 2.7 kB Cyrillic subset
    // sat in the shell CSS this way).
    assetsInlineLimit: (file) => (/\.woff2?$/.test(file) ? false : undefined),
    chunkSizeWarningLimit: 1500,
    rolldownOptions: {
      output: {
        // Tab code lands in `tab-<tab>-*.js`, readable in a build listing (the budgets in
        // .size-limit.mjs follow the manifest's import closures, not these names).
        chunkFileNames(chunk) {
          const tab = chunk.facadeModuleId?.replaceAll("\\", "/").match(TAB_CHUNK)?.[1];
          return tab ? `assets/tab-${tab}-[name]-[hash].js` : "assets/[name]-[hash].js";
        },
      },
    },
  },
  test: {
    name: "web",
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
  },
});
