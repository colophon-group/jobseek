import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getSessionUserId: vi.fn<() => Promise<string | null>>(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

import { GET } from "./route";

const NOW_SECONDS = 2_000_000_000;
const PARENT_KEY = "test-parent-key-abcdef0123456789";

withTestEnv({
  TYPESENSE_BROWSER_PARENT_KEY: PARENT_KEY,
  TYPESENSE_HOST: "typesense.example",
  TYPESENSE_PORT: "443",
  TYPESENSE_PROTOCOL: "https",
});

function decodeEmbed(apiKey: string): Record<string, unknown> {
  const decoded = Buffer.from(apiKey, "base64").toString("utf-8");
  return JSON.parse(decoded.slice(48)) as Record<string, unknown>;
}

describe("GET /api/typesense-key", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW_SECONDS * 1000 + 789);
    mocks.getSessionUserId.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("signs anonymous expiry and derives metadata from the same timestamp", async () => {
    mocks.getSessionUserId.mockResolvedValue(null);

    const response = await GET();
    const body = (await response.json()) as { apiKey: string; expiresAt: number };

    expect(response.status).toBe(200);
    expect(decodeEmbed(body.apiKey)).toEqual({
      use_cache: true,
      expires_at: NOW_SECONDS + 300,
    });
    expect(body.expiresAt).toBe((NOW_SECONDS + 300) * 1000);
    expect(response.headers.get("cache-control")).toBe(
      "public, s-maxage=150, max-age=0",
    );
  });

  it("signs authenticated expiry beyond the private response cache", async () => {
    mocks.getSessionUserId.mockResolvedValue("user-1");

    const response = await GET();
    const body = (await response.json()) as { apiKey: string; expiresAt: number };

    expect(decodeEmbed(body.apiKey)).toEqual({
      use_cache: true,
      expires_at: NOW_SECONDS + 600,
    });
    expect(body.expiresAt).toBe((NOW_SECONDS + 600) * 1000);
    expect(response.headers.get("cache-control")).toBe("private, max-age=300");
  });

  it("does not mint a key when the browser parent is unavailable", async () => {
    mocks.getSessionUserId.mockResolvedValue(null);
    setTestEnv({ TYPESENSE_BROWSER_PARENT_KEY: undefined });

    const response = await GET();

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      error: "search not configured",
    });
  });
});
