import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  connection: vi.fn<() => Promise<void>>(),
}));

vi.mock("next/server", () => ({
  connection: mocks.connection,
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
    mocks.connection.mockReset();
    mocks.connection.mockResolvedValue();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("signs a shared expiry and derives metadata from the same timestamp", async () => {
    const response = await GET();
    const body = (await response.json()) as { apiKey: string; expiresAt: number };

    expect(response.status).toBe(200);
    expect(decodeEmbed(body.apiKey)).toEqual({
      use_cache: true,
      expires_at: NOW_SECONDS + 600,
    });
    expect(body.expiresAt).toBe((NOW_SECONDS + 600) * 1000);
    expect(mocks.connection).toHaveBeenCalledOnce();
    expect(response.headers.get("cache-control")).toBe(
      "public, max-age=0, must-revalidate",
    );
    const cdnCacheControl = response.headers.get("vercel-cdn-cache-control");
    expect(cdnCacheControl).toBe("public, max-age=510, must-revalidate");
    expect(cdnCacheControl).not.toContain("s-maxage");

    const cdnMaxAge = Number(cdnCacheControl?.match(/max-age=(\d+)/)?.[1]);
    expect(body.expiresAt / 1000 - NOW_SECONDS - cdnMaxAge).toBe(90);
  });

  it("does not mint a key when the browser parent is unavailable", async () => {
    setTestEnv({ TYPESENSE_BROWSER_PARENT_KEY: undefined });

    const response = await GET();

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      error: "search not configured",
    });
  });
});
