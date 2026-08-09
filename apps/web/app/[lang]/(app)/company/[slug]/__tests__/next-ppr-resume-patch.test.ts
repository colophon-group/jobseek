import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const productionRuntime = readFileSync(
  join(
    process.cwd(),
    "node_modules/next/dist/compiled/next-server/app-page-turbo.runtime.prod.js",
  ),
  "utf8",
);

describe("Next.js PPR metadata resume patch (#5911)", () => {
  it("patches the compiled production runtime used by Turbopack", () => {
    const patchedChecks =
      productionRuntime.match(
        /="string"==typeof [et]\.renderOpts\.postponed\|\|!![et]\.renderOpts\.serveStreamingMetadata/g,
      ) ?? [];

    expect(patchedChecks).toHaveLength(3);
    expect(productionRuntime).not.toMatch(
      /[fE]=!![et]\.renderOpts\.serveStreamingMetadata/,
    );
  });
});
