import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

const s3Mock = vi.hoisted(() => ({
  send: vi.fn(),
}));

vi.mock("@aws-sdk/client-s3", () => {
  class MockGetObjectCommand {
    input: unknown;

    constructor(input: unknown) {
      this.input = input;
    }
  }

  class MockPutObjectCommand {
    input: unknown;

    constructor(input: unknown) {
      this.input = input;
    }
  }

  class MockS3Client {
    send = s3Mock.send;
  }

  return {
    GetObjectCommand: MockGetObjectCommand,
    PutObjectCommand: MockPutObjectCommand,
    S3Client: MockS3Client,
  };
});

function configureR2Env() {
  setTestEnv({
    R2_ENDPOINT_URL: "https://r2.example.test",
    R2_ACCESS_KEY_ID: "access-key",
    R2_SECRET_ACCESS_KEY: "secret-key",
    R2_BUCKET: "bucket",
  });
}

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
    s3Mock.send.mockReset();
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

  it("reads R2 bodies with transformToByteArray", async () => {
    configureR2Env();
    const transformToByteArray = vi.fn().mockResolvedValue(new Uint8Array([4, 5, 6]));
    s3Mock.send.mockResolvedValueOnce({ Body: { transformToByteArray } });
    const { readCompanyOgCache } = await import("../company-og-cache");

    const bytes = await readCompanyOgCache("og/company/x/en/acme.png");

    expect(Array.from(bytes ?? [])).toEqual([4, 5, 6]);
    expect(transformToByteArray).toHaveBeenCalledOnce();
    expect(s3Mock.send).toHaveBeenCalledOnce();
  });

  it("reads iterable R2 bodies", async () => {
    configureR2Env();
    async function* chunks() {
      yield new Uint8Array([1, 2]);
      yield new Uint8Array([3]);
    }
    s3Mock.send.mockResolvedValueOnce({ Body: chunks() });
    const { readCompanyOgCache } = await import("../company-og-cache");

    const bytes = await readCompanyOgCache("og/company/x/en/acme.png");

    expect(Array.from(bytes ?? [])).toEqual([1, 2, 3]);
    expect(s3Mock.send).toHaveBeenCalledOnce();
  });
});
