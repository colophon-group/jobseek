import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

const globalForDb = globalThis as unknown as { _db?: unknown };
let originalDb: unknown;

describe("lazy database initialization", () => {
  withTestEnv({ DATABASE_URL: undefined });

  beforeEach(() => {
    originalDb = globalForDb._db;
    delete globalForDb._db;
    vi.resetModules();
  });

  afterEach(() => {
    if (originalDb === undefined) {
      delete globalForDb._db;
    } else {
      globalForDb._db = originalDb;
    }
    vi.resetModules();
  });

  it("exposes Drizzle metadata without creating a database client", async () => {
    const { db } = await import("@/db");

    expect(db._.fullSchema).toBeDefined();
    expect(globalForDb._db).toBeUndefined();
  });

  it("still rejects query access when DATABASE_URL is absent", async () => {
    const { db } = await import("@/db");

    expect(() => db.select()).toThrow("DATABASE_URL is not set");
    expect(globalForDb._db).toBeUndefined();
  });
});
