import { beforeEach, describe, expect, it, vi } from "vitest";
import { Readable } from "node:stream";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  httpsRequest: vi.fn(),
  s3Send: vi.fn(),
}));

vi.mock("node:https", () => ({
  default: { request: mocks.httpsRequest },
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
    send = mocks.s3Send;
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

function mockPublicResponse(statusCode: number, bytes: Uint8Array = new Uint8Array()) {
  mocks.httpsRequest.mockImplementation(
    (_url: string, _options: unknown, callback: (response: unknown) => void) => {
      const response = Readable.from(bytes.length > 0 ? [bytes] : []);
      Object.assign(response, { statusCode });
      callback(response);
      return {
        destroy: vi.fn(),
        end: vi.fn(),
        on: vi.fn(),
        setTimeout: vi.fn(),
      };
    },
  );
}

describe("company OG cache", () => {
  withTestEnv({
    COMPANY_OG_RENDERER_VERSION: "renderer123",
    COMPANY_OG_CACHE_BYPASS: undefined,
    R2_DOMAIN_URL: undefined,
    R2_ENDPOINT_URL: undefined,
    R2_ACCESS_KEY_ID: undefined,
    R2_SECRET_ACCESS_KEY: undefined,
    R2_BUCKET: undefined,
  });

  beforeEach(() => {
    vi.resetModules();
    mocks.httpsRequest.mockReset();
    mocks.s3Send.mockReset();
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

  it("reads public R2 bytes with lightweight built-in HTTPS", async () => {
    setTestEnv({ R2_DOMAIN_URL: "https://assets.example.test" });
    mockPublicResponse(200, new Uint8Array([7, 8, 9]));
    const { readCompanyOgCache } = await import("../company-og-cache");

    const bytes = await readCompanyOgCache("og/company/x/en/acme labs.png");

    expect(Array.from(bytes ?? [])).toEqual([7, 8, 9]);
    expect(mocks.httpsRequest).toHaveBeenCalledWith(
      "https://assets.example.test/og/company/x/en/acme%20labs.png",
      { method: "GET" },
      expect.any(Function),
    );
    expect(mocks.s3Send).not.toHaveBeenCalled();
  });

  it("falls back to a signed read when the public CDN has a stale 404", async () => {
    setTestEnv({ R2_DOMAIN_URL: "https://assets.example.test" });
    configureR2Env();
    mockPublicResponse(404);
    const transformToByteArray = vi.fn().mockResolvedValue(new Uint8Array([3, 2, 1]));
    mocks.s3Send.mockResolvedValueOnce({ Body: { transformToByteArray } });
    const { readCompanyOgCache } = await import("../company-og-cache");

    const bytes = await readCompanyOgCache("og/company/x/en/prewarmed.png");

    expect(Array.from(bytes ?? [])).toEqual([3, 2, 1]);
    expect(mocks.s3Send).toHaveBeenCalledOnce();
  });

  it("soft-disables reads and writes when R2 is not configured", async () => {
    const { readCompanyOgCache, writeCompanyOgCache } = await import("../company-og-cache");

    await expect(readCompanyOgCache("og/company/x/en/acme.png")).resolves.toBeNull();
    await expect(
      writeCompanyOgCache("og/company/x/en/acme.png", new Uint8Array([1, 2, 3])),
    ).resolves.toBeUndefined();
  });

  it("falls back to a signed R2 read when no public domain is configured", async () => {
    configureR2Env();
    const transformToByteArray = vi.fn().mockResolvedValue(new Uint8Array([4, 5, 6]));
    mocks.s3Send.mockResolvedValueOnce({ Body: { transformToByteArray } });
    const { readCompanyOgCache } = await import("../company-og-cache");

    const bytes = await readCompanyOgCache("og/company/x/en/acme.png");

    expect(Array.from(bytes ?? [])).toEqual([4, 5, 6]);
    expect(transformToByteArray).toHaveBeenCalledOnce();
    expect(mocks.s3Send).toHaveBeenCalledOnce();
  });

  it("reads iterable R2 bodies", async () => {
    configureR2Env();
    async function* chunks() {
      yield new Uint8Array([1, 2]);
      yield new Uint8Array([3]);
    }
    mocks.s3Send.mockResolvedValueOnce({ Body: chunks() });
    const { readCompanyOgCache } = await import("../company-og-cache");

    const bytes = await readCompanyOgCache("og/company/x/en/acme.png");

    expect(Array.from(bytes ?? [])).toEqual([1, 2, 3]);
    expect(mocks.s3Send).toHaveBeenCalledOnce();
  });

  it("lazy-loads the AWS SDK for cache writes", async () => {
    configureR2Env();
    mocks.s3Send.mockResolvedValueOnce({});
    const { writeCompanyOgCache } = await import("../company-og-cache");

    await writeCompanyOgCache(
      "og/company/x/en/acme.png",
      new Uint8Array([1, 2, 3]),
    );

    expect(mocks.s3Send).toHaveBeenCalledOnce();
  });
});
