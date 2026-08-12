import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import snapshot from "@/content/blog/mention-snapshot.json";
import {
  CompanyMention,
  WatchlistCard,
} from "@/components/blog/MdxMentions";
import {
  BLOG_MENTION_EXTERNAL_CALL_BUDGET,
  resolveBlogCompanyMention,
  resolveBlogWatchlistMention,
} from "@/lib/blog-mention-snapshot";
import { withTestEnv } from "@/test-utils/env";

const locales = ["en", "de", "fr", "it"] as const;
const SEARCH_KEY_CANARY = "blog-mention-search-key-must-not-appear";

withTestEnv({
  TYPESENSE_HOST: "production-typesense-canary.invalid",
  TYPESENSE_PORT: "443",
  TYPESENSE_PROTOCOL: "https",
  TYPESENSE_SEARCH_KEY: SEARCH_KEY_CANARY,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MDX mention snapshot", () => {
  it("resolves localized company fields with the English fallback", () => {
    const german = resolveBlogCompanyMention("anthropic", "de");
    expect(german).toMatchObject({
      name: "Anthropic",
      description:
        "KI-Sicherheitsunternehmen, das zuverlässige, interpretierbare und steuerbare Systeme künstlicher Intelligenz entwickelt.",
    });

    const fallback = resolveBlogCompanyMention("anthropic", "unsupported");
    expect(fallback?.description).toBe(
      "AI safety company building reliable, interpretable, and steerable artificial intelligence systems.",
    );

    render(CompanyMention({ slug: "anthropic", locale: "de" }));
    expect(screen.getByRole("link", { name: "Anthropic" }).getAttribute("href"))
      .toBe("/de/company/anthropic");
  });

  it("keeps unknown references author-visible", async () => {
    render(CompanyMention({ slug: "not-in-the-approved-snapshot", locale: "en" }));
    expect(screen.getByText("{Company not-in-the-approved-snapshot}")).toBeTruthy();

    const missingCard = await WatchlistCard({
      owner: "colophongroup",
      slug: "not-in-the-approved-snapshot",
      locale: "en",
    });
    render(missingCard);
    expect(
      screen.getByText("{WatchlistCard colophongroup/not-in-the-approved-snapshot}"),
    ).toBeTruthy();
  });

  it("uses one approved record per unique mention", () => {
    expect(new Set(snapshot.companies.map((company) => company.slug)).size)
      .toBe(snapshot.companies.length);
    expect(
      new Set(snapshot.watchlists.map((watchlist) => `${watchlist.owner}/${watchlist.slug}`)).size,
    ).toBe(snapshot.watchlists.length);
    expect(resolveBlogWatchlistMention("colophongroup", "maang"))
      .toMatchObject({ title: "MAANG", companyCount: 5 });
  });

  it("makes zero search-plane calls across the full four-locale company set", () => {
    const searchPlane503 = Object.assign(new Error("simulated Typesense 503"), {
      httpStatus: 503,
    });
    const searchPlane429 = Object.assign(new Error("simulated Typesense 429"), {
      httpStatus: 429,
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(searchPlane503)
      .mockRejectedValue(searchPlane429);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    const resolved = locales.flatMap((locale) =>
      snapshot.companies.map((company) =>
        resolveBlogCompanyMention(company.slug, locale),
      ),
    );

    expect(resolved).toHaveLength(locales.length * snapshot.companies.length);
    expect(resolved.every(Boolean)).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
    expect(JSON.stringify(resolved)).not.toContain(SEARCH_KEY_CANARY);
    expect(BLOG_MENTION_EXTERNAL_CALL_BUDGET).toBe(0);
  });
});
