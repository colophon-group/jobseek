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
 */
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import * as schema from "./schema";

const globalForDb = globalThis as unknown as {
  _murmurShimDb?: PostgresJsDatabase<typeof schema>;
};

export const db = new Proxy({} as PostgresJsDatabase<typeof schema>, {
  get(_target, prop, receiver) {
    if (!globalForDb._murmurShimDb) {
      if (!process.env.DATABASE_URL) {
        throw new Error("DATABASE_URL is not set");
      }
      globalForDb._murmurShimDb = drizzle(
        postgres(process.env.DATABASE_URL, {
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
