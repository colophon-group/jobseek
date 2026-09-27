import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import postgres from "postgres";

async function main() {
  const index = process.argv.indexOf("--database-url");
  assert(index !== -1 && process.argv[index + 1], "Pass an explicit disposable --database-url");
  const url = new URL(process.argv[index + 1]);
  assert(/^postgres(?:ql)?:$/.test(url.protocol) && /fixture/i.test(url.pathname), "Database must be a PostgreSQL fixture");
  const sql = postgres(url.href, { max: 1, onnotice: () => {} });
  try {
    await sql.begin(async tx => {
      // All fixture changes, including cluster roles, are rolled back.
      await tx.unsafe("DROP SCHEMA public CASCADE; CREATE SCHEMA public");
      await tx.unsafe(`DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
      END $$`);
      await tx.unsafe("GRANT USAGE ON SCHEMA public TO anon, authenticated");
      await tx.unsafe("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated");
      await tx.unsafe('CREATE TABLE public."user" (id text PRIMARY KEY)');
      const migration = await readFile(resolve("drizzle/0096_paddle_billing.sql"), "utf8");
      for (const statement of migration.split("--> statement-breakpoint").filter(s => s.trim())) {
        await tx.unsafe(statement);
      }
      await tx`INSERT INTO public."user" VALUES ('paddle-fixture')`;
      const [account] = await tx`INSERT INTO public.paddle_account (user_id, environment) VALUES ('paddle-fixture', 'sandbox') RETURNING id`;
      await tx`INSERT INTO public.paddle_subscription (id,account_id,status,expected_price_id,entitled,event_occurred_at) VALUES ('paddle-fixture-sub',${account.id},'active','paddle-fixture-price',true,now())`;
      const tables = ["paddle_account", "paddle_subscription"];
      for (const table of tables) {
        const [policy] = await tx`SELECT relrowsecurity FROM pg_class WHERE oid=${'public.' + table}::regclass`;
        assert.equal(policy.relrowsecurity, true, `${table} must enable RLS`);
        for (const role of ["anon", "authenticated"]) {
          for (const privilege of ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]) {
            const [permission] = await tx`SELECT has_table_privilege(${role},${'public.' + table},${privilege}) AS allowed`;
            assert.equal(permission.allowed, false, `${role} must not have ${privilege} on ${table}`);
          }
          for (const command of [`SELECT * FROM public.${table}`, `DELETE FROM public.${table}`, `TRUNCATE public.${table}`]) {
            await assert.rejects(tx.savepoint(async sp => {
              await sp.unsafe(`SET LOCAL ROLE ${role}`);
              await sp.unsafe(command);
            }), (error: unknown) => (error as { code?: string }).code === "42501");
          }
        }
      }
      const [owner] = await tx`SELECT entitled FROM public.paddle_subscription WHERE id='paddle-fixture-sub'`;
      assert.equal(owner.entitled, true, "Server database owner retains billing access");
      throw new Error("PADDLE_FIXTURE_ROLLBACK");
    }).catch(error => { if (error.message !== "PADDLE_FIXTURE_ROLLBACK") throw error; });
    console.log("Paddle SQL migration passed: server access retained; browser reads, writes, and truncation denied.");
  } finally { await sql.end(); }
}
void main().catch(() => { console.error("Paddle billing migration fixture failed"); process.exitCode = 1; });
