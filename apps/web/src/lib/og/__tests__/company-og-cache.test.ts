import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const ORIGINAL_RENDERER_VERSION = process.env.COMPANY_OG_RENDERER_VERSION;
const ORIGINAL_CACHE_BYPASS = process.env.COMPANY_OG_CACHE_BYPASS;
const ORIGINAL_R2_ENDPOINT_URL = process.env.R2_ENDPOINT_URL;
const ORIGINAL_R2_ACCESS_KEY_ID = process.env.R2_ACCESS_KEY_ID;
const ORIGINAL_R2_SECRET_ACCESS_KEY = process.env.R2_SECRET_ACCESS_KEY;
const ORIGINAL_R2_BUCKET = process.env.R2_BUCKET;

describe("company OG cache", () => {
  beforeEach(() => {
    vi.resetModules();
    process.env.COMPANY_OG_RENDERER_VERSION = "renderer123";
    delete process.env.COMPANY_OG_CACHE_BYPASS;
    delete process.env.R2_ENDPOINT_URL;
    delete process.env.R2_ACCESS_KEY_ID;
    delete process.env.R2_SECRET_ACCESS_KEY;
    delete process.env.R2_BUCKET;
  });

  afterEach(() => {
    if (ORIGINAL_RENDERER_VERSION === undefined) {
      delete process.env.COMPANY_OG_RENDERER_VERSION;
    } else {
      process.env.COMPANY_OG_RENDERER_VERSION = ORIGINAL_RENDERER_VERSION;
    }
    if (ORIGINAL_CACHE_BYPASS === undefined) {
      delete process.env.COMPANY_OG_CACHE_BYPASS;
    } else {
      process.env.COMPANY_OG_CACHE_BYPASS = ORIGINAL_CACHE_BYPASS;
    }
    if (ORIGINAL_R2_ENDPOINT_URL === undefined) {
      delete process.env.R2_ENDPOINT_URL;
    } else {
      process.env.R2_ENDPOINT_URL = ORIGINAL_R2_ENDPOINT_URL;
    }
    if (ORIGINAL_R2_ACCESS_KEY_ID === undefined) {
      delete process.env.R2_ACCESS_KEY_ID;
    } else {
      process.env.R2_ACCESS_KEY_ID = ORIGINAL_R2_ACCESS_KEY_ID;
    }
    if (ORIGINAL_R2_SECRET_ACCESS_KEY === undefined) {
      delete process.env.R2_SECRET_ACCESS_KEY;
    } else {
      process.env.R2_SECRET_ACCESS_KEY = ORIGINAL_R2_SECRET_ACCESS_KEY;
    }
    if (ORIGINAL_R2_BUCKET === undefined) {
      delete process.env.R2_BUCKET;
    } else {
      process.env.R2_BUCKET = ORIGINAL_R2_BUCKET;
    }
  });

  it("uses the renderer version in sanitized object keys", async () => {
    const { companyOgCacheKey } = await import("../company-og-cache");

    expect(companyOgCacheKey("EN", "Acme, Inc.")).toBe(
      "og/company/renderer123/en/acme-inc.png",
    );
  });

  it("exposes the explicit bypass knob", async () => {
    process.env.COMPANY_OG_CACHE_BYPASS = "1";
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
