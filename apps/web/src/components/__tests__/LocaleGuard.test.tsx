import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

// `LocaleGuard` is a tiny client-only component that reads the
// `NEXT_LOCALE` cookie and redirects (`window.location.replace`) any
// URL whose `[lang]` segment disagrees. It deliberately uses
// browser-native APIs only — under cacheComponents (#2835),
// `useRouter()`/`usePathname()` would taint the parent layout's
// static-rendering classification.
//
// Tests pin the regression in #2988 (locale switch in /settings does
// not propagate to subsequent navigations) AND the build-fix in
// #3001 (no Next.js navigation hooks).

const replaceMock = vi.fn();
let cookieValue = "";

function setCookie(value: string) {
  cookieValue = value;
}

function setLocation(pathname: string, search = "") {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      ...window.location,
      pathname,
      search,
      replace: replaceMock,
    },
  });
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
});

afterEach(() => {
  vi.resetModules();
});

import { LocaleGuard } from "../LocaleGuard";

describe("LocaleGuard (#2988, #3001)", () => {
  it("redirects /en/explore -> /de/explore when NEXT_LOCALE=de", () => {
    setCookie("NEXT_LOCALE=de");
    setLocation("/en/explore");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/de/explore");
  });

  it("preserves query string across the redirect", () => {
    setCookie("NEXT_LOCALE=de");
    setLocation("/en/explore", "?q=engineer&loc=zurich");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith(
      "/de/explore?q=engineer&loc=zurich",
    );
  });

  it("does nothing when URL [lang] already matches the cookie", () => {
    setCookie("NEXT_LOCALE=de");
    setLocation("/de/explore");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does nothing when the cookie is absent", () => {
    setCookie("");
    setLocation("/en/explore");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("ignores an unsupported cookie value", () => {
    setCookie("NEXT_LOCALE=xx");
    setLocation("/en/explore");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("ignores a path whose first segment is not a known locale", () => {
    setCookie("NEXT_LOCALE=de");
    setLocation("/api/health");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects nested paths, not just /<lang>/<page>", () => {
    setCookie("NEXT_LOCALE=fr");
    setLocation("/en/company/stripe");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith("/fr/company/stripe");
  });

  it("decodes URL-encoded cookie values", () => {
    setCookie("NEXT_LOCALE=de; theme=dark");
    setLocation("/en/explore");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith("/de/explore");
  });

  it("handles multi-cookie strings where NEXT_LOCALE is not first", () => {
    setCookie("logged_in=1; theme=dark; NEXT_LOCALE=it");
    setLocation("/en/settings");

    act(() => {
      render(<LocaleGuard />);
    });

    expect(replaceMock).toHaveBeenCalledWith("/it/settings");
  });

  it("re-fires on browser back/forward (popstate)", () => {
    // Initial mount: URL matches cookie, no redirect.
    setCookie("NEXT_LOCALE=de");
    setLocation("/de/explore");

    act(() => {
      render(<LocaleGuard />);
    });
    expect(replaceMock).not.toHaveBeenCalled();

    // User clicks back to a stale /en/explore history entry. The
    // browser updates location and dispatches popstate.
    setLocation("/en/explore");
    act(() => {
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/de/explore");
  });
});
