import type { NextConfig } from "next";
import { describe, expect, it } from "vitest";
import nextConfig from "../../../next.config";

describe("Next image cache configuration", () => {
  it("uses the supported image cache floor without overriding the generated route", async () => {
    expect(typeof nextConfig).toBe("object");

    const config = nextConfig as NextConfig;
    expect(config.images?.minimumCacheTTL).toBe(31536000);

    const customHeaders = await config.headers?.();
    expect(customHeaders?.some(({ source }) => source === "/_next/image")).toBe(false);
  });
});
