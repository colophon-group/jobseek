// @vitest-environment node
import { readFileSync } from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { getTableColumns } from "drizzle-orm";
import { getTableConfig, PgTimestamp } from "drizzle-orm/pg-core";
import postgres, { type Sql } from "postgres";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { companyReference } from "../schema";
import { auditCompanyReferences, normalizeReferenceCheck, referenceMigrationIdentity, referencePrerequisiteIdentity, validateCompanyReferenceRuntimeRole } from "../../../scripts/company-reference-contract";
const migration = readFileSync("drizzle/0100_company_references.sql", "utf8");
const id = "11111111-1111-4111-8111-111111111111";
const second = "22222222-2222-4222-8222-222222222222";
const third = "33333333-3333-4333-8333-333333333333";
const url = process.env.COMPANY_REFERENCE_MIGRATION_TEST_DATABASE_URL;
let sql: Sql;
async function apply() {
  await sql.begin(async tx => {
    for (const statement of migration.split("--> statement-breakpoint").filter(s => s.trim())) await tx.unsafe(statement);
    await tx`INSERT INTO drizzle.__drizzle_migrations (hash, created_at) VALUES (${referenceMigrationIdentity.hash}, ${referenceMigrationIdentity.createdAt})`;
  });
}
async function fixture(malformed = false) {
  await sql.unsafe(`DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; SET search_path = public;
    DROP SCHEMA IF EXISTS drizzle CASCADE; CREATE SCHEMA drizzle;
    CREATE TABLE drizzle.__drizzle_migrations (id serial PRIMARY KEY, hash text NOT NULL, created_at bigint NOT NULL);
    CREATE TABLE company (id uuid PRIMARY KEY, name text NOT NULL, slug text UNIQUE NOT NULL, icon text, created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now());
    CREATE TABLE watchlist_company (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), watchlist_id uuid NOT NULL, company_id uuid NOT NULL REFERENCES company(id) ON DELETE CASCADE);
    CREATE TABLE followed_company (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text NOT NULL, company_id uuid NOT NULL REFERENCES company(id) ON DELETE CASCADE);
    CREATE TABLE saved_job (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), company_id uuid NOT NULL, company_name text NOT NULL, company_slug text NOT NULL);
    DO $$ BEGIN CREATE ROLE anon; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    DO $$ BEGIN CREATE ROLE authenticated; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    DO $$ BEGIN CREATE ROLE jobseek_migration_auditor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    ALTER ROLE jobseek_migration_auditor PASSWORD 'company-reference-auditor-fixture';
    ALTER ROLE jobseek_migration_auditor SET default_transaction_read_only = on;`);
  await sql`INSERT INTO drizzle.__drizzle_migrations (hash, created_at) VALUES (${referencePrerequisiteIdentity.hash}, ${referencePrerequisiteIdentity.createdAt})`;
  await sql`INSERT INTO company (id,name,slug,icon,created_at,updated_at) VALUES (${id}, ${malformed ? " " : "First"}, 'first', 'https://example.test/icon.png', '2026-01-01 12:00:00', '2026-02-01 12:00:00')`;
  await sql`INSERT INTO watchlist_company (watchlist_id, company_id) VALUES (${third}, ${id})`;
  await sql`INSERT INTO followed_company (user_id, company_id) VALUES ('fixture-user', ${id})`;
  await sql`INSERT INTO saved_job (company_id,company_name,company_slug) VALUES (${id}, 'Historical snapshot', 'historical-slug')`;
}
beforeAll(() => {
  if (!url) return;
  const parsed = new URL(url);
  if (parsed.hostname !== "127.0.0.1" || !parsed.pathname.endsWith("_fixture")) throw new Error("Disposable localhost _fixture database required");
  sql = postgres(url, { max: 5, prepare: false, onnotice: () => {} });
});
afterAll(async () => { if (sql) await sql.end(); });
describe("company reference schema", () => {
  it.each(["anon", "authenticated", "jobseek_migration_auditor", "postgres;DROP", " Postgres ", "role-unsafe", "UPPER", "x".repeat(64)])("rejects an unsafe audited runtime role %s", role => {
    expect(() => validateCompanyReferenceRuntimeRole(role)).toThrow("Invalid audited");
  });
  it("uses canonical UUID without defaults, mutable nonunique slugs, timezone timestamps", () => {
    const columns = getTableColumns(companyReference);
    expect(columns.id.hasDefault).toBe(false); expect(columns.id.primary).toBe(true);
    expect(getTableConfig(companyReference).uniqueConstraints).toHaveLength(0);
    for (const name of ["verifiedAt", "createdAt", "updatedAt"] as const) {
      expect(columns[name]).toBeInstanceOf(PgTimestamp);
      expect((columns[name] as PgTimestamp<never>).withTimezone).toBe(true);
    }
  });
  it("retains operands when comparing CHECK contracts", () => {
    expect(normalizeReferenceCheck("CHECK (length(name) <= 300 OR true)"))
      .not.toEqual(normalizeReferenceCheck("CHECK (length(name) <= 300)"));
    expect(normalizeReferenceCheck("CHECK (slug ~ '^[A-Z]+$')"))
      .not.toEqual(normalizeReferenceCheck("CHECK (slug ~ '^[a-z]+$')"));
    expect(normalizeReferenceCheck("CHECK (slug ~ '^[a-z ]+$')"))
      .not.toEqual(normalizeReferenceCheck("CHECK (slug ~ '^[a-z]+$')"));
  });
});
describe.skipIf(!url)("company reference expansion with PostgreSQL", () => {
  it("preserves memberships/snapshots and UTC instants with exact ledger/catalog", async () => {
    await fixture();
    const snapshot = () => sql`SELECT to_jsonb(w) AS data FROM watchlist_company w UNION ALL SELECT to_jsonb(f) FROM followed_company f UNION ALL SELECT to_jsonb(s) FROM saved_job s`;
    const before = await snapshot();
    expect(await auditCompanyReferences(sql, "preflight")).toMatchObject({ status: "passed" });
    await apply();
    expect(await auditCompanyReferences(sql, "postflight")).toMatchObject({ status: "passed", coverage: { missingSelections: 0, missingLegacy: 0 } });
    expect(await auditCompanyReferences(sql, "drift")).toMatchObject({ status: "passed" });
    expect(await snapshot()).toEqual(before);
    const [seed] = await sql`SELECT * FROM company_reference WHERE id=${id}`;
    expect(seed).toMatchObject({ name: "First", slug: "first", source: "legacy_seed", verified_at: null });
    expect(seed!.created_at.toISOString()).toBe("2026-01-01T12:00:00.000Z");
  });
  it("rejects malformed seed atomically using bounded count-only diagnostics", async () => {
    await fixture(true); await expect(apply()).rejects.toThrow("1 malformed legacy rows");
    expect((await sql`SELECT to_regclass('public.company_reference') AS relation`)[0]!.relation).toBeNull();
    expect((await sql`SELECT count(*)::integer AS count FROM drizzle.__drizzle_migrations`)[0]!.count).toBe(1);
    expect((await sql`SELECT count(*)::integer AS count FROM watchlist_company`)[0]!.count).toBe(1);
  });
  it("refuses display data rejected by the actual read normalizer before expansion", async () => {
    await fixture();
    await sql`UPDATE company SET name=${"😀".repeat(151)} WHERE id=${id}`;
    await expect(auditCompanyReferences(sql, "preflight")).rejects.toThrow("nonrenderable companies");
    expect((await sql`SELECT to_regclass('public.company_reference') AS relation`)[0]!.relation).toBeNull();
  });
  it("bridges old-version insert/update and protects verified provenance", async () => {
    await fixture(); await apply();
    await sql`INSERT INTO company (id,name,slug) VALUES (${second},'Late','late')`;
    expect((await sql`SELECT source FROM company_reference WHERE id=${second}`)[0]!.source).toBe("legacy_seed");
    await sql`UPDATE company SET name='Late updated' WHERE id=${second}`;
    expect((await sql`SELECT name FROM company_reference WHERE id=${second}`)[0]!.name).toBe("Late updated");
    await sql`UPDATE company_reference SET name='Verified', slug='verified', source='typesense', verified_at=now() WHERE id=${second}`;
    await sql`UPDATE company SET name='Older release' WHERE id=${second}`;
    expect((await sql`SELECT name,source FROM company_reference WHERE id=${second}`)[0]).toMatchObject({ name: "Verified", source: "typesense" });
    expect(await auditCompanyReferences(sql, "drift")).toMatchObject({ status: "passed" });
  });
  it("enforces bounds, identity and verified provenance while allowing reused slugs", async () => {
    await fixture(); await apply();
    const invalid = [
      { name: " ", slug: "valid", icon: null, source: "legacy_seed" },
      { name: "x".repeat(301), slug: "valid", icon: null, source: "legacy_seed" },
      { name: "Valid", slug: " ", icon: null, source: "legacy_seed" },
      { name: "Bad\u0001Name", slug: "valid", icon: null, source: "legacy_seed" },
      { name: "Valid", slug: "Bad_Slug", icon: null, source: "legacy_seed" },
      { name: "Valid", slug: "valid", icon: "bad\u0001icon", source: "legacy_seed" },
      { name: "Valid", slug: "x".repeat(101), icon: null, source: "legacy_seed" },
      { name: "Valid", slug: "valid", icon: "x".repeat(2049), source: "legacy_seed" },
      { name: "Valid", slug: "valid", icon: null, source: "browser" },
      { name: "Valid", slug: "valid", icon: null, source: "typesense" },
    ];
    for (const row of invalid) await expect(sql`INSERT INTO company_reference (id,name,slug,icon,source)
      VALUES (${second},${row.name},${row.slug},${row.icon},${row.source})`).rejects.toMatchObject({ code: "23514" });
    await sql`INSERT INTO company_reference (id,name,slug,source,verified_at) VALUES (${second},'Other','first','typesense',now())`;
    expect((await sql`SELECT count(*)::integer AS count FROM company_reference WHERE slug='first'`)[0]!.count).toBe(2);
    await expect(sql`INSERT INTO company_reference (name,slug,source) VALUES ('No UUID','no-uuid','legacy_seed')`).rejects.toMatchObject({ code: "23502" });
  });
  it("rolls back reference, legacy row and selection together", async () => {
    await fixture(); await apply();
    await expect(sql.begin(async tx => {
      await tx`INSERT INTO company_reference (id,name,slug,source,verified_at) VALUES (${second},'New','new','typesense',now())`;
      await tx`INSERT INTO company (id,name,slug) VALUES (${second},'New','new')`;
      await tx`INSERT INTO watchlist_company (watchlist_id,company_id) VALUES (${third},${second})`;
      throw new Error("fixture rollback");
    })).rejects.toThrow("fixture rollback");
    expect(await sql`SELECT id FROM company_reference WHERE id=${second}`).toHaveLength(0);
    expect(await sql`SELECT id FROM company WHERE id=${second}`).toHaveLength(0);
    expect(await sql`SELECT company_id FROM watchlist_company WHERE company_id=${second}`).toHaveLength(0);
  });
  it("verifies an already-expanded backup restored into the disposable fixture", async () => {
    await fixture(); await apply();
    const parsed = new URL(url!);
    const { stdout: dump } = await promisify(execFile)("pg_dump", ["--schema=public", "--schema=drizzle", "--no-owner", "--inserts"], {
      env: { ...process.env, PGHOST: parsed.hostname, PGPORT: parsed.port || "5432", PGDATABASE: parsed.pathname.slice(1),
        PGUSER: decodeURIComponent(parsed.username), PGPASSWORD: decodeURIComponent(parsed.password) }, maxBuffer: 10 * 1024 * 1024,
    });
    await sql.unsafe("DROP SCHEMA public CASCADE; DROP SCHEMA drizzle CASCADE");
    // Recent pg_dump emits psql-only restrict/unrestrict directives.
    await sql.unsafe(dump.split("\n").filter(line => !line.startsWith("\\restrict ") && !line.startsWith("\\unrestrict ")).join("\n") + "\nSET search_path = public;");
    expect(await auditCompanyReferences(sql, "postflight")).toMatchObject({ status: "passed" });
    expect((await sql`SELECT count(*)::integer AS count FROM watchlist_company`)[0]!.count).toBe(1);
    expect((await sql`SELECT company_name FROM saved_job`)[0]!.company_name).toBe("Historical snapshot");
  });
  it("rejects duplicate identities and repeated expansion without changing recorded state", async () => {
    await fixture(); await apply();
    await expect(sql`INSERT INTO company_reference (id,name,slug,source) VALUES (${id}, 'Conflicting','other','legacy_seed')`).rejects.toMatchObject({ code: "23505" });
    await expect(apply()).rejects.toMatchObject({ code: "42P07" });
    expect(await auditCompanyReferences(sql, "postflight")).toMatchObject({ status: "passed" });
    expect((await sql`SELECT name FROM company_reference WHERE id=${id}`)[0]!.name).toBe("First");
  });
  it("requires every runtime privilege rather than accepting a SELECT-only role", async () => {
    await fixture(); await apply();
    await sql.unsafe(`DO $$ BEGIN CREATE ROLE company_reference_runtime_fixture BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      GRANT USAGE ON SCHEMA public, drizzle TO company_reference_runtime_fixture;
      GRANT SELECT ON ALL TABLES IN SCHEMA public, drizzle TO company_reference_runtime_fixture;
      REVOKE INSERT, UPDATE ON company_reference FROM company_reference_runtime_fixture;`);
    const restricted = postgres(url!, { max: 1, prepare: false, onnotice: () => {} });
    try {
      await restricted.unsafe("SET ROLE company_reference_runtime_fixture");
      await expect(auditCompanyReferences(restricted, "postflight")).rejects.toThrow("runtime role cannot read/write");
      await sql.unsafe("GRANT INSERT, UPDATE ON company_reference TO company_reference_runtime_fixture");
      await expect(auditCompanyReferences(restricted, "postflight")).rejects.toThrow("runtime role cannot read/write");
      await sql.unsafe("GRANT INSERT ON company TO company_reference_runtime_fixture");
      expect(await auditCompanyReferences(restricted, "postflight")).toMatchObject({ status: "passed" });
    } finally { await restricted.end(); }
  });
  it("permits the actual read-only auditor to verify runtime ACLs without write privileges", async () => {
    await fixture(); await apply();
    await sql.unsafe(`DO $$ BEGIN CREATE ROLE company_reference_runtime_fixture BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      GRANT USAGE ON SCHEMA public, drizzle TO jobseek_migration_auditor;
      GRANT SELECT ON company, watchlist_company, followed_company, drizzle.__drizzle_migrations TO jobseek_migration_auditor;
      GRANT SELECT,INSERT,UPDATE ON company_reference TO company_reference_runtime_fixture;
      GRANT INSERT ON company TO company_reference_runtime_fixture;`);
    const auditorUrl = new URL(url!); auditorUrl.username="jobseek_migration_auditor"; auditorUrl.password="company-reference-auditor-fixture";
    const auditor = postgres(auditorUrl.href, { max: 1, prepare: false, onnotice: () => {} });
    try {
      expect((await auditor`SHOW default_transaction_read_only`)[0]!.default_transaction_read_only).toBe("on");
      expect(await auditCompanyReferences(auditor, "drift", "company_reference_runtime_fixture")).toMatchObject({ status: "passed" });
      await expect(auditor.begin(async tx => {
        await tx`SET TRANSACTION READ WRITE`;
        await tx`INSERT INTO company_reference (id,name,slug,source) VALUES (${second},'Forbidden','forbidden','legacy_seed')`;
      })).rejects.toMatchObject({ code: "42501" });
      expect((await auditor`SELECT count(*)::integer AS count FROM company_reference`)[0]!.count).toBe(1);
      await sql.unsafe("ALTER POLICY company_reference_migration_auditor_select ON company_reference USING (false)");
      await expect(auditCompanyReferences(sql, "drift")).rejects.toThrow("exact SELECT policy");
    } finally { await auditor.end(); }
  });
  it("detects disabled triggers, weakened checks, browser grants and wrong ledger", async () => {
    await fixture(); await apply();
    await sql.unsafe("ALTER TABLE company DISABLE TRIGGER company_reference_legacy_bridge");
    await expect(auditCompanyReferences(sql, "drift")).rejects.toThrow("trigger drift");
    await sql.unsafe("ALTER TABLE company ENABLE TRIGGER company_reference_legacy_bridge");
    await sql.unsafe("ALTER TABLE company_reference DROP CONSTRAINT company_reference_name_check; ALTER TABLE company_reference ADD CONSTRAINT company_reference_name_check CHECK (length(name)<=300)");
    await expect(auditCompanyReferences(sql, "drift")).rejects.toThrow("CHECK drift");
    await fixture(); await apply();
    await sql.unsafe("GRANT SELECT ON company_reference TO authenticated");
    await expect(auditCompanyReferences(sql, "drift")).rejects.toThrow("Browser role");
    await sql.unsafe("REVOKE SELECT ON company_reference FROM authenticated");
    await sql`UPDATE drizzle.__drizzle_migrations SET hash='wrong' WHERE created_at=${referenceMigrationIdentity.createdAt}`;
    await expect(auditCompanyReferences(sql, "drift")).rejects.toThrow("0100 identity");
  });
});
