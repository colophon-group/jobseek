import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const watchlistCopySources = [
  {
    path: "app/[lang]/(public)/about/about-content.tsx",
    shippedCopy:
      "Build a watchlist of the companies you care about and review their latest postings in one place.",
  },
  {
    path: "src/components/TruncationPrompt.tsx",
    shippedCopy:
      "Create a free account to browse all results, save jobs, track applications, and build watchlists.",
  },
  {
    path: "src/components/watchlist/watchlist-tip-banner.tsx",
    shippedCopy:
      "Mirror any public watchlist to make it your own — tweak companies or adjust filters.",
  },
  {
    path: "src/components/HowWeIndexContent.tsx",
    shippedCopy:
      "users can build watchlists and browse postings from those companies in one place",
  },
] as const;

function readCatalogMessage(locale: "de" | "fr" | "it", id: string) {
  const catalog = readFileSync(`locales/${locale}.po`, "utf8");
  const escapedId = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const entry = catalog.match(
    new RegExp(`msgid "${escapedId}"\\nmsgstr "([^"\\n]*)"`),
  );

  expect(entry, `${locale}.po should contain ${id}`).not.toBeNull();
  return entry?.[1] ?? "";
}

describe("public plan and watchlist availability claims", () => {
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

  it("keeps public watchlist copy limited to shipped functionality", () => {
    for (const { path, shippedCopy } of watchlistCopySources) {
      const source = readFileSync(path, "utf8");

      expect(source, path).toContain(shippedCopy);
      expect(source, path).not.toMatch(
        /\b(?:alerts?|notifications?|notified|notify)\b/i,
      );
    }
  });

  it("does not retain alert promises in translated public watchlist copy", () => {
    const messageIds = [
      "about.p2",
      "truncation.benefits",
      "watchlists.tip.mirror",
      "indexing.hero.description",
    ];
    const alertTerms = {
      de: /\b(?:Benachrichtigungen?|benachrichtigt)\b/i,
      fr: /\b(?:alertes?|notifications?)\b/i,
      it: /\b(?:avvis[oi]|notific(?:a|he))\b/i,
    } as const;

    for (const locale of ["de", "fr", "it"] as const) {
      for (const id of messageIds) {
        expect(readCatalogMessage(locale, id), `${locale}.po: ${id}`).not.toMatch(
          alertTerms[locale],
        );
      }
    }
  });
});
