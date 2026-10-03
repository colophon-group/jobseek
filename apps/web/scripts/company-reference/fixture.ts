import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import postgres from "postgres";
import { generateDrizzleJson, generateMigration } from "drizzle-kit/api";
import * as schema from "../../src/db/schema";

/** Only disposable loopback databases may be reset by these harnesses. */
export function fixtureDatabaseUrl(): string {
  const value = process.env.COMPANY_REFERENCE_TEST_DATABASE_URL;
  if (!value) throw new Error("COMPANY_REFERENCE_TEST_DATABASE_URL is required (no skipped contracts)");
  const url = new URL(value);
  if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) || !/^\/jobseek_company_reference_[a-z0-9_]+_fixture$/.test(url.pathname)) {
    throw new Error("Company reference harness requires a named disposable loopback fixture database");
  }
  return value;
}

export function fixtureClient() {
  return postgres(fixtureDatabaseUrl(), { max: 10, prepare: false, onnotice: () => {} });
}

/** Generate untouched pre-expand tables from real Drizzle schema, then execute the exact migration. */
export async function resetFixture(sql: ReturnType<typeof fixtureClient>, writeMode: "bridge" | "reference" = "bridge") {
  await sql.unsafe("DROP SCHEMA public CASCADE; CREATE SCHEMA public");
  const legacy = { ...schema } as Record<string, unknown>;
  delete legacy.companyReference;
  const empty = generateDrizzleJson({});
  const desired = generateDrizzleJson(legacy);
  for (const statement of await generateMigration(empty, desired)) {
    // The final Drizzle schema points selections at references. Reconstruct
    // exactly the preceding cascade FKs before running the real migrations.
    const preExpand = statement.replaceAll('REFERENCES "public"."company_reference"("id") ON DELETE restrict',
      'REFERENCES "public"."company"("id") ON DELETE cascade');
    await sql.unsafe(preExpand);
  }
  const migration = await readFile(new URL("../../drizzle/0100_company_references.sql", import.meta.url), "utf8");
  // Migration holds a transaction-scoped lock. Preserve the production runner's transaction boundary.
  await sql.begin(async (tx) => {
    for (const statement of migration.split("--> statement-breakpoint").filter((part) => part.trim())) {
      await tx.unsafe(statement);
    }
  });
  if (writeMode === "reference") {
    const contract = await readFile(new URL("../../drizzle/0101_company_reference_selection_contract.sql", import.meta.url), "utf8");
    await sql.begin(async tx => {
      for (const statement of contract.split("--> statement-breakpoint").filter(part => part.trim())) await tx.unsafe(statement);
    });
  }
}

export async function seedUser(sql: ReturnType<typeof fixtureClient>, suffix = randomUUID()) {
  const id = `company-reference-${suffix}`;
  const email = `${suffix}@company-reference.invalid`;
  await sql`INSERT INTO "user" (id, name, email, username, display_username, email_verified)
    VALUES (${id}, 'Company reference fixture', ${email}, ${`fixture-${suffix.slice(0, 8)}`}, 'Fixture', true)`;
  return { id, email };
}

export function companyDocument(id = randomUUID(), name = "Fixture Company") {
  return { id, name, slug: `fixture-${id}`, icon: null, active_posting_count: 1, year_posting_count: 1 };
}
