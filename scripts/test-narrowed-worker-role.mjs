import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { provisionNarrowedWorkerRole } from "../apps/web/scripts/narrowed-worker-role.ts";

test("a schema owner verifies a preprovisioned worker without role administration", async () => {
  const uri = new URL(process.env.WEB_DB_TEST_URL);
  assert.ok(["127.0.0.1", "localhost"].includes(uri.hostname));
  assert.equal(uri.pathname, "/postgres");
  const { default: postgres } = await import(pathToFileURL(process.env.WEB_DB_DRIVER_TEST_MODULE).href);
  const options = { max: 1, prepare: false, onnotice: () => {} };
  const root = postgres(uri.href, options);
  const suffix = randomBytes(6).toString("hex");
  const database = `narrowed_role_${suffix}_fixture`;
  const owner = `narrowed_owner_${suffix}`;
  const password = "fixture".repeat(8);
  const workerRole = "jobseek_narrowed_worker";
  const read = ["watchlist", "watchlist_company", "user_preferences", "subscription", "currency_rate",
    "paddle_subscription", "paddle_account", "stripe_subscription", "stripe_account"];
  const write = ["ai_filter_configuration", "ai_filter_query_version", "ai_filter_segment", "ai_filter_work",
    "ai_filter_worker", "ai_filter_global_cache", "ai_filter_decision", "ai_filter_budget_account",
    "ai_filter_usage_ledger", "ai_filter_event"];
  let app, worker;
  let created = false;
  try {
    const [existing] = await root`SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=${workerRole}) AS present`;
    if (!existing.present) await root.unsafe(`CREATE ROLE ${workerRole}`);
    await root.unsafe(`ALTER ROLE ${workerRole} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6 PASSWORD '${password}'`);
    await root.unsafe(`ALTER ROLE ${workerRole} RESET ALL`);
    await root.unsafe(`ALTER ROLE ${workerRole} SET timezone='UTC'`);
    await root.unsafe(`ALTER ROLE ${workerRole} SET statement_timeout='45s'`);
    await root.unsafe(`ALTER ROLE ${workerRole} SET idle_in_transaction_session_timeout='15s'`);
    await root.unsafe(`CREATE ROLE ${owner} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '${password}'`);
    await root.unsafe(`CREATE DATABASE ${database} OWNER ${owner}`);
    created = true;
    const appUri = new URL(uri); appUri.pathname = `/${database}`; appUri.username = owner; appUri.password = password;
    app = postgres(appUri.href, options);
    const workerUri = new URL(appUri); workerUri.username = workerRole;
    worker = postgres(workerUri.href, options);
    for (const table of [...read, ...write]) {
      await app.unsafe(`CREATE TABLE public.${table}(id int); ALTER TABLE public.${table} ENABLE ROW LEVEL SECURITY`);
    }
    await app.unsafe('CREATE TABLE public."user"(id int); CREATE SEQUENCE public.ai_filter_event_sequence_seq');
    await app`INSERT INTO watchlist VALUES (1)`;
    await assert.rejects(app.unsafe(`ALTER ROLE ${workerRole} NOINHERIT`), { code: "42501" });
    await provisionNarrowedWorkerRole(app, password);
    await provisionNarrowedWorkerRole(app, password);
    assert.equal((await worker`SELECT count(*)::int AS n FROM watchlist`)[0].n, 1);
    await worker`INSERT INTO ai_filter_work VALUES (1)`;
    await assert.rejects(worker`UPDATE watchlist SET id=2`, { code: "42501" });
    await assert.rejects(worker`SELECT * FROM "user"`, { code: "42501" });
    const [identity] = await app`SELECT rolsuper,rolcreaterole FROM pg_roles WHERE rolname=current_user`;
    assert.deepEqual({ ...identity }, { rolsuper: false, rolcreaterole: false });
    await root.unsafe(`ALTER ROLE ${workerRole} SET statement_timeout='60s'`);
    await assert.rejects(provisionNarrowedWorkerRole(app, password), /administrator must preprovision/);
    await root.unsafe(`ALTER ROLE ${workerRole} SET statement_timeout='45s'`);
    await root.unsafe(`ALTER ROLE ${workerRole} CREATEDB`);
    await assert.rejects(provisionNarrowedWorkerRole(app, password), /elevated privileges/);
  } finally {
    await Promise.all([app?.end({ timeout: 5 }), worker?.end({ timeout: 5 })]);
    if (created) await root.unsafe(`DROP DATABASE ${database} WITH (FORCE)`);
    await root.unsafe(`DROP ROLE IF EXISTS ${owner}`);
    await root.unsafe(`ALTER ROLE ${workerRole} NOCREATEDB`);
    await root.end({ timeout: 5 });
  }
});
