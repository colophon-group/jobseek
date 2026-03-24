import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import * as schema from "./schema";

const globalForDb = globalThis as unknown as {
  _db?: PostgresJsDatabase<typeof schema>;
};

export const db = new Proxy({} as PostgresJsDatabase<typeof schema>, {
  get(_target, prop, receiver) {
    if (!globalForDb._db) {
      if (!process.env.DATABASE_URL) {
        throw new Error("DATABASE_URL is not set");
      }
      globalForDb._db = drizzle(
        postgres(process.env.DATABASE_URL, { max: 1, idle_timeout: 20, max_lifetime: 300, prepare: false }),
        { schema },
      );
    }
    return Reflect.get(globalForDb._db, prop, receiver);
  },
});
