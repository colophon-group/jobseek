import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  assertOfflineImportGraph,
  collectMentionRefs,
  validateWatchlistSnapshot,
} from "../../../script/check-blog-mention-snapshot";

let temporaryDirectory: string | null = null;

afterEach(async () => {
  if (temporaryDirectory) {
    await rm(temporaryDirectory, { recursive: true, force: true });
    temporaryDirectory = null;
  }
});
async function temporaryModule(source: string): Promise<string> {
  temporaryDirectory ??= await mkdtemp(join(tmpdir(), "blog-mention-gate-"));
  const path = join(temporaryDirectory, "entry.ts");
  await writeFile(path, source, "utf8");
  return path;
}

describe("blog mention build gate", () => {
  const path = "/watchlists/12345678-1234-4234-8234-123456789abc";
  const watchlist = { slug: "swiss-robotics", name: "Swiss robotics employers", path };
  const watchlistSnapshot = (records: unknown[] = [watchlist]) => ({ schemaVersion: 1, watchlists: records });

  it("collects watchlist references separately and deduplicates locale copies", () => {
    const refs = collectMentionRefs([
      '<Watchlist slug="swiss-robotics" /> <Company slug="swiss-robotics" />',
      "<Watchlist slug='swiss-robotics' />",
    ]);
    expect([...refs.watchlists]).toEqual(["swiss-robotics"]);
    expect([...refs.companies]).toEqual(["swiss-robotics"]);
    expect(() => validateWatchlistSnapshot(watchlistSnapshot(), refs.watchlists)).not.toThrow();
  });

  it.each([
    '<Watchlist />',
    '<Watchlist slug={slug} />',
    '<Watchlist slug="Not Canonical" />',
    '<Watchlist slug="swiss-robotics" slug="other" />',
    '<Watchlist slug="swiss-robotics" {...props} />',
  ])("rejects invalid watchlist references: %s", (source) => {
    expect(() => collectMentionRefs([source])).toThrow("canonical literal slug");
  });

  it("requires exactly one reviewed record for every used watchlist", () => {
    const refs = new Set([watchlist.slug]);
    expect(() => validateWatchlistSnapshot(watchlistSnapshot([]), refs)).toThrow("missing=[swiss-robotics]");
    expect(() => validateWatchlistSnapshot(watchlistSnapshot(), new Set())).toThrow("stale=[swiss-robotics]");
    expect(() => validateWatchlistSnapshot(watchlistSnapshot([watchlist, watchlist]), refs)).toThrow("duplicate");
    expect(() => validateWatchlistSnapshot(watchlistSnapshot([
      watchlist,
      { ...watchlist, path: "/watchlists/12345678-1234-4234-8234-123456789abd" },
    ]), refs)).toThrow("duplicates");
  });

  it.each([
    "https://jseek.co/watchlists/12345678-1234-4234-8234-123456789abc",
    "//example.com/watchlists/12345678-1234-4234-8234-123456789abc",
    "/en/watchlists/12345678-1234-4234-8234-123456789abc",
    "/alice/swiss-robotics",
    "/watchlists/not-a-uuid",
    "/watchlists/12345678-1234-4234-8234-123456789ABC",
    `${path}?token=secret`,
    `${path}#jobs`,
    `${path}/`,
    "/watchlists/../settings",
    "/watchlists/%2e%2e/settings",
    "javascript:alert(1)",
  ])("rejects noncanonical or unsafe watchlist URLs: %s", (invalidPath) => {
    expect(() => validateWatchlistSnapshot(watchlistSnapshot([{ ...watchlist, path: invalidPath }]), new Set([watchlist.slug])))
      .toThrow("canonical internal shared watchlist path");
  });

  it.each([
    { ...watchlist, name: "" },
    { ...watchlist, name: "  " },
    { ...watchlist, slug: "Invalid Slug" },
    { ...watchlist, activeJobCount: 12 },
    { ...watchlist, token: "not-allowed" },
    null,
  ])("rejects malformed or unapproved watchlist metadata: %j", (record) => {
    expect(() => validateWatchlistSnapshot(watchlistSnapshot([record]), new Set([watchlist.slug]))).toThrow();
  });

  it.each([null, [], {}, { schemaVersion: 2, watchlists: [] }, { schemaVersion: 1, watchlists: null }])(
    "rejects unsupported watchlist snapshot shapes: %j", (data) => {
      expect(() => validateWatchlistSnapshot(data, new Set())).toThrow();
    },
  );

  it("deduplicates identical entity references across locale sources", () => {
    const refs = collectMentionRefs([
      '<Company slug="anthropic" />',
      '<CompanyCard slug="anthropic" />',
    ]);

    expect([...refs.companies]).toEqual(["anthropic"]);
  });

  it("rejects non-literal or malformed mention identifiers", () => {
    expect(() => collectMentionRefs(['<Company slug="Not Canonical" />']))
      .toThrow("canonical literal slug");
  });

  it("blocks a direct fetch fallback", async () => {
    const entry = await temporaryModule(
      'export async function resolve() { return fetch("https://production.invalid"); }',
    );
    await expect(assertOfflineImportGraph(entry)).rejects.toThrow("calls fetch()");
  });

  it("blocks unapproved client packages anywhere in the local import graph", async () => {
    temporaryDirectory = await mkdtemp(join(tmpdir(), "blog-mention-gate-"));
    const entry = join(temporaryDirectory, "entry.ts");
    const nested = join(temporaryDirectory, "nested.ts");
    await writeFile(entry, 'export { resolve } from "./nested";', "utf8");
    await writeFile(nested, 'import Client from "typesense"; export const resolve = Client;', "utf8");

    await expect(assertOfflineImportGraph(entry)).rejects.toThrow(
      "imports unapproved package typesense",
    );
  });
});
