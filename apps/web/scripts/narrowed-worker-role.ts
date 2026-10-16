import type { Sql } from "postgres";

export const NARROWED_WORKER_ROLE = "jobseek_narrowed_worker";
const readTables = ["watchlist", "watchlist_company", "user_preferences", "subscription", "currency_rate",
  "paddle_subscription", "paddle_account", "stripe_subscription", "stripe_account"];
const writeTables = ["ai_filter_configuration", "ai_filter_query_version", "ai_filter_segment", "ai_filter_work",
  "ai_filter_worker", "ai_filter_global_cache", "ai_filter_decision", "ai_filter_budget_account",
  "ai_filter_usage_ledger", "ai_filter_event"];

/** Protected deployment only. The worker never receives the owner connection. */
export async function provisionNarrowedWorkerRole(admin: Sql, password: string) {
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(password)) throw new Error("Invalid dedicated worker password");
  await admin.begin(async tx => {
    await tx`SELECT pg_advisory_xact_lock(830176421)`;
    const [role] = await tx`SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
      FROM pg_roles WHERE rolname=${NARROWED_WORKER_ROLE}`;
    if (role && Object.values(role).some(Boolean)) throw new Error("Worker role has elevated privileges");
    const [membership] = await tx`SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
      WHERE r.rolname=${NARROWED_WORKER_ROLE}) AS present`;
    if (membership?.present) throw new Error("Worker role must not assume other roles");
    if (!role) {
      await tx.unsafe(`CREATE ROLE ${NARROWED_WORKER_ROLE} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6 PASSWORD '${password}'`);
    }
    await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} NOINHERIT CONNECTION LIMIT 6`);
    // Reuse the original password on later deployments, keeping rollback credentials valid.
    await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} SET timezone='UTC'`);
    await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} SET statement_timeout='45s'`);
    await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} SET idle_in_transaction_session_timeout='15s'`);
    await tx.unsafe(`GRANT USAGE ON SCHEMA public TO ${NARROWED_WORKER_ROLE}`);
    for (const table of [...readTables, ...writeTables]) {
      const [exists] = await tx`SELECT to_regclass(${`public.${table}`}) IS NOT NULL AS present`;
      if (!exists?.present) {
        if (writeTables.includes(table)) throw new Error("Worker schema is incomplete");
        continue;
      }
      const permissions = writeTables.includes(table) ? "SELECT,INSERT,UPDATE" : "SELECT";
      await tx.unsafe(`GRANT ${permissions} ON public.${table} TO ${NARROWED_WORKER_ROLE}`);
    }
    await tx.unsafe(`GRANT USAGE ON SEQUENCE public.ai_filter_event_sequence_seq TO ${NARROWED_WORKER_ROLE}`);
  });
}
