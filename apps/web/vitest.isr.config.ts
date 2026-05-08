import { defineConfig } from "vitest/config";
import path from "path";

/**
 * Slow-lane vitest config for the ISR build-output classifier
 * (`apps/web/__tests__/build-output.test.ts`, see #2885).
 *
 * Runs `pnpm build` once via globalSetup, captures the per-route
 * classification (`◐` / `○` / `ƒ`) from stdout, and asserts the
 * must-stay-cacheable routes are not Dynamic. Excluded from the
 * default `pnpm test` config (which only includes `src/**` and
 * `app/**`).
 *
 * Usage:
 *   pnpm test:isr          # run build + classifier locally
 *   BUILD_OUTPUT_LOG=path  # skip build, read pre-captured log (CI split)
 */
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
    environment: "node",
    globals: true,
    include: ["__tests__/build-output.test.ts"],
    globalSetup: ["./test-setup/run-prod-build.ts"],
    // The build itself eats 1-3 minutes; per-test timeout has to clear it
    // since the globalSetup blocks until pnpm build finishes.
    testTimeout: 600_000,
    hookTimeout: 600_000,
  },
});
