import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { getTableColumns } from "drizzle-orm";
import { PgTimestamp } from "drizzle-orm/pg-core";

import {
  account,
  session,
  user,
  userPreferences,
  verification,
} from "@/db/schema";

/**
 * Coverage for #3204. Better-Auth + user_preferences columns used to be
 * declared as TZ-naive `timestamp(...)`, which made auth-token TTLs
 * silently drift whenever the writer and reader disagreed about the
 * session timezone. Both axes are pinned here:
 *
 * 1. Drizzle introspection — every affected column reports
 *    `withTimezone === true`, so the TS schema (which Better-Auth
 *    consumes via the drizzle adapter) keeps round-tripping `Date`
 *    instants correctly.
 * 2. Migration SQL — the 0079 hand-written migration includes the
 *    `AT TIME ZONE 'UTC'` clause for every column. That clause is
 *    load-bearing: without it Postgres uses the session TZ to
 *    reinterpret existing rows, which on Hetzner (Europe/Berlin)
 *    would shift every auth timestamp by 2 hours and reset every
 *    session.
 */

const AFFECTED_COLUMNS = [
  // user
  { table: user, tableName: "user", column: "created_at" },
  { table: user, tableName: "user", column: "updated_at" },
  // session
  { table: session, tableName: "session", column: "expires_at" },
  { table: session, tableName: "session", column: "created_at" },
  { table: session, tableName: "session", column: "updated_at" },
  // account
  { table: account, tableName: "account", column: "access_token_expires_at" },
  { table: account, tableName: "account", column: "refresh_token_expires_at" },
  { table: account, tableName: "account", column: "created_at" },
  { table: account, tableName: "account", column: "updated_at" },
  // verification
  { table: verification, tableName: "verification", column: "expires_at" },
  { table: verification, tableName: "verification", column: "created_at" },
  { table: verification, tableName: "verification", column: "updated_at" },
  // user_preferences
  {
    table: userPreferences,
    tableName: "user_preferences",
    column: "theme_updated_at",
  },
  {
    table: userPreferences,
    tableName: "user_preferences",
    column: "locale_updated_at",
  },
  {
    table: userPreferences,
    tableName: "user_preferences",
    column: "last_password_reset_at",
  },
  {
    table: userPreferences,
    tableName: "user_preferences",
    column: "updated_at",
  },
] as const;

function findColumn(table: unknown, sqlName: string) {
  const cols = getTableColumns(table as Parameters<typeof getTableColumns>[0]);
  for (const value of Object.values(cols)) {
    if ((value as { name: string }).name === sqlName) return value;
  }
  return undefined;
}

describe("Better-Auth + user_preferences TZ-aware schema (#3204)", () => {
  it.each(AFFECTED_COLUMNS)(
    "$tableName.$column is declared with withTimezone=true",
    ({ table, column }) => {
      const col = findColumn(table, column);
      expect(col, `column ${column} not found in schema`).toBeDefined();
      // Belt-and-braces: the column must be a PgTimestamp (not, say,
      // PgTimestampString) AND the timezone flag must be on.
      expect(col).toBeInstanceOf(PgTimestamp);
      expect((col as PgTimestamp<never>).withTimezone).toBe(true);
    },
  );
});

describe("0079_tz_aware_auth_columns migration uses AT TIME ZONE 'UTC'", () => {
  const migrationPath = join(
    __dirname,
    "..",
    "..",
    "..",
    "drizzle",
    "0079_tz_aware_auth_columns.sql",
  );
  const sql = readFileSync(migrationPath, "utf8");

  it.each(AFFECTED_COLUMNS)(
    "$tableName.$column has TYPE timestamp with time zone USING ... AT TIME ZONE 'UTC'",
    ({ tableName, column }) => {
      // Match the ALTER TABLE ... ALTER COLUMN ... USING line for this
      // (table, column) pair. The line MUST end in "AT TIME ZONE 'UTC'"
      // — anything else (default cast, AT TIME ZONE 'Europe/Berlin',
      // …) shifts existing rows by N hours.
      const pattern = new RegExp(
        `ALTER TABLE\\s+"${tableName}"[\\s\\S]*?` +
          `ALTER COLUMN\\s+"${column}"\\s+SET DATA TYPE\\s+timestamp with time zone\\s+` +
          `USING\\s+"${column}"\\s+AT TIME ZONE\\s+'UTC'`,
      );
      expect(sql).toMatch(pattern);
    },
  );

  // Strip SQL line comments so the prose header (which discusses
  // AT TIME ZONE 'UTC' in english) doesn't get counted as actual SQL.
  const sqlNoComments = sql
    .split("\n")
    .filter((line) => !line.trim().startsWith("--"))
    .join("\n");

  it("has exactly one AT TIME ZONE 'UTC' clause per affected column (in SQL, not comments)", () => {
    const matches = sqlNoComments.match(/AT TIME ZONE\s+'UTC'/g) ?? [];
    expect(matches).toHaveLength(AFFECTED_COLUMNS.length);
  });

  it("references only 'UTC' as the source timezone — no host-local TZ", () => {
    // Any AT TIME ZONE 'X' where X !== 'UTC' would silently reinterpret
    // rows using a non-UTC origin, which is exactly the bug.
    const tzClauses = sqlNoComments.match(/AT TIME ZONE\s+'([^']+)'/g) ?? [];
    for (const clause of tzClauses) {
      expect(clause).toMatch(/AT TIME ZONE\s+'UTC'/);
    }
  });

  it("is registered in drizzle's _journal.json", () => {
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
    const tags = journal.entries.map((e) => e.tag);
    expect(tags).toContain("0079_tz_aware_auth_columns");
  });
});
