import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("public Pro-plan availability claims", () => {
  it("marks Pro as coming soon in AI discovery files", () => {
    const llms = readFileSync("public/.well-known/llms.txt", "utf8");
    const plugin = JSON.parse(
      readFileSync("public/.well-known/ai-plugin.json", "utf8"),
    ) as { description_for_model: string };

    expect(llms).toContain("Pro tier (coming soon;");
    expect(plugin.description_for_model).toContain(
      "planned Pro tier (coming soon",
    );
  });

  it("marks the structured Pro offer as unavailable until launch", () => {
    const layout = readFileSync("app/[lang]/layout.tsx", "utf8");

    expect(layout).toMatch(
      /name: "Pro",[\s\S]*?availability: "https:\/\/schema\.org\/OutOfStock"/,
    );
  });

  it("tells human readers that Pro is coming soon", () => {
    const faq = readFileSync(
      "app/[lang]/(public)/faq/page.tsx",
      "utf8",
    );

    expect(faq).toContain(
      "Pro is coming soon and will add unlimited watchlists",
    );
  });
});
