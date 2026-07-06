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
    include: ["src/**/*.test.{ts,tsx}", "app/**/*.test.{ts,tsx}"],
    coverage: {
      enabled: true,
      provider: "v8",
      reporter: ["text"],
      include: [
        "app/api/murmur/**/*.{ts,tsx}",
        "app/health/**/*.ts",
        "src/deploy/**/*.ts",
        "src/lib/murmur/**/*.ts",
      ],
      exclude: ["**/__tests__/**", "**/*.test.{ts,tsx}"],
      thresholds: {
        statements: 70,
        branches: 60,
        functions: 65,
        lines: 70,
      },
    },
  },
});
