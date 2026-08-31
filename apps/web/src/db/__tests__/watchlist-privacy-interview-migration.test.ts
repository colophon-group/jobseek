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
  "0083_reconcile_supabase_baseline.sql",
);
const migrationSql = readFileSync(migrationPath, "utf8");

const privacyMigrationPath = join(
  __dirname,
  "..",
  "..",
  "..",
  "drizzle",
  "0089_make_existing_watchlists_private.sql",
);
const privacyMigrationSql = readFileSync(privacyMigrationPath, "utf8");
const privacyRunner = readFileSync(
  join(
    __dirname,
    "..",
    "..",
    "..",
    "scripts",
    "watchlist-visibility-migration.ts",
  ),
  "utf8",
);
const nextConfig = readFileSync(
  join(__dirname, "..", "..", "..", "next.config.ts"),
  "utf8",
);

describe("0083 Supabase baseline reconciliation", () => {
  it("aligns the database interview CHECK with the general UI option", () => {
    expect(migrationSql).toMatch(
      /ADD CONSTRAINT application_interview_type_check[\s\S]*?'interview'/,
    );
  });

  it("makes private the database and Drizzle default", () => {
    expect(migrationSql).toMatch(
      /ALTER TABLE public\.watchlist\s+ALTER COLUMN is_public SET DEFAULT false/,
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
      "0083_reconcile_supabase_baseline",
    );
    expect(journal.entries.map((entry) => entry.tag)).not.toContain(
      "0080_experience_decimal_years",
    );
  });
});

describe("0089 legacy watchlist visibility migration", () => {
  it("requires reviewed rollout evidence and changes only public visibility", () => {
    expect(privacyMigrationSql).toContain(
      "pg_temp.jobseek_watchlist_privacy_attestation",
    );
    expect(privacyMigrationSql).toContain("PRIVATE-WATCHLISTS-0089");
    expect(privacyMigrationSql).toContain("private_mutations_deploy_sha");
    expect(privacyMigrationSql).toContain("route_cutover_deploy_sha");
    expect(privacyMigrationSql).toContain("route_cutover_approved_by");
    expect(privacyMigrationSql).toContain("public_api_cutover_deploy_sha");
    expect(privacyMigrationSql).toContain(
      "public_api_cutover_verification_run_id",
    );
    expect(privacyMigrationSql).toMatch(
      /UPDATE public\.watchlist\s+SET is_public = false\s+WHERE is_public = true/,
    );
    expect(privacyMigrationSql.match(/UPDATE public\.watchlist/g)).toHaveLength(1);
    expect(privacyMigrationSql).not.toMatch(/DELETE\s+FROM\s+public\.watchlist/i);
  });

  it("retains exact rollback content and old localized path variants", () => {
    expect(privacyMigrationSql).toContain(
      "CREATE TABLE public.watchlist_visibility_0089_rollback",
    );
    expect(privacyMigrationSql).toContain("watchlist_payload jsonb NOT NULL");
    expect(privacyMigrationSql).toContain("company_memberships jsonb NOT NULL");
    expect(privacyMigrationSql).toContain("owner_username text");
    expect(privacyMigrationSql).toContain("owner_display_username text");
    expect(privacyMigrationSql).toContain("path_variants jsonb NOT NULL");
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(privacyMigrationSql).toContain(`('${locale}'::text)`);
    }
    expect(privacyMigrationSql).toContain("'pagePath'");
    expect(privacyMigrationSql).toContain("'ogPath'");
    expect(privacyMigrationSql).toContain("'legacyOgPathPattern'");
    expect(privacyMigrationSql).toContain("'legacyOgPurgePattern'");
    expect(privacyMigrationSql).toContain("opengraph-image-:hash");
    expect(nextConfig).toContain(
      'source: "/:lang(en|de|fr|it)/:userSlug/:watchlistSlug/opengraph-image-:hash"',
    );
  });

  it("asserts rows, filters, alerts, provenance, owners, and memberships", () => {
    for (const invariant of [
      "watchlist_content_digest",
      "filters_digest",
      "alerts_digest",
      "provenance_digest",
      "owners_digest",
      "membership_digest",
      "jobseek_0089_watchlist_before",
      "jobseek_0089_membership_before",
      "orphaned_owner_count",
    ]) {
      expect(privacyMigrationSql).toContain(invariant);
    }
    expect(privacyMigrationSql).toContain("source_watchlist_id");
    expect(privacyMigrationSql).toContain("watchlist_company content");
  });

  it("retains the compatibility column and partial index", () => {
    expect(privacyMigrationSql).toContain("idx_wl_public");
    expect(privacyMigrationSql).not.toMatch(/DROP\s+(?:INDEX|COLUMN)/i);
    expect(getTableColumns(watchlist).isPublic).toBeDefined();
  });

  it("provides guarded inventory, verification, and rollback operations", () => {
    expect(privacyRunner).toContain(
      'type Command = "inventory" | "apply" | "verify" | "rollback"',
    );
    expect(privacyRunner).toContain("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY");
    expect(privacyRunner).toContain("MIGRATION_REQUIRE_UNPOOLED");
    expect(privacyRunner).toContain("requiredWatchlistApplyEvidence(process.env");
    expect(privacyRunner).toContain("ROLLBACK-PRIVATE-WATCHLISTS-0089");
    expect(privacyRunner).toContain('mode: 0o600');
    expect(privacyRunner).not.toContain("notifyIndexNow");
  });

  it("appends one monotonic journal entry", () => {
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
      when: 1_788_206_680_000,
      tag: "0089_make_existing_watchlists_private",
      breakpoints: true,
    });
  });
});
