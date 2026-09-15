import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { drizzle } from "drizzle-orm/postgres-js";
import { describe, expect, it } from "vitest";

import * as schema from "../schema";

describe("Better Auth runtime schema", () => {
  it("accepts the production Drizzle schema with the installed Better Auth version", async () => {
    const database = drizzle.mock({ schema });
    const testAuth = betterAuth({
      baseURL: "http://localhost:3000",
      secret: "test-secret-that-is-longer-than-thirty-two-characters",
      database: drizzleAdapter(database, { provider: "pg" }),
    });

    await expect(
      testAuth.api.getSession({ headers: new Headers() }),
    ).resolves.toBeNull();
  });
});
