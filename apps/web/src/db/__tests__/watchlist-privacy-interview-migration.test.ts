import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { getTableColumns } from "drizzle-orm";

import { watchlist } from "@/db/schema";

const migrationPath = join(
  __dirname,
  "..",
  "..",
  "..",
  "drizzle",
  "0081_private_watchlists_and_general_interviews.sql",
);
const migrationSql = readFileSync(migrationPath, "utf8");

describe("0081 private-watchlist and general-interview migration", () => {
  it("aligns the database interview CHECK with the general UI option", () => {
    expect(migrationSql).toMatch(
      /ADD CONSTRAINT application_interview_type_check[\s\S]*?'interview'/,
    );
  });

  it("makes private the database and Drizzle default", () => {
    expect(migrationSql).toMatch(
      /ALTER TABLE watchlist ALTER COLUMN is_public SET DEFAULT false/,
    );
    expect(getTableColumns(watchlist).isPublic.default).toBe(false);
  });

  it("is registered in drizzle's migration journal", () => {
    const journalPath = join(
      __dirname,
      "..",
      "..",
      "..",
      "drizzle",
      "meta",
      "_journal.json",
    );
    const journal = JSON.parse(readFileSync(journalPath, "utf8")) as {
      entries: { tag: string }[];
    };
    expect(journal.entries.map((entry) => entry.tag)).toContain(
      "0081_private_watchlists_and_general_interviews",
    );
  });
});
