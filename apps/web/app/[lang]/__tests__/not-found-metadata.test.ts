import { describe, expect, it, vi } from "vitest";

const titles = {
  en: "Page not found",
  de: "Seite nicht gefunden",
  fr: "Page introuvable",
  it: "Pagina non trovata",
} as const;

vi.mock("@/lib/i18n", () => ({
  defaultLocale: "en",
  isLocale: (value: string) => value in titles,
  loadCatalog: async (locale: keyof typeof titles) => ({
    i18n: {
      _: ({ id, message }: { id: string; message: string }) => (
        id === "notFound.title" ? titles[locale] : message
      ),
    },
  }),
}));

import { generateMetadata } from "../not-found";

describe("[lang]/not-found metadata", () => {
  it.each([
    ["en", "Page not found"],
    ["de", "Seite nicht gefunden"],
    ["fr", "Page introuvable"],
    ["it", "Pagina non trovata"],
    ["bogus", "Page not found"],
  ])("localizes the 404 title for %s", async (lang, title) => {
    const metadata = await generateMetadata({
      params: Promise.resolve({ lang }),
    });

    expect(metadata.title).toBe(title);
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });
});
