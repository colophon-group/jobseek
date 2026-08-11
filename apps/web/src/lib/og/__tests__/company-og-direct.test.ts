import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({ cacheLife: vi.fn() }));

import { checkCompanyOgNamespaceComplete } from "../company-og-direct";

describe("direct company OG completion gate", () => {
  it("accepts only a matching successful completion marker", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      complete: true,
      rendererVersion: "render-v1",
      sourceVersion: "source-v1",
    }), { status: 200 }));

    await expect(checkCompanyOgNamespaceComplete(
      "https://assets.example.test",
      "render-v1",
      "source-v1",
      fetcher,
    )).resolves.toBe(true);
    expect(fetcher).toHaveBeenCalledWith(
      "https://assets.example.test/og/company/render-v1/_complete/source-v1.json",
      {
        cache: "no-store",
        headers: { Accept: "application/json" },
      },
    );
  });

  it("keeps the Vercel route fallback for missing or mismatched markers", async () => {
    const missing = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    const mismatched = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      complete: true,
      rendererVersion: "render-v0",
      sourceVersion: "source-v1",
    }), { status: 200 }));

    await expect(checkCompanyOgNamespaceComplete(
      "https://assets.example.test",
      "render-v1",
      "source-v1",
      missing,
    )).resolves.toBe(false);
    await expect(checkCompanyOgNamespaceComplete(
      "https://assets.example.test",
      "render-v1",
      "source-v1",
      mismatched,
    )).resolves.toBe(false);
  });

  it("fails closed on public R2 transport errors", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("simulated outage"));

    await expect(checkCompanyOgNamespaceComplete(
      "https://assets.example.test",
      "render-v1",
      "source-v1",
      fetcher,
    )).resolves.toBe(false);
  });
});
