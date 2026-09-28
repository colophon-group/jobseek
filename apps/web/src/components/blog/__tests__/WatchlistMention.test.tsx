import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WatchlistMention, buildMdxComponents } from "@/components/blog/MdxMentions";
import { resolveBlogWatchlistMention } from "@/lib/blog-mention-snapshot";

vi.mock("@/content/blog/watchlist-mention-snapshot.json", () => ({
  default: {
    schemaVersion: 1,
    watchlists: [{
      slug: "swiss-robotics",
      name: "Swiss robotics employers",
      path: "/watchlists/12345678-1234-4234-8234-123456789abc",
    }],
  },
}));

afterEach(() => vi.restoreAllMocks());

describe("inline MDX watchlist mentions", () => {
  it.each(["en", "de", "fr", "it"])("uses the canonical shared route in %s", (locale) => {
    const { Watchlist } = buildMdxComponents(locale);
    render(<Watchlist slug="swiss-robotics" />);
    expect(screen.getByRole("link", { name: "Swiss robotics employers" }).getAttribute("href"))
      .toBe(`/${locale}/watchlists/12345678-1234-4234-8234-123456789abc`);
  });

  it("normalizes unsupported locales without changing the reviewed watchlist name", () => {
    render(<WatchlistMention slug="swiss-robotics" locale="unsupported" />);
    expect(screen.getByRole("link", { name: "Swiss robotics employers" }).getAttribute("href"))
      .toBe("/en/watchlists/12345678-1234-4234-8234-123456789abc");
  });

  it("leaves unknown references visible to authors without creating a broken link", () => {
    render(<WatchlistMention slug="unknown-watchlist" locale="en" />);
    expect(screen.getByText("{Watchlist unknown-watchlist}")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(resolveBlogWatchlistMention("unknown-watchlist", "en")).toBeNull();
  });

  it("renders every locale from reviewed static fields without fetching or live counts", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("external calls forbidden"));
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(resolveBlogWatchlistMention("swiss-robotics", locale)).toEqual({
        slug: "swiss-robotics",
        name: "Swiss robotics employers",
        href: `/${locale}/watchlists/12345678-1234-4234-8234-123456789abc`,
      });
      const view = render(<WatchlistMention slug="swiss-robotics" locale={locale} />);
      expect(view.container.textContent).toBe("Swiss robotics employers");
      view.unmount();
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
