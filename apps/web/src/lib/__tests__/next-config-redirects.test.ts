import type { NextConfig } from "next";
import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "../../../next.config";

describe("root asset redirects", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("canonicalizes browser-generated Apple icon variants", async () => {
    expect(typeof nextConfig).toBe("object");

    const redirects = await (nextConfig as NextConfig).redirects?.();

    expect(redirects).toContainEqual({
      source: "/apple-touch-icon-:variant.png",
      destination: "/apple-touch-icon.png",
      permanent: true,
    });
  });

  it("retains the runtime-configured IndexNow proof rewrite", async () => {
    vi.stubEnv("INDEXNOW_KEY", "indexnow-verification-token");

    const rewrites = await (nextConfig as NextConfig).rewrites?.();

    expect(rewrites).toContainEqual({
      source: "/indexnow-verification-token.txt",
      destination: "/indexnow-key.txt",
    });
  });
});
