import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const dockerfilePath = path.resolve(__dirname, "../../Dockerfile");

describe("murmur-shim Dockerfile: patched dependency inputs", () => {
  it("copies the root patches directory before the filtered pnpm install", () => {
    const dockerfile = readFileSync(dockerfilePath, "utf8");
    const copyPatches = dockerfile.indexOf("COPY patches/ ./patches/");
    const shimFilter = dockerfile.indexOf("--filter @jobseek/murmur-shim...");
    const frozenInstall = dockerfile.indexOf(
      "install --frozen-lockfile",
      shimFilter,
    );

    expect(copyPatches).toBeGreaterThanOrEqual(0);
    expect(shimFilter).toBeGreaterThan(copyPatches);
    expect(frozenInstall).toBeGreaterThan(shimFilter);
  });

  it("copies the web schema's local notification contract before building", () => {
    const dockerfile = readFileSync(dockerfilePath, "utf8");
    const copySchema = dockerfile.indexOf(
      "COPY apps/web/src/db/schema.ts ./apps/web/src/db/schema.ts",
    );
    const copyNotificationContracts = dockerfile.indexOf(
      "COPY apps/web/src/lib/notifications/contracts.ts ./apps/web/src/lib/notifications/contracts.ts",
    );
    const build = dockerfile.indexOf(
      "RUN pnpm --filter @jobseek/murmur-shim build",
      copyNotificationContracts,
    );

    expect(copySchema).toBeGreaterThanOrEqual(0);
    expect(copyNotificationContracts).toBeGreaterThan(copySchema);
    expect(build).toBeGreaterThan(copyNotificationContracts);
  });
});
