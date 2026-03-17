import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import * as schema from "./schema";

let _db: PostgresJsDatabase<typeof schema> | undefined;

export const db = new Proxy({} as PostgresJsDatabase<typeof schema>, {
  get(_target, prop, receiver) {
    if (!_db) {
      if (!process.env.DATABASE_URL) {
        throw new Error("DATABASE_URL is not set");
      }
      _db = drizzle(postgres(process.env.DATABASE_URL), { schema });
    }
    return Reflect.get(_db, prop, receiver);
  },
});
