import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingWatchlist,
  readPendingWatchlists,
  removePendingWatchlist,
  stagePendingWatchlist,
  takePendingWatchlist,
  takePendingWatchlists,
} from "./pending-watchlist";

const draft = {
  title: "Swiss internships",
  companyIds: [],
  filters: { anyCompany: true, locationSlugs: ["switzerland"] },
  isPublic: false as const,
};

describe("pending watchlist session handoff", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  it("stages and consumes a create exactly once", () => {
    expect(stagePendingWatchlist({ kind: "create", draft })).toBe(true);
    expect(takePendingWatchlist()).toEqual({ kind: "create", draft });
    expect(takePendingWatchlist()).toBeNull();
  });

  it("stages a shared-watchlist clone", () => {
    const watchlistId = "11111111-1111-4111-8111-111111111111";
    stagePendingWatchlist({ kind: "clone", watchlistId, title: "Finance" });
    expect(takePendingWatchlist()).toEqual({
      kind: "clone",
      watchlistId,
      title: "Finance",
    });
  });

  it("keeps multiple anonymous watchlists in creation order", () => {
    const watchlistId = "11111111-1111-4111-8111-111111111111";
    expect(stagePendingWatchlist({ kind: "create", draft })).toBe(true);
    expect(stagePendingWatchlist({ kind: "clone", watchlistId, title: "Finance" })).toBe(true);

    const entries = readPendingWatchlists();
    expect(entries).toHaveLength(2);
    expect(entries.map(({ intent }) => intent)).toEqual([
      { kind: "create", draft },
      { kind: "clone", watchlistId, title: "Finance" },
    ]);
    expect(takePendingWatchlists()).toEqual(entries.map(({ intent }) => intent));
    expect(readPendingWatchlists()).toEqual([]);
  });

  it("removes one anonymous watchlist without affecting the others", () => {
    const secondDraft = { ...draft, title: "Design roles" };
    stagePendingWatchlist({ kind: "create", draft });
    stagePendingWatchlist({ kind: "create", draft: secondDraft });
    const [first] = readPendingWatchlists();

    removePendingWatchlist(first.id);

    expect(readPendingWatchlists().map(({ intent }) => intent)).toEqual([
      { kind: "create", draft: secondDraft },
    ]);
  });

  it("discards malformed or expired browser state", () => {
    sessionStorage.setItem("jobseek:pending-watchlist:v1", "not-json");
    expect(takePendingWatchlist()).toBeNull();

    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-22T12:00:00Z"));
    stagePendingWatchlist({ kind: "create", draft });
    vi.advanceTimersByTime(2 * 60 * 60 * 1_000 + 1);
    expect(takePendingWatchlist()).toBeNull();
  });

  it("can explicitly discard a staged intent", () => {
    stagePendingWatchlist({ kind: "create", draft });
    clearPendingWatchlist();
    expect(takePendingWatchlist()).toBeNull();
  });
});
