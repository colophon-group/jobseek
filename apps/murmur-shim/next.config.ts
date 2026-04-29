import type { NextConfig } from "next";
import path from "node:path";

/**
 * Next.js config for `apps/murmur-shim`.
 *
 * This app is a slim sibling of `apps/web` that hosts the Murmur webhook +
 * subcommand routes (`/api/murmur/**`). It targets Hetzner via the
 * standalone output — Vercel cannot host these handlers because they
 * spawn a Python subprocess (`apps/crawler/.venv` + `cli_shim`), which
 * the serverless platform does not support.
 *
 * `outputFileTracingRoot` is set to the monorepo root so Next.js' file
 * tracer picks up the workspace siblings (the `apps/web/src/db/schema.ts`
 * we re-export, and the `apps/crawler/.venv` referenced by `invoke-lib`).
 */
const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "../.."),
  devIndicators: false,
};

export default nextConfig;
