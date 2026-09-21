import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const migration = readFileSync(
  resolve(webRoot, "drizzle/0092_jev_ai_filter_foundation.sql"),
  "utf8",
);
const schema = readFileSync(resolve(webRoot, "src/db/schema.ts"), "utf8");

describe("0092 Jev AI filter foundation migration", () => {
  it("creates durable configuration, segment, decision, cache, budget, and event state", () => {
    for (const table of [
      "ai_filter_configuration",
      "ai_filter_query_version",
      "ai_filter_segment",
      "ai_filter_global_cache",
      "ai_filter_decision",
      "ai_filter_budget_account",
      "ai_filter_usage_ledger",
      "ai_filter_event",
      "ai_filter_feedback",
    ]) {
      expect(migration).toContain(`CREATE TABLE public.${table}`);
      expect(schema).toContain(`\"${table}\"`);
    }
  });

  it("prevents duplicate active work, semantic decisions, cache claims, and ledger reconciliation", () => {
    expect(migration).toContain("ai_filter_segment_active_watchlist_uidx");
    expect(migration).toContain("ai_filter_decision_semantic_uidx");
    expect(migration).toContain("cache_key text PRIMARY KEY NOT NULL");
    expect(migration).toContain("ai_filter_usage_idempotency_key_unique");
    expect(migration).toContain("ai_filter_usage_reconciled_check");
  });

  it("stores only HMAC/content digests in the global cache and enforces hard retention", () => {
    const cacheDefinition = migration.slice(
      migration.indexOf("CREATE TABLE public.ai_filter_global_cache"),
      migration.indexOf("CREATE TABLE public.ai_filter_decision"),
    );
    expect(cacheDefinition).not.toMatch(/query_text|normalized_query|description/i);
    expect(cacheDefinition).toContain("cache_key ~ '^[0-9a-f]{64}$'");
    expect(cacheDefinition).toContain("content_identity ~ '^[0-9a-f]{64}$'");
    expect(migration).toContain(
      "expires_at <= posting_first_seen_at + interval '30 days'",
    );
    expect(migration).not.toMatch(/expires_at\s*=\s*GREATEST/i);
  });

  it("uses fixed-point nanodollars and owner-cascading foreign keys", () => {
    expect(migration).toContain("actual_nanodollars bigint");
    expect(migration).toContain("reserved_nanodollars bigint");
    expect(migration).not.toMatch(/\b(real|double precision|money)\s+(DEFAULT|NOT NULL|NULL|,)/i);
    expect(migration).toContain(
      'REFERENCES public."user"(id) ON DELETE CASCADE',
    );
  });

  it("appends a monotonic migration journal entry", () => {
    const journal = JSON.parse(
      readFileSync(resolve(webRoot, "drizzle/meta/_journal.json"), "utf8"),
    ) as { entries: Array<{ idx: number; when: number; tag: string }> };
    expect(journal.entries.at(-1)).toMatchObject({
      idx: 80,
      when: 1_789_988_400_000,
      tag: "0092_jev_ai_filter_foundation",
    });
  });
});
