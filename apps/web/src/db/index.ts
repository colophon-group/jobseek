import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import * as schema from "./schema";

const globalForDb = globalThis as unknown as {
  _db?: PostgresJsDatabase<typeof schema>;
};

// Better Auth 1.7.2 inspects Drizzle's relational metadata while its adapter is
// configured. Keep that inspection connection-free so importing auth from a
// static build or a unit test does not require DATABASE_URL. Query methods still
// initialize the real client below and fail closed when the URL is absent.
const dbMetadata = drizzle.mock({ schema })._;

export const db = new Proxy({} as PostgresJsDatabase<typeof schema>, {
  get(_target, prop, receiver) {
    if (prop === "_") {
      return globalForDb._db?._ ?? dbMetadata;
    }

    if (!globalForDb._db) {
      if (!process.env.DATABASE_URL) {
        throw new Error("DATABASE_URL is not set");
      }
      globalForDb._db = drizzle(
        postgres(process.env.DATABASE_URL, { max: 10, idle_timeout: 20, max_lifetime: 300, prepare: false }),
        { schema },
      );
    }
    return Reflect.get(globalForDb._db, prop, receiver);
  },
});
