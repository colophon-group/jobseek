import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

describe("SaveButton anonymous auth handoff", () => {
  it("includes the current path, filters, and open job in the sign-in URL", () => {
    const source = readFileSync("src/components/search/save-button.tsx", "utf8");

    expect(source).toContain("window.location.pathname");
    expect(source).toContain("window.location.search");
    expect(source).toContain("window.location.hash");
    expect(source).toContain("withAuthReturnPath");
  });
});
