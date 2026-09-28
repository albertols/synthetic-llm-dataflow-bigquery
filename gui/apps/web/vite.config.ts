/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const TAB_CHUNK = /\/src\/features\/(intro|evaluation|rag|config)\//;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@contracts": fileURLToPath(new URL("../../packages/contracts/src", import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // The BFF (apps/server, task G0b) listens on 127.0.0.1:8787.
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
  build: {
    target: "es2023",
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
    rolldownOptions: {
      output: {
        // Tab code lands in `tab-<tab>-*.js` so `.size-limit.json` can budget
        // each tab on its own; everything else keeps Vite's default naming.
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
