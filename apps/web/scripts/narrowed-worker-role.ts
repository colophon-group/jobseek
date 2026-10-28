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
    const [role] = await tx`SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
      rolinherit,rolconnlimit,rolcanlogin,rolconfig
      FROM pg_roles WHERE rolname=${NARROWED_WORKER_ROLE}`;
    if (role && [role.rolsuper, role.rolcreatedb, role.rolcreaterole, role.rolreplication, role.rolbypassrls].some(Boolean)) throw new Error("Worker role has elevated privileges");
    const [membership] = await tx`SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
      WHERE r.rolname=${NARROWED_WORKER_ROLE}) AS present`;
    if (membership?.present) throw new Error("Worker role must not assume other roles");
    const [administrator] = await tx`SELECT rolsuper OR rolcreaterole AS can_manage_roles
      FROM pg_roles WHERE rolname=current_user`;
    if (administrator?.can_manage_roles) {
      if (!role) {
        await tx.unsafe(`CREATE ROLE ${NARROWED_WORKER_ROLE} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6 PASSWORD '${password}'`);
      }
      await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} NOINHERIT CONNECTION LIMIT 6`);
      // Reuse the original password on later deployments, keeping rollback credentials valid.
      await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} SET timezone='UTC'`);
      await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} SET statement_timeout='45s'`);
      await tx.unsafe(`ALTER ROLE ${NARROWED_WORKER_ROLE} SET idle_in_transaction_session_timeout='15s'`);
    } else {
      // The database host provisions cluster roles; the app owner only owns its schema.
      const settings = new Map<string,string>((role?.rolconfig ?? []).map((entry: string) => {
        const equals = entry.indexOf("=");
        return [entry.slice(0, equals).toLowerCase(), entry.slice(equals + 1)];
      }));
      if (!role || !role.rolcanlogin || role.rolinherit || role.rolconnlimit !== 6 ||
        settings.size !== 3 || settings.get("timezone") !== "UTC" ||
        settings.get("statement_timeout") !== "45s" ||
        settings.get("idle_in_transaction_session_timeout") !== "15s") {
        throw new Error("The database administrator must preprovision the restricted worker role");
      }
    }
    await tx.unsafe(`GRANT USAGE ON SCHEMA public TO ${NARROWED_WORKER_ROLE}`);
    for (const table of [...readTables, ...writeTables]) {
      const [exists] = await tx`SELECT to_regclass(${`public.${table}`}) IS NOT NULL AS present`;
      if (!exists?.present) {
        if (writeTables.includes(table)) throw new Error("Worker schema is incomplete");
        continue;
      }
      const permissions = writeTables.includes(table) ? "SELECT,INSERT,UPDATE" : "SELECT";
      await tx.unsafe(`GRANT ${permissions} ON public.${table} TO ${NARROWED_WORKER_ROLE}`);
      for (const command of writeTables.includes(table) ? ["SELECT", "INSERT", "UPDATE"] : ["SELECT"]) {
        const name = `jobseek_narrowed_worker_${command.toLowerCase()}`;
        const qual = command === "INSERT" ? "" : "true";
        const check = command === "SELECT" ? "" : "true";
        const [existing] = await tx`SELECT roles=ARRAY[${NARROWED_WORKER_ROLE}]::name[] AND cmd=${command}
          AND permissive='PERMISSIVE' AND coalesce(qual,'')=${qual} AND coalesce(with_check,'')=${check} AS compatible
          FROM pg_policies WHERE schemaname='public' AND tablename=${table} AND policyname=${name}`;
        if (existing && !existing.compatible) throw new Error("Worker row security policy differs from the reviewed contract");
        if (!existing) await tx.unsafe(`CREATE POLICY ${name} ON public.${table} AS PERMISSIVE FOR ${command} TO ${NARROWED_WORKER_ROLE}${qual ? " USING (true)" : ""}${check ? " WITH CHECK (true)" : ""}`);
      }
    }
    await tx.unsafe(`GRANT USAGE ON SEQUENCE public.ai_filter_event_sequence_seq TO ${NARROWED_WORKER_ROLE}`);
  });
}

/** Compare the real login with its owner's view at one consistent snapshot. */
export async function verifyNarrowedWorkerReadAccess(admin: Sql, worker: Sql) {
  await admin.begin(async owner => {
    await owner.unsafe("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY");
    const [snapshot] = await owner`SELECT pg_export_snapshot() AS id`;
    if (!snapshot || !/^[a-f0-9-]{1,128}$/i.test(snapshot.id)) throw new Error("Worker access snapshot unavailable");
    await worker.begin(async limited => {
      await limited.unsafe("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY");
      await limited.unsafe(`SET TRANSACTION SNAPSHOT '${snapshot.id}'`);
      for (const table of [...readTables, ...writeTables]) {
        const [exists] = await owner`SELECT to_regclass(${`public.${table}`}) IS NOT NULL AS present`;
        if (!exists?.present) continue;
        const [expected] = await owner.unsafe(`SELECT count(*)::text AS n FROM public.${table}`);
        const [actual] = await limited.unsafe(`SELECT count(*)::text AS n FROM public.${table}`);
        if (expected?.n !== actual?.n) throw new Error("Worker row visibility differs from the owner");
      }
    });
  });
}
