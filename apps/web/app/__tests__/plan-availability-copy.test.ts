import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("public Pro-plan availability claims", () => {
  it("keeps AI discovery copy factual about the watchlist cap and Pro", () => {
    const llms = readFileSync("public/.well-known/llms.txt", "utf8");
    const plugin = JSON.parse(
      readFileSync("public/.well-known/ai-plugin.json", "utf8"),
    ) as { description_for_model: string };

    expect(llms).toContain("Free tier: full search, up to 10 watchlists");
    expect(llms).toContain("Pro tier: coming soon");
    expect(plugin.description_for_model).toContain(
      "Free tier includes full search, up to 10 watchlists",
    );
    expect(plugin.description_for_model).toContain("plan details have not been announced");
    expect(`${llms}\n${plugin.description_for_model}`).not.toMatch(
      /unlimited watchlists|email alerts/i,
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

    expect(faq).toContain("up to 10 watchlists");
    expect(faq).toContain(
      "Pro is coming soon; plan details will be announced before launch.",
    );
    expect(faq).not.toMatch(/unlimited watchlists|email alerts/i);
  });
});
