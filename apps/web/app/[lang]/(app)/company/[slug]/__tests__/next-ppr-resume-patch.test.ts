import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const appRenderSource = readFileSync(
  join(
    process.cwd(),
    "node_modules/next/dist/server/app-render/app-render.js",
  ),
  "utf8",
);

describe("Next.js PPR metadata resume patch (#5911)", () => {
  it("keeps streaming metadata enabled while a postponed shell resumes", () => {
    expect(appRenderSource).toContain(
      "typeof renderOpts.postponed === 'string'",
    );
    expect(
      appRenderSource.match(
        /serveStreamingMetadata = getServeStreamingMetadata\(ctx\.renderOpts\)/g,
      ),
    ).toHaveLength(3);
  });
});
