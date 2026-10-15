import { useState } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AiFilterUiState } from "@/lib/ai-filter/ui-contract";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";
import { useAiFilteredResults } from "@/components/watchlist/use-ai-filtered-results";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/safe-external-error", () => ({ logExternalError: vi.fn() }));

const initial: AiFilterUiState = {
  watchlistId: "11111111-1111-4111-8111-111111111111",
  queryVersionId: "22222222-2222-4222-8222-222222222222",
  enabled: true,
  entitled: true,
  query: "Companies ready to hire outside the US",
  queryRevision: 1,
  status: "processing",
  counts: { accepted: 0, rejected: 0, total: 0 },
  progress: { selectionOffset: 0, scannedCount: 0, completedCount: 0, stopReason: null },
  lastCaughtUpAt: null,
  latestEventSequence: 1,
};
const languages: string[] = [];
const posting: WatchlistPostingEntry = {
  id: "33333333-3333-4333-8333-333333333333",
  title: "Newly accepted role",
  sourceUrl: "https://example.com/job",
  firstSeenAt: "2026-10-15T15:00:00Z",
  isActive: true,
  company: { id: "company", name: "Example", slug: "example", icon: null },
};
const response = (body: unknown) => ({ ok: true, json: async () => body }) as Response;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-15T15:38:52Z"));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("Live narrowed result delivery", () => {
  function fixture(withExistingMatch: boolean) {
    let persisted = withExistingMatch
      ? { ...initial, counts: { accepted: 1, rejected: 4, total: 5 } }
      : initial;
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return response({ state: persisted, workflow: { runId: "demand:fixture" } });
      }
      if (String(url).includes("/decisions?")) {
        return response({
          decisions: persisted.counts.accepted > 0 ? [{ posting }] : [],
          state: persisted,
          total: persisted.counts.accepted,
          nextOffset: persisted.counts.accepted,
          hasMore: true,
        });
      }
      return response(persisted);
    });
    vi.stubGlobal("fetch", fetchMock);
    const initialPage = withExistingMatch ? {
      queryVersionId: initial.queryVersionId,
      postings: [{ ...posting, id: "old", title: "Existing match" }],
      total: 1,
      nextOffset: 1,
      hasMore: true,
    } : null;
    const hook = renderHook(() => {
      const [state, setState] = useState(persisted);
      return useAiFilteredResults({
        active: true,
        watchlistId: initial.watchlistId,
        state,
        initialPage,
        scopeKey: "unchanged-scope",
        scopeReady: true,
        jobLanguages: languages,
        locale: "en",
        readOnly: false,
        onStateChange: setState,
      });
    });
    return {
      ...hook,
      fetchMock,
      persistMatch() {
        persisted = {
          ...initial,
          counts: { accepted: withExistingMatch ? 2 : 1, rejected: 4, total: withExistingMatch ? 6 : 5 },
          latestEventSequence: 2,
        };
      },
    };
  }

  it("shows the first matching batch immediately without waiting for twenty results", async () => {
    const f = fixture(false);
    await act(async () => {});
    f.persistMatch();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(f.result.current.postings.map(post => post.title)).toEqual(["Newly accepted role"]);
    expect(f.result.current.isLoading).toBe(false);
    expect(f.result.current.total).toBe(1);
  });

  it("shows a first match arriving after foreground polling ends", async () => {
    const f = fixture(false);
    await act(async () => {});
    // Flush each timer in a separate act so React can transition to background polling.
    for (let i = 0; i < 30; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    }
    expect(f.result.current.isLoading).toBe(false);
    expect(f.result.current.postings).toHaveLength(0);
    const reads = f.fetchMock.mock.calls.filter(([url]) => String(url).includes("/decisions?")).length;
    f.persistMatch();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(f.result.current.total).toBe(1);
    expect(f.result.current.evaluated).toBe(5);
    expect(f.result.current.postings).toHaveLength(1);
    expect(f.fetchMock.mock.calls.filter(([url]) => String(url).includes("/decisions?")).length).toBeGreaterThan(reads);
  });

  it("appends new background matches without a pagination event", async () => {
    const f = fixture(true);
    await act(async () => {});
    f.persistMatch();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(f.result.current.total).toBe(2);
    expect(f.result.current.postings.map(p => p.title)).toEqual(["Existing match", "Newly accepted role"]);
    expect(f.fetchMock.mock.calls.some(([url]) => String(url).includes("/decisions?"))).toBe(true);
  });

  it("keeps reading an open processing feed beyond the former polling limit", async () => {
    const f = fixture(true);
    await act(async () => {});
    for (let i = 0; i < 25; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    }
    const reads = f.fetchMock.mock.calls.length;
    f.persistMatch();
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
    expect(f.fetchMock.mock.calls.length).toBeGreaterThan(reads);
    expect(f.result.current.total).toBe(2);
    expect(f.result.current.hasMore).toBe(true);
  });
});

