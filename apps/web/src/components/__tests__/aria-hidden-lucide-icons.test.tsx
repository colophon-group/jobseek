import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

// `server-only` is a marker import that throws when loaded outside an
// RSC context. Several transitive deps pull it in; stub here so the
// component tree under test renders.
vi.mock("server-only", () => ({}));

// Stub the full Lingui surface used by Header/MobileMenu/search-toolbar
// so the components render without an active I18nProvider.
import "@/test-utils/lingui-mock";

// `next/link` pulls in router internals; stub to a plain anchor so the
// test renderer doesn't try to wire App Router hooks.
vi.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, onClick, children, prefetch: _prefetch, ...rest }: {
    href: string;
    onClick?: (e: React.MouseEvent) => void;
    children: React.ReactNode;
    prefetch?: boolean;
  }) => (
    <a href={href} onClick={onClick} {...rest}>{children}</a>
  ),
}));

// `usePathname` is used by Header and MobileMenu for the active-link
// computation. Stub to a fixed value so the components render.
vi.mock("next/navigation", () => ({
  usePathname: () => "/en",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ lang: "en" }),
}));

// SessionProvider is consumed indirectly via the Avatar in AppHeader,
// but Header.tsx and MobileMenu.tsx don't reach for session. The
// component itself doesn't import next-themes either. We still need
// to stub `useLocalePath` so the Link `href` resolves.
vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (p: string) => p,
}));

// Header reads from siteConfig — small, deterministic JSON; pulling the
// real one is fine. ThemedImage doesn't require any provider.
vi.mock("@/components/ThemeToggleButton", () => ({
  ThemeToggleButton: () => null,
}));
vi.mock("@/components/LocaleSwitcher", () => ({
  LocaleSwitcher: () => null,
}));
vi.mock("@/components/ThemedImage", () => ({
  ThemedImage: ({ alt }: { alt: string }) => <span>{alt}</span>,
}));

import { Header } from "../Header";
import { MobileMenu } from "../MobileMenu";
import { SearchToolbar } from "../search/search-toolbar";

describe("Lucide icon aria-hidden (regression for #3177)", () => {
  it("Header hamburger icon is aria-hidden", () => {
    const { container } = render(
      <Header onOpenMobileAction={() => {}} />,
    );
    // The hamburger button's inner SVG should have aria-hidden="true"
    // so screen readers announce only the button's aria-label.
    const btn = container.querySelector('button[aria-label]');
    expect(btn).not.toBeNull();
    const svg = btn?.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
  });

  it("MobileMenu close icon is aria-hidden when open", () => {
    const { container } = render(
      <MobileMenu open={true} onCloseAction={() => {}} />,
    );
    // The Radix Dialog portal renders into document.body, so query off
    // the global root rather than the container.
    const closeBtn = document.querySelector(
      'button[aria-label*="Close" i]',
    );
    expect(closeBtn).not.toBeNull();
    const svg = closeBtn?.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    // Cleanup the portaled content to avoid leaking between tests.
    container.remove();
  });

  it("search-toolbar pill X icons are aria-hidden", () => {
    const { container } = render(
      <SearchToolbar
        locale="en"
        keywords={["typescript"]}
        locations={[]}
        occupations={[
          { id: 1, slug: "engineering", name: "Engineering" },
        ]}
        seniorities={[]}
        jobLanguages={[]}
        onRemoveKeyword={() => {}}
        onAddLocation={() => {}}
        onRemoveLocation={() => {}}
        onAddOccupation={() => {}}
        onRemoveOccupation={() => {}}
        onAddSeniority={() => {}}
        onRemoveSeniority={() => {}}
        onClearAll={() => {}}
        onSubmitSearch={() => {}}
      />,
    );

    // Every remove-filter pill button now has aria-label, so collect
    // them and assert their child SVG is aria-hidden.
    const removeButtons = container.querySelectorAll(
      'button[aria-label^="Remove"]',
    );
    // We rendered one occupation pill + one keyword pill -> at least 2
    // remove buttons live in the DOM.
    expect(removeButtons.length).toBeGreaterThanOrEqual(2);

    for (const btn of Array.from(removeButtons)) {
      const svg = btn.querySelector("svg");
      expect(svg).not.toBeNull();
      expect(svg?.getAttribute("aria-hidden")).toBe("true");
    }
  });
});
