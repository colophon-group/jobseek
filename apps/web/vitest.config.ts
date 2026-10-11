import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  oxc: {
    jsx: { runtime: "automatic" },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    setupFiles: ["./src/test-utils/normal-service-availability.ts"],
    environment: "happy-dom",
    globals: true,
    fileParallelism: false,
    include: ["src/**/*.test.{ts,tsx}", "app/**/*.test.{ts,tsx}"],
  },
});
