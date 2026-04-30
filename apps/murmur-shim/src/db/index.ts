/**
 * Postgres / Drizzle client for `apps/murmur-shim`.
 *
 * The `claim-kv.ts` and accept-handler `idempotency.ts` modules write to
 * three tables in the shared jobseek database (`murmur_claim_kv`,
 * `murmur_accept_log`, and the `company` row referenced by the FK on
 * `murmur_accept_log.company_id`). All those tables are owned by the
 * Drizzle schema in `apps/web/src/db/schema.ts`.
 *
 * Rather than maintain two parallel schema definitions (which would
 * bit-rot at the first migration), the shim re-exports the same schema
 * via a relative path (`./schema`). The shim's db client is identical
 * to web's: same Proxy-lazy connection pattern, same `DATABASE_URL`
 * reading, same Postgres pool tuning. The only thing duplicated is the
 * ~14 lines of init wiring; the schema itself is single-source.
 *
 * Why duplicate the wiring at all (vs. importing `@jobseek/web`'s db
 * directly): `apps/web` is a Next.js app, not a publishable package. Its
 * `src/db/index.ts` lives behind the `@/` path alias, so cross-app
 * import would require either making web a workspace package or wiring
 * a relative import that tunnels through web's tsconfig. Either path
 * is more friction than the 14 lines below.
 *
 * DSN selection: `MURMUR_DB_DSN` first, `DATABASE_URL` as fallback.
 * Per the colophon-group/jobseek protocol, the local Hetzner Postgres
 * machine is the primary read-write DB; Supabase is a read-only mirror
 * (CDC). All shim writes (`murmur_claim_kv`, `murmur_accept_log`)
 * MUST go through the primary, so prod compose forwards
 * `LOCAL_DATABASE_URL → MURMUR_DB_DSN`. `DATABASE_URL` stays as a
 * fallback so local dev / tests still work when only the latter is
 * set, and so the apps/web schema re-export is backward-compatible
 * with the original `DATABASE_URL` convention.
 */
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import * as schema from "./schema";

const globalForDb = globalThis as unknown as {
  _murmurShimDb?: PostgresJsDatabase<typeof schema>;
};

/**
 * Resolve the Postgres DSN for the shim. Reads `MURMUR_DB_DSN` first
 * (the prod-side var the compose forwards from `LOCAL_DATABASE_URL` —
 * the Hetzner Postgres primary that holds `murmur_claim_kv` and
 * `murmur_accept_log`), then falls back to `DATABASE_URL` for local
 * dev / tests where only the latter is set. Returns the variable NAME
 * for diagnostics so an unset config surfaces a clear error without
 * leaking the value.
 */
function resolveDsn(): { value: string; name: string } {
  const dsn = process.env.MURMUR_DB_DSN ?? process.env.DATABASE_URL;
  if (!dsn) {
    throw new Error(
      "Neither MURMUR_DB_DSN nor DATABASE_URL is set; the shim needs " +
        "a Postgres DSN to write `murmur_claim_kv` / `murmur_accept_log`.",
    );
  }
  return {
    value: dsn,
    name: process.env.MURMUR_DB_DSN ? "MURMUR_DB_DSN" : "DATABASE_URL",
  };
}

export const db = new Proxy({} as PostgresJsDatabase<typeof schema>, {
  get(_target, prop, receiver) {
    if (!globalForDb._murmurShimDb) {
      const dsn = resolveDsn();
      globalForDb._murmurShimDb = drizzle(
        postgres(dsn.value, {
          max: 10,
          idle_timeout: 20,
          max_lifetime: 300,
          prepare: false,
        }),
        { schema },
      );
    }
    return Reflect.get(globalForDb._murmurShimDb, prop, receiver);
  },
});
