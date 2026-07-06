import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

describe("company OG cache", () => {
  withTestEnv({
    COMPANY_OG_RENDERER_VERSION: "renderer123",
    COMPANY_OG_CACHE_BYPASS: undefined,
    R2_ENDPOINT_URL: undefined,
    R2_ACCESS_KEY_ID: undefined,
    R2_SECRET_ACCESS_KEY: undefined,
    R2_BUCKET: undefined,
  });

  beforeEach(() => {
    vi.resetModules();
  });

  it("uses the renderer version in sanitized object keys", async () => {
    const { companyOgCacheKey } = await import("../company-og-cache");

    expect(companyOgCacheKey("EN", "Acme, Inc.")).toBe(
      "og/company/renderer123/en/acme-inc.png",
    );
  });

  it("exposes the explicit bypass knob", async () => {
    setTestEnv({ COMPANY_OG_CACHE_BYPASS: "1" });
    const { shouldBypassCompanyOgCache } = await import("../company-og-cache");

    expect(shouldBypassCompanyOgCache()).toBe(true);
  });

  it("soft-disables reads and writes when R2 is not configured", async () => {
    const { readCompanyOgCache, writeCompanyOgCache } = await import("../company-og-cache");

    await expect(readCompanyOgCache("og/company/x/en/acme.png")).resolves.toBeNull();
    await expect(
      writeCompanyOgCache("og/company/x/en/acme.png", new Uint8Array([1, 2, 3])),
    ).resolves.toBeUndefined();
  });
});
