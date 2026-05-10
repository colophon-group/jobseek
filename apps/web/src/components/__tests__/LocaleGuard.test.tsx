import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

// ── Mocks ────────────────────────────────────────────────────────────
//
// `LocaleGuard` is a tiny client-only component: it reads the
// `NEXT_LOCALE` cookie via `document.cookie` and `router.replace`s any
// URL whose `[lang]` segment disagrees. The tests pin down the
// regression in #2988: after the user changes their UI language in
// /settings, *every* subsequent in-app navigation must land on a path
// prefixed with the new locale — including back-button traversal of
// pre-switch history entries.

const replaceMock = vi.fn();
let currentPathname = "/en/explore";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: replaceMock,
    push: vi.fn(),
    refresh: vi.fn(),
  }),
  usePathname: () => currentPathname,
}));

// Stub `document.cookie` per test. happy-dom supports cookie writes,
// but we want deterministic state across the suite.
let cookieValue = "";
function setCookie(value: string) {
  cookieValue = value;
}

beforeEach(() => {
  replaceMock.mockReset();
  cookieValue = "";
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: () => cookieValue,
    set: (v: string) => {
      cookieValue = v;
    },
  });
  // `window.location.search` defaults to "" in happy-dom; tests that
  // exercise query-string preservation override it explicitly.
});

afterEach(() => {
  vi.resetModules();
});

import { LocaleGuard } from "../LocaleGuard";

describe("LocaleGuard (#2988)", () => {
  it("redirects /en/explore -> /de/explore when NEXT_LOCALE=de", () => {
    setCookie("NEXT_LOCALE=de");
    currentPathname = "/en/explore";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/de/explore");
  });

  it("preserves query string across the redirect", () => {
    setCookie("NEXT_LOCALE=de");
    currentPathname = "/en/explore";
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, search: "?q=engineer&loc=zurich" },
    });

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith(
      "/de/explore?q=engineer&loc=zurich",
    );

    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, search: "" },
    });
  });

  it("does nothing when URL [lang] already matches the cookie", () => {
    setCookie("NEXT_LOCALE=de");
    currentPathname = "/de/explore";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does nothing when the cookie is absent", () => {
    setCookie("");
    currentPathname = "/en/explore";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("ignores an unsupported cookie value", () => {
    setCookie("NEXT_LOCALE=xx");
    currentPathname = "/en/explore";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("ignores a path whose first segment is not a known locale", () => {
    // e.g. a /sitemap.xml-shaped fallback that somehow reaches the
    // [lang] layout — never happens in production thanks to
    // `notFound()` in the layout, but the guard must not redirect.
    setCookie("NEXT_LOCALE=de");
    currentPathname = "/api/health";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects nested paths, not just /<lang>/<page>", () => {
    setCookie("NEXT_LOCALE=fr");
    currentPathname = "/en/company/stripe";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith("/fr/company/stripe");
  });

  it("decodes URL-encoded cookie values", () => {
    setCookie("NEXT_LOCALE=de; theme=dark");
    currentPathname = "/en/explore";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith("/de/explore");
  });

  it("handles multi-cookie strings where NEXT_LOCALE is not first", () => {
    setCookie("logged_in=1; theme=dark; NEXT_LOCALE=it");
    currentPathname = "/en/settings";

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith("/it/settings");
  });
});
