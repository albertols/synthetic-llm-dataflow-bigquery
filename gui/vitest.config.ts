import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * One `vitest run` for the whole workspace:
 *   web  — apps/web (jsdom; config in apps/web/vite.config.ts)
 *   node — packages/*, apps/server, gui/scripts (Node)
 */
export default defineConfig({
  test: {
    projects: [
      "apps/web",
      {
        resolve: {
          alias: [
            {
              find: "@contracts/generated",
              replacement: fileURLToPath(new URL("./packages/contracts/generated", import.meta.url)),
            },
            { find: "@contracts", replacement: fileURLToPath(new URL("./packages/contracts/src", import.meta.url)) },
          ],
        },
        test: {
          name: "node",
          environment: "node",
          include: [
            "packages/*/src/**/*.test.ts",
            "packages/*/*.test.{ts,mjs}",
            "apps/server/src/**/*.test.ts",
            "scripts/**/*.test.{ts,mjs}",
          ],
        },
      },
    ],
  },
});
