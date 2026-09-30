import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("public Pro-plan availability claims", () => {
  it("describes the launched Pro plan and points to current signup availability", () => {
    const llms = readFileSync("public/.well-known/llms.txt", "utf8");
    const plugin = JSON.parse(
      readFileSync("public/.well-known/ai-plugin.json", "utf8"),
    ) as { description_for_model: string };

    for (const text of [llms, plugin.description_for_model]) {
      expect(text).not.toMatch(/coming soon|planned Pro|at launch/);
      expect(text).toContain("Narrowed results for watchlists");
      expect(text).toContain("seven-day free trial requiring a payment method");
      expect(text).toContain("/en/settings/billing");
    }
  });

  it("uses the public launch gate for pricing and structured availability", () => {
    const layout = readFileSync("app/[lang]/layout.tsx", "utf8");

    expect(layout).toMatch(
      /name: "Pro",[\s\S]*?availability: stripeSignupOpen\(\)[\s\S]*?schema\.org\/InStock[\s\S]*?schema\.org\/OutOfStock/,
    );
    for (const path of ["app/[lang]/(public)/page.tsx", "app/[lang]/(public)/narrowed/page.tsx"]) {
      expect(readFileSync(path, "utf8")).toContain("checkoutEnabled={stripeSignupOpen()}");
    }
  });

  it("describes Narrowed as the paid benefit", () => {
    const faq = readFileSync(
      "app/[lang]/(public)/faq/page.tsx",
      "utf8",
    );

    expect(faq).toContain(
      "Pro adds Narrowed results.",
    );
  });
});
