import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

describe("responsive shared shells", () => {
  it("allows the auth shell to shrink below its desktop minimum", () => {
    const source = readFileSync("src/components/AuthShell.tsx", "utf8");
    const themedImageSource = readFileSync(
      "src/components/ThemedImage.tsx",
      "utf8",
    );

    expect(source).toContain("w-full max-w-lg px-4");
    expect(source).toContain("sm:w-fit sm:min-w-[24rem]");
    expect(source).toContain('loading="eager"');
    expect(source).toContain('fetchPriority="high"');
    expect(themedImageSource).toContain("loading={loading}");
    expect(themedImageSource).toContain("fetchPriority={fetchPriority}");
    expect(source).not.toContain("w-fit min-w-[24rem] max-w-lg");
  });

  it("wraps public footer links on narrow viewports", () => {
    const source = readFileSync("src/components/Footer.tsx", "utf8");

    expect(source).toContain(
      "flex flex-wrap list-none gap-x-4 gap-y-2 p-0",
    );
    expect(source).not.toContain('className="flex list-none gap-4 p-0"');
  });
});
