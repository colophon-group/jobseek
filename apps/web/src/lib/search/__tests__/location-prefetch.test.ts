import { afterEach, describe, expect, it, vi } from "vitest";
import type { GlobalLocationsPage } from "@/lib/actions/locations";
import {
  _clearLocationsPrefetchCache,
  getCachedLocationsFirstPage,
  getCachedLocationsFirstPageSync,
  prefetchLocationsFirstPage,
} from "../location-prefetch";

function _page(overrides: Partial<GlobalLocationsPage> = {}): GlobalLocationsPage {
  return {
    macros: [],
    countries: [],
    nextCursor: null,
    totalCountries: 0,
    ...overrides,
  };
}

afterEach(() => {
  _clearLocationsPrefetchCache();
  vi.useRealTimers();
});

describe("location-prefetch (#3031)", () => {
  it("returns null on a cold cache", () => {
    expect(getCachedLocationsFirstPage("en", undefined)).toBeNull();
    expect(getCachedLocationsFirstPageSync("en", undefined)).toBeNull();
  });

  it("caches a resolved fetcher result so a second call returns synchronously", async () => {
    const fetcher = vi.fn(async () => _page({ totalCountries: 137 }));
    const first = await prefetchLocationsFirstPage("en", undefined, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(first.totalCountries).toBe(137);

    // Sync getter sees the resolved value
    const sync = getCachedLocationsFirstPageSync("en", undefined);
    expect(sync).not.toBeNull();
    expect(sync?.totalCountries).toBe(137);

    // Second prefetch is a no-op (fetcher not called again)
    const second = await prefetchLocationsFirstPage("en", undefined, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
  });

  it("deduplicates an in-flight prefetch — concurrent callers share one fetch", async () => {
    let resolveFetch!: (page: GlobalLocationsPage) => void;
    const pending = new Promise<GlobalLocationsPage>((res) => {
      resolveFetch = res;
    });
    const fetcher = vi.fn(() => pending);

    const a = prefetchLocationsFirstPage("en", undefined, fetcher);
    const b = prefetchLocationsFirstPage("en", undefined, fetcher);
    const cached = getCachedLocationsFirstPage("en", undefined);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
    expect(cached).toBe(a);
    // Sync getter must return null while in-flight (we don't have a value yet)
    expect(getCachedLocationsFirstPageSync("en", undefined)).toBeNull();

    resolveFetch(_page({ totalCountries: 5 }));
    const v = await a;
    expect(v.totalCountries).toBe(5);
    // After resolve, sync getter returns the value
    expect(getCachedLocationsFirstPageSync("en", undefined)).toEqual(v);
  });

  it("treats filter shapes with the same logical content as the same cache key", async () => {
    const fetcher = vi.fn(async () => _page({ totalCountries: 42 }));
    await prefetchLocationsFirstPage(
      "en",
      { keywords: ["a", "b"], languages: ["en"] },
      fetcher,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);

    // Same filters, different array order — must hit the same cache slot
    await prefetchLocationsFirstPage(
      "en",
      { languages: ["en"], keywords: ["b", "a"] },
      fetcher,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats different filter shapes as different cache keys", async () => {
    const fetcher = vi.fn(async (_locale: string, _cursor: number, filters: unknown) => {
      const total = (filters as { keywords?: string[] } | undefined)?.keywords?.length ?? 0;
      return _page({ totalCountries: total });
    });
    const a = await prefetchLocationsFirstPage("en", { keywords: ["a"] }, fetcher);
    const b = await prefetchLocationsFirstPage("en", { keywords: ["a", "b"] }, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(a.totalCountries).toBe(1);
    expect(b.totalCountries).toBe(2);
  });

  it("evicts the cache slot when a fetcher rejects so the next caller retries", async () => {
    const fetcher = vi.fn(async () => {
      throw new Error("upstream down");
    });

    await expect(
      prefetchLocationsFirstPage("en", undefined, fetcher),
    ).rejects.toThrow("upstream down");

    // Slot must be gone — sync getter returns null, second prefetch
    // calls fetcher again instead of awaiting the (rejected) promise.
    expect(getCachedLocationsFirstPageSync("en", undefined)).toBeNull();

    const fetcher2 = vi.fn(async () => _page({ totalCountries: 1 }));
    const v = await prefetchLocationsFirstPage("en", undefined, fetcher2);
    expect(v.totalCountries).toBe(1);
    expect(fetcher2).toHaveBeenCalledTimes(1);
  });

  it("expires resolved entries after the TTL window", async () => {
    vi.useFakeTimers({ now: 0 });
    const fetcher = vi.fn(async () => _page({ totalCountries: 7 }));
    await prefetchLocationsFirstPage("en", undefined, fetcher);
    // Within TTL — still resolved
    vi.setSystemTime(4 * 60 * 1000);
    expect(getCachedLocationsFirstPageSync("en", undefined)).not.toBeNull();
    // After TTL (5min) — expired
    vi.setSystemTime(6 * 60 * 1000);
    expect(getCachedLocationsFirstPageSync("en", undefined)).toBeNull();
    expect(getCachedLocationsFirstPage("en", undefined)).toBeNull();
    // Re-prefetch should call fetcher again
    await prefetchLocationsFirstPage("en", undefined, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
