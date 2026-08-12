import { describe, expect, it } from "vitest";
import { isPlausiblePublicWatchlistPath } from "@/lib/public-watchlist-path";

describe("isPlausiblePublicWatchlistPath", () => {
  it.each([
    ["alice", "big-tech-jobs"],
    ["user-123", "watchlist"],
    ["LegacyUser", "jobs-2"],
    ["abc", "a"],
  ])("accepts persisted URL shapes: %s/%s", (userSlug, watchlistSlug) => {
    expect(isPlausiblePublicWatchlistPath(userSlug, watchlistSlug)).toBe(true);
  });

  it.each([
    ["wp-admin", "install.php"],
    ["wp-admin", "install"],
    ["phpmyadmin", "index"],
    ["alice", ".env"],
    ["alice", "wp-login.php"],
    [".git", "config"],
    ["ab", "jobs"],
    ["alice", ""],
    ["alice", "Uppercase-Watchlist"],
    ["alice", `${"a".repeat(61)}`],
  ])("rejects impossible or scanner-shaped URLs: %s/%s", (userSlug, watchlistSlug) => {
    expect(isPlausiblePublicWatchlistPath(userSlug, watchlistSlug)).toBe(false);
  });
});
