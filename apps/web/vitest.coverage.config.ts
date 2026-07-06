import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "happy-dom",
    globals: true,
    include: [
      "src/lib/actions/__tests__/{bootstrap,company-page-data-defaults,explore-page-data-salary-currency,locations-hierarchical-counts,locations-macro,request-company}.test.ts",
      "src/lib/search/**/*.test.{ts,tsx}",
      "src/lib/__tests__/{salary,sanitize}.test.ts",
      "app/api/v1/**/*.test.{ts,tsx}",
    ],
    fileParallelism: false,
    coverage: {
      enabled: true,
      provider: "v8",
      reporter: ["text"],
      include: [
        "app/api/v1/_shared.ts",
        "app/api/v1/resolve/route.ts",
        "app/api/v1/search/route.ts",
        "app/api/v1/watchlist/create/route.ts",
        "src/lib/actions/bootstrap.ts",
        "src/lib/actions/company-page-data.ts",
        "src/lib/actions/explore-page-data.ts",
        "src/lib/services/locations.ts",
        "src/lib/actions/request-company.ts",
        "src/lib/search/canonicalize-filters.ts",
        "src/lib/search/histogram-filters.ts",
        "src/lib/search/location-prefetch.ts",
        "src/lib/search/query-params.ts",
        "src/lib/search/scoped-key.ts",
        "src/lib/search/typesense-browser-key.ts",
        "src/lib/search/typesense-filters.ts",
        "src/lib/search/typesense-query-size.ts",
        "src/lib/search/typesense-retry.ts",
        "src/lib/salary.ts",
        "src/lib/sanitize.ts",
      ],
      exclude: [
        "**/__tests__/**",
        "**/*.test.{ts,tsx}",
        "src/lib/search/typesense.e2e.test.ts",
      ],
      thresholds: {
        statements: 75,
        branches: 65,
        functions: 65,
        lines: 75,
      },
    },
  },
});
