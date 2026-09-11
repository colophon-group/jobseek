import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const read = (path: string) => readFileSync(`${root}/${path}`, "utf8");

describe("unlisted watchlist sharing contract", () => {
  it("uses a new default-private database flag instead of grandfathered is_public", () => {
    const schema = read("src/db/schema.ts");
    const migration = read("drizzle/0089_watchlist_unlisted_sharing.sql");
    expect(schema).toContain('shareEnabled: boolean("share_enabled").default(false).notNull()');
    expect(migration).toContain("ADD COLUMN share_enabled boolean DEFAULT false NOT NULL");
  });

  it("appends the sharing migration as the latest monotonic journal entry", () => {
    const journal = JSON.parse(read("drizzle/meta/_journal.json")) as {
      entries: Array<{
        idx: number;
        version: string;
        when: number;
        tag: string;
        breakpoints: boolean;
      }>;
    };

    expect(journal.entries.at(-1)).toEqual({
      idx: 77,
      version: "7",
      when: 1_789_050_000_000,
      tag: "0089_watchlist_unlisted_sharing",
      breakpoints: true,
    });
    expect(journal.entries.at(-2)?.when).toBeLessThan(
      journal.entries.at(-1)?.when ?? 0,
    );
  });

  it("authorizes the opaque id lookup only when explicit sharing is enabled", () => {
    const service = read("src/lib/services/watchlists.ts");
    const start = service.indexOf("export async function getSharedWatchlistById");
    const end = service.indexOf("/** Bounded compatibility lookup", start);
    const reader = service.slice(start, end);
    expect(reader).toContain(
      "w.id = ${normalizedWatchlistId} AND w.share_enabled = true",
    );
    expect(reader).not.toContain("w.is_public");
    expect(reader).not.toContain("lastAccessedAt");
    expect(reader).not.toContain("owner_id");
    expect(reader).not.toContain("alerts_enabled");
    expect(reader).not.toContain("source_watchlist_id");
    expect(reader).not.toContain("created_at");
  });

  it("keeps the shared UUID page unlisted from search engines", () => {
    const page = read("app/[lang]/(app)/watchlists/[watchlistId]/page.tsx");
    expect(page).toContain("robots: { index: false, follow: false }");
    const overviewPage = read("app/[lang]/(app)/watchlists/page.tsx");
    expect(overviewPage).toContain("robots: { index: false, follow: false }");
    expect(page).toContain('referrer: "no-referrer"');
  });
});
