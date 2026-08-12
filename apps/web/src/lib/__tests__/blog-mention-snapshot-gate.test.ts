import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  assertOfflineImportGraph,
  collectMentionRefs,
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
  it("deduplicates identical entity references across locale sources", () => {
    const refs = collectMentionRefs([
      '<Company slug="anthropic" /> <WatchlistCard owner="team" slug="ai" />',
      '<CompanyCard slug="anthropic" /> <Watchlist owner="team" slug="ai" />',
    ]);

    expect([...refs.companies]).toEqual(["anthropic"]);
    expect([...refs.watchlists]).toEqual(["team/ai"]);
  });

  it("rejects non-literal or malformed mention identifiers", () => {
    expect(() => collectMentionRefs(['<Company slug="Not Canonical" />']))
      .toThrow("canonical literal slug");
    expect(() => collectMentionRefs(['<Watchlist owner="Team" slug="ai" />']))
      .toThrow("canonical literal owner");
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
