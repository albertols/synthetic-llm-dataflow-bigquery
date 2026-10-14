// ESLint 9 flat config for the whole GUI workspace.
// Type-aware typescript-eslint on .ts/.tsx, React hooks (incl. the React
// Compiler rules) and jsx-a11y on the web app.
import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores([
    "**/node_modules/**",
    "**/dist/**",
    "**/.vite/**",
    "playwright-report/**",
    "test-results/**",
    "apps/web/public/**",
    "packages/contracts/generated/**",
  ]),
  {
    files: ["**/*.{js,mjs,cjs}"],
    extends: [js.configs.recommended],
    languageOptions: { globals: { ...globals.node } },
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": ["error", { checksVoidReturn: { attributes: false } }],
    },
  },
  {
    files: ["apps/web/src/**/*.{ts,tsx}"],
    extends: [reactHooks.configs.flat.recommended, jsxA11y.flatConfigs.recommended],
    languageOptions: { globals: { ...globals.browser } },
    rules: {
      // Scrollable regions must be keyboard-reachable (axe scrollable-region-focusable).
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["tabpanel", "region"] }],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["echarts", "echarts/*"],
              allowTypeImports: true,
              message: "Render charts through ChartFrame; import echarts types only (`import type`).",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "apps/web/src/components/EChartCanvas.tsx",
      "apps/web/src/components/ChartFrame.tsx",
      "apps/web/src/components/echartsTheme.ts",
    ],
    rules: { "no-restricted-imports": "off" },
  },
  {
    files: ["**/*.test.{ts,tsx}", "e2e/**/*.ts"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/unbound-method": "off",
    },
  },
]);
